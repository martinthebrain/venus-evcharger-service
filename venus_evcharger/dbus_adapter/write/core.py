# SPDX-License-Identifier: GPL-3.0-or-later
"""Core command processing for scheduled gateway operations."""

from __future__ import annotations

import time
from enum import Enum, auto

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.write.dispatch import WriteCommandDispatcher
from venus_evcharger.dbus_adapter.write.protocols import (
    DbusWriteSchedulerAdapter,
    WriteSchedulerHealth,
)
from venus_evcharger.dbus_adapter.write.support import (
    FastPublishBurst,
    LocalPublishCandidate,
    budget_elapsed,
    command_deadline_expired,
    command_matches_filters,
    command_ready,
    is_local_publish_command,
    is_urgent_durable_command,
    local_publish_action_result,
    stale_coalesced_paths,
)
from venus_evcharger.ipc.command_types import CommandFile, CommandFileList, CommandMapping
from venus_evcharger.ipc.fast_publication import FastPublicationWork
from venus_evcharger.ipc.pending_snapshot import (
    PendingCommandSnapshot,
    TickPendingSnapshotProvider,
)
from venus_evcharger.ipc.publication_order import PublicationOrderDeferredError


class _CompletionPhase(Enum):
    DISPATCHING = auto()
    WAITING = auto()
    CLOSED = auto()


class _CompletionAction(Enum):
    BUFFER = auto()
    FINALIZE = auto()
    IGNORE = auto()


def _completion_action(phase: _CompletionPhase) -> _CompletionAction:
    if phase is _CompletionPhase.DISPATCHING:
        return _CompletionAction.BUFFER
    if phase is _CompletionPhase.WAITING:
        return _CompletionAction.FINALIZE
    if phase is _CompletionPhase.CLOSED:
        return _CompletionAction.IGNORE
    raise RuntimeError


class WriteCommandQueue:
    """Select and retire queued gateway commands around one dispatcher."""

    def __init__(
        self,
        adapter: DbusWriteSchedulerAdapter,
        *,
        dispatcher: WriteCommandDispatcher,
        health: WriteSchedulerHealth,
    ) -> None:
        self.adapter = adapter
        self.dispatcher = dispatcher
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
            LocalPublishCandidate(fast.processed, remaining, pending_commands, started),
        )

    def _process_durable_publish_burst(
        self,
        pending: CommandFileList,
        candidate: LocalPublishCandidate,
    ) -> int:
        processed = candidate.processed
        for path, command in pending:
            action = self._process_local_publish_candidate(
                path,
                command,
                LocalPublishCandidate(
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

    def _process_fast_publish_burst(self, limit: int, started: float) -> FastPublishBurst:
        processed = 0
        while processed < limit:
            work = self.adapter.fast_publications.pop_next(now=started)
            if work is None:
                return FastPublishBurst(processed, False)
            if not self._process_fast_publish_candidate(work, started):
                return FastPublishBurst(processed, True)
            processed += 1
        return FastPublishBurst(processed, True)

    def _process_fast_publish_candidate(self, work: FastPublicationWork, started: float) -> bool:
        command = work.command
        if self._fast_publish_blocked(command, started):
            self.adapter.fast_publications.requeue(work, now=started)
            return False
        outcome = self.dispatcher.outcome("", command)
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
        candidate: LocalPublishCandidate,
    ) -> str:
        if self._local_publish_burst_done(candidate.processed, candidate.remaining_budget, candidate.started):
            return "break"
        if not is_local_publish_command(command) or not self.health.budget_available(command, time.time()):
            return "skip"
        outcome = self.process_loaded_command(path, command, pending_commands=candidate.pending_commands)
        return "processed" if outcome in ("applied", "dropped") else "break"

    def _local_publish_burst_done(self, processed: int, remaining: int, started: float) -> bool:
        return processed >= max(0, remaining) or budget_elapsed(started, self.health.local_publish_tick_budget_seconds)

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
        commands = self.pending_snapshot().physical_list() if pending_commands is None else pending_commands
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
            and command_matches_filters(
                command,
                include_local_publish=include_local_publish,
                required_kind=required_kind,
            )
            and self.health.budget_available(command, now)
        )

    def process_loaded_command(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None = None,
    ) -> CommandOutcome:
        prepared = self._prepare_loaded_command(
            path,
            command,
            pending_commands=pending_commands,
        )
        if isinstance(prepared, str):
            return prepared
        return self._dispatch_loaded_command(
            path,
            prepared,
            pending_commands=pending_commands,
        )

    def _prepare_loaded_command(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None,
    ) -> CommandMapping | CommandOutcome:
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
        return effective_command

    def _dispatch_loaded_command(
        self,
        path: str,
        effective_command: CommandMapping,
        *,
        pending_commands: CommandFileList | None,
    ) -> CommandOutcome:
        completed_during_dispatch: list[CommandOutcome] = []
        completion_phase = _CompletionPhase.DISPATCHING

        def _complete(outcome: CommandOutcome) -> None:
            nonlocal completion_phase
            action = _completion_action(completion_phase)
            if action is _CompletionAction.IGNORE:
                return
            completion_phase = _CompletionPhase.CLOSED
            if action is _CompletionAction.BUFFER:
                completed_during_dispatch.append(outcome)
                return
            self._finalize_loaded_command(
                path,
                effective_command,
                outcome,
                pending_commands=pending_commands,
            )

        execution = self.dispatcher.execute(
            path,
            effective_command,
            completion=_complete,
        )
        if completed_during_dispatch:
            outcome = completed_during_dispatch[0]
            self._finalize_loaded_command(
                path,
                effective_command,
                outcome,
                pending_commands=pending_commands,
            )
            return outcome
        if execution.in_flight:
            completion_phase = _CompletionPhase.WAITING
            return "deferred"
        completion_phase = _CompletionPhase.CLOSED
        self._finalize_loaded_command(
            path,
            effective_command,
            execution.outcome,
            pending_commands=pending_commands,
        )
        return execution.outcome

    def _finalize_loaded_command(
        self,
        path: str,
        command: CommandMapping,
        outcome: CommandOutcome,
        *,
        pending_commands: CommandFileList | None,
    ) -> None:
        if is_local_publish_command(command):
            self.adapter.fast_publications.record_durable_outcome(command, outcome)
        self._apply_command_result(
            path,
            command,
            outcome,
            pending_commands=pending_commands,
        )
        self.health.record_lifecycle(command, outcome)

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
