# SPDX-License-Identifier: GPL-3.0-or-later
"""Core command processing for scheduled gateway operations."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.dbus_adapter.write.protocols import (
    DbusWriteSchedulerAdapter,
    PublicationExecutor,
    SemanticOperationExecutor,
    WriteSchedulerHealth,
)
from venus_evcharger.dbus_adapter.write.support import (
    budget_elapsed,
    command_deadline_expired,
    command_kind,
    command_ready,
    is_local_publish_command,
    is_urgent_durable_command,
    local_publish_action_result,
    stale_coalesced_paths,
)
from venus_evcharger.ipc.command_types import CommandFile, CommandFileList, CommandMapping
from venus_evcharger.ipc.fast_publication import FastPublicationWork
from venus_evcharger.ipc.gateway_operations import SEMANTIC_GATEWAY_KINDS
from venus_evcharger.ipc.gateway_publication import SEMANTIC_PUBLICATION_KINDS
from venus_evcharger.ipc.generic_shelly_configuration import DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND
from venus_evcharger.ipc.pending_snapshot import (
    PendingCommandSnapshot,
    TickPendingSnapshotProvider,
)
from venus_evcharger.ipc.publication_order import PublicationOrderDeferredError

GATEWAY_COMMAND_RETRY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class _LocalPublishCandidate:
    processed: int
    remaining_budget: int
    pending_commands: CommandFileList
    started: float


@dataclass(frozen=True, slots=True)
class _FastPublishBurst:
    processed: int
    stopped: bool


class WriteCommandQueue:
    """Select, dispatch, and retire queued gateway commands."""

    def __init__(
        self,
        adapter: DbusWriteSchedulerAdapter,
        *,
        publication: PublicationExecutor,
        semantic: SemanticOperationExecutor,
        health: WriteSchedulerHealth,
    ) -> None:
        self.adapter = adapter
        self.publication = publication
        self.semantic = semantic
        self.health = health
        self._pending = TickPendingSnapshotProvider(adapter.commands)
        self.last_scheduled_outcome: CommandOutcome | None = None

    def begin_tick(self) -> PendingCommandSnapshot:
        return self._pending.begin_tick()

    def end_tick(self) -> None:
        self._pending.end_tick()

    def pending_snapshot(self) -> PendingCommandSnapshot:
        return self._pending.snapshot()

    def remove_pending(self, path: str, expected: CommandMapping) -> bool:
        return self._pending.remove(path, expected)

    def process_local_publish_burst(self, limit: int | None = None) -> int:
        remaining = self.health.dynamic_local_publish_burst_limit if limit is None else int(limit)
        started = time.monotonic()
        snapshot = self.pending_snapshot()
        pending_commands = snapshot.physical_list()
        pending = self.health.prioritized_commands(snapshot.effective_list())
        if self._urgent_durable_ready(pending):
            return 0
        fast = self._process_fast_publish_burst(max(0, remaining), started)
        if fast.stopped:
            return fast.processed
        return self._process_durable_publish_burst(
            pending,
            _LocalPublishCandidate(fast.processed, remaining, pending_commands, started),
        )

    def _process_durable_publish_burst(
        self,
        pending: CommandFileList,
        candidate: _LocalPublishCandidate,
    ) -> int:
        processed = candidate.processed
        for path, command in pending:
            action = self._process_local_publish_candidate(
                path,
                command,
                _LocalPublishCandidate(
                    processed,
                    candidate.remaining_budget,
                    candidate.pending_commands,
                    candidate.started,
                ),
            )
            processed, stop = local_publish_action_result(processed, action)
            if stop:
                break
        return processed

    def _urgent_durable_ready(self, commands: CommandFileList) -> bool:
        now = time.time()
        self.health.prune_budget(now)
        return any(
            is_urgent_durable_command(command)
            and command_ready(command, now)
            and self.health.budget_available(command, now)
            for _path, command in commands
        )

    def _process_fast_publish_burst(self, limit: int, started: float) -> _FastPublishBurst:
        processed = 0
        while processed < limit:
            work = self.adapter.fast_publications.pop_next(now=started)
            if work is None:
                return _FastPublishBurst(processed, False)
            if not self._process_fast_publish_candidate(work, started):
                return _FastPublishBurst(processed, True)
            processed += 1
        return _FastPublishBurst(processed, True)

    def _process_fast_publish_candidate(self, work: FastPublicationWork, started: float) -> bool:
        command = work.command
        if self._fast_publish_blocked(command, started):
            self.adapter.fast_publications.requeue(work, now=started)
            return False
        outcome = self.command_outcome("", command)
        sample = self.adapter.fast_publications.record_outcome(work, outcome)
        if outcome == "deferred":
            self.adapter.fast_publications.requeue(work, deferred=True, now=started)
        if sample:
            self.health.record_lifecycle(command, outcome)
        self.health.record_budget(command)
        if outcome == "deferred":
            return True
        self.health.record_processed()
        return True

    def _fast_publish_blocked(self, command: CommandMapping, started: float) -> bool:
        elapsed = budget_elapsed(started, self.health.local_publish_tick_budget_seconds)
        return elapsed or not self.health.budget_available(command, time.time())

    def _process_local_publish_candidate(
        self,
        path: str,
        command: CommandMapping,
        candidate: _LocalPublishCandidate,
    ) -> str:
        if self._local_publish_burst_done(candidate.processed, candidate.remaining_budget, candidate.started):
            return "break"
        if not is_local_publish_command(command) or not self.health.budget_available(command, time.time()):
            return "skip"
        outcome = self.process_loaded_command(path, command, pending_commands=candidate.pending_commands)
        return "processed" if outcome in ("applied", "dropped") else "break"

    def _local_publish_burst_done(self, processed: int, remaining: int, started: float) -> bool:
        return processed >= max(0, remaining) or budget_elapsed(
            started,
            self.health.local_publish_tick_budget_seconds,
        )

    def next_local_publish_command(self) -> CommandFile | None:
        now = time.time()
        self.health.prune_budget(now)
        pending = self.health.prioritized_commands(self.pending_snapshot().effective_list())
        for path, command in pending:
            if is_local_publish_command(command) and self.health.budget_available(command, now):
                return path, command
        return None

    def drop_stale_coalesced_commands(
        self,
        processed_path: str,
        processed_command: CommandMapping,
        *,
        pending_commands: CommandFileList | None = None,
    ) -> None:
        key = str(processed_command.get("coalesce_key") or "")
        if not key:
            return
        commands = (
            self.pending_snapshot().physical_list()
            if pending_commands is None
            else pending_commands
        )
        command_by_path = dict(commands)
        for stale_path in stale_coalesced_paths(
            commands,
            processed_path=processed_path,
            key=key,
        ):
            self.remove_pending(stale_path, command_by_path[stale_path])

    def process_one(
        self,
        *,
        include_local_publish: bool = True,
        required_kind: str | None = None,
    ) -> bool:
        self.last_scheduled_outcome = None
        snapshot = self.pending_snapshot()
        pending = snapshot.physical_list()
        commands = self.health.prioritized_commands(snapshot.effective_list())
        selected = self.select_next_command(
            commands,
            include_local_publish=include_local_publish,
            required_kind=required_kind,
        )
        if selected is None:
            return False
        path, command = selected
        self.last_scheduled_outcome = self.process_loaded_command(path, command, pending_commands=pending)
        return True

    def process_urgent_once(self) -> bool:
        """Process one ready safety or user command before ordinary scheduled work."""
        self.last_scheduled_outcome = None
        snapshot = self.pending_snapshot()
        pending = snapshot.physical_list()
        commands = self.health.prioritized_commands(snapshot.effective_list())
        now = time.time()
        self.health.prune_budget(now)
        selected = next(
            (
                (path, command)
                for path, command in commands
                if is_urgent_durable_command(command)
                and self._command_selectable(
                    command,
                    include_local_publish=True,
                    required_kind=None,
                    now=now,
                )
            ),
            None,
        )
        if selected is None:
            return False
        path, command = selected
        self.last_scheduled_outcome = self.process_loaded_command(path, command, pending_commands=pending)
        return True

    def select_next_command(
        self,
        commands: CommandFileList,
        *,
        include_local_publish: bool = True,
        required_kind: str | None = None,
    ) -> CommandFile | None:
        now = time.time()
        self.health.prune_budget(now)
        for path, command in commands:
            if self._command_selectable(
                command,
                include_local_publish=include_local_publish,
                required_kind=required_kind,
                now=now,
            ):
                return path, command
        return None

    def _command_selectable(
        self,
        command: CommandMapping,
        *,
        include_local_publish: bool,
        required_kind: str | None,
        now: float,
    ) -> bool:
        return (
            command_ready(command, now)
            and self._command_matches_filters(
                command,
                include_local_publish=include_local_publish,
                required_kind=required_kind,
            )
            and self.health.budget_available(command, now)
        )

    @classmethod
    def _command_matches_filters(
        cls,
        command: CommandMapping,
        *,
        include_local_publish: bool,
        required_kind: str | None,
    ) -> bool:
        publish_allowed = include_local_publish or not is_local_publish_command(command)
        kind_allowed = required_kind is None or command_kind(command) == required_kind
        return publish_allowed and kind_allowed

    def process_command(self, command: CommandMapping, *, command_file: str = "") -> CommandOutcome:
        if self._command_blocked(command):
            return "deferred"
        return self._dispatch_command(command, command_file=command_file)

    def _command_blocked(self, command: CommandMapping) -> bool:
        priority = str(command.get("priority") or "diagnostic")
        return not self.adapter.circuit.allows_priority(priority)

    def _dispatch_command(self, command: CommandMapping, *, command_file: str) -> CommandOutcome:
        kind = command_kind(command)
        if kind in SEMANTIC_PUBLICATION_KINDS:
            return self.publication.process(command)
        if self._is_semantic_operation(kind):
            return self.semantic.process_semantic_operation(command, command_file=command_file)
        return self.adapter.process_non_write_command(command)

    @staticmethod
    def _is_semantic_operation(kind: str) -> bool:
        return kind in SEMANTIC_GATEWAY_KINDS or kind == DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND

    def process_loaded_command(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None = None,
    ) -> CommandOutcome:
        if self.command_expired(command):
            return self._drop_expired_command(path, command, pending_commands=pending_commands)
        try:
            effective_command = self._effective_durable_command(command)
        except PublicationOrderDeferredError:
            self.health.record_budget(command)
            self.health.record_lifecycle(command, "deferred")
            return "deferred"
        if effective_command is None:
            return self._drop_superseded_publication(
                path,
                command,
                pending_commands=pending_commands,
            )
        outcome = self.command_outcome(path, effective_command)
        if is_local_publish_command(effective_command):
            self.adapter.fast_publications.record_durable_outcome(
                effective_command,
                outcome,
            )
        self._apply_command_result(path, effective_command, outcome, pending_commands=pending_commands)
        self.health.record_lifecycle(effective_command, outcome)
        return outcome

    def _effective_durable_command(self, command: CommandMapping) -> CommandMapping | None:
        if not is_local_publish_command(command):
            return command
        return self.adapter.fast_publications.prepare_durable(command)

    def _drop_superseded_publication(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None,
    ) -> CommandOutcome:
        self.remove_pending(path, command)
        self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
        self.health.record_lifecycle(command, "coalesced")
        self.health.record_processed()
        self.health.record_budget(command)
        return "dropped"

    def _drop_expired_command(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None,
    ) -> CommandOutcome:
        self.remove_pending(path, command)
        self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
        self.health.record_lifecycle(command, "expired")
        self.health.record_processed()
        return "dropped"

    def command_outcome(self, path: str, command: CommandMapping) -> CommandOutcome:
        try:
            return self.process_command(command, command_file=path)
        except DbusOperationDeferred:
            return "deferred"
        except GATEWAY_COMMAND_RETRY_ERRORS as error:
            logging.exception("Gateway command failed; keeping for retry path=%s: %s", path, error)
            return "deferred"

    def _apply_command_result(
        self,
        path: str,
        command: CommandMapping,
        outcome: CommandOutcome,
        *,
        pending_commands: CommandFileList | None,
    ) -> None:
        if outcome == "deferred":
            self.health.record_budget(command)
        if outcome in ("applied", "dropped"):
            self.remove_pending(path, command)
            self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
            self.health.record_processed()
            self.health.record_budget(command)

    @staticmethod
    def command_expired(command: CommandMapping) -> bool:
        return command_deadline_expired(command, time.time())
