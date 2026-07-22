# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic EVCS and companion publication scheduling."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.write.core import DbusWriteSchedulerCore
from venus_evcharger.dbus_adapter.write.protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter.write.support import (
    budget_elapsed,
    is_local_publish_command,
    local_publish_action_result,
    stale_coalesced_paths,
)
from venus_evcharger.ipc.command_types import CommandFile, CommandFileList, CommandMapping
from venus_evcharger.ipc.gateway_publication import (
    parse_publish_companion_fields,
    parse_publish_evcs_fields,
    parse_register_companion,
    parse_register_evcs,
)


@dataclass(frozen=True, slots=True)
class _LocalPublishCandidate:
    processed: int
    remaining_budget: int
    pending_commands: CommandFileList
    started: float


class DbusWriteSchedulerPublish(DbusWriteSchedulerCore):
    adapter: DbusWriteSchedulerAdapter
    dynamic_local_publish_burst_limit: int
    last_processed_at: float
    local_publish_tick_budget_seconds: float
    _processed_events: deque[float]

    def process_local_publish_burst(self, limit: int | None = None) -> int:
        remaining = self.dynamic_local_publish_burst_limit if limit is None else int(limit)
        processed = 0
        pending_commands = self.adapter.commands.load_pending()
        pending = self.prioritized_commands(self.adapter.commands.coalesce(pending_commands))
        started = time.monotonic()
        for path, command in pending:
            action = self._process_local_publish_candidate(
                path,
                command,
                _LocalPublishCandidate(processed, remaining, pending_commands, started),
            )
            processed, stop = local_publish_action_result(processed, action)
            if stop:
                break
        return processed

    def _process_local_publish_candidate(
        self,
        path: str,
        command: CommandMapping,
        candidate: _LocalPublishCandidate,
    ) -> str:
        if self._local_publish_burst_done(candidate.processed, candidate.remaining_budget, candidate.started):
            return "break"
        if not is_local_publish_command(command) or not self.budget_available(command, time.time()):
            return "skip"
        outcome = self.process_loaded_command(path, command, pending_commands=candidate.pending_commands)
        return "processed" if outcome in ("applied", "dropped") else "break"

    def _local_publish_burst_done(self, processed: int, remaining: int, started: float) -> bool:
        return processed >= max(0, remaining) or budget_elapsed(started, self.local_publish_tick_budget_seconds)

    def next_local_publish_command(self) -> CommandFile | None:
        now = time.time()
        self.prune_budget(now)
        pending = self.prioritized_commands(self.adapter.commands.coalesce(self.adapter.commands.load_pending()))
        for path, command in pending:
            if is_local_publish_command(command) and self.budget_available(command, now):
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
        commands = self.adapter.commands.load_pending() if pending_commands is None else pending_commands
        for stale_path in stale_coalesced_paths(commands, processed_path=processed_path, key=key):
            self.adapter.commands.remove(stale_path)

    def process_publication(self, command: CommandMapping) -> CommandOutcome:
        register_evcs = parse_register_evcs(command)
        if register_evcs is not None:
            return self.adapter.publication_registry.register_evcs(register_evcs)
        publish_evcs = parse_publish_evcs_fields(command)
        if publish_evcs is not None:
            return self.adapter.publication_registry.publish_evcs(publish_evcs)
        register_companion = parse_register_companion(command)
        if register_companion is not None:
            return self.adapter.publication_registry.register_companion(register_companion)
        publish_companion = parse_publish_companion_fields(command)
        if publish_companion is not None:
            return self.adapter.publication_registry.publish_companion(publish_companion)
        return "dropped"

    def record_processed(self) -> None:
        now = time.time()
        self.last_processed_at = now
        self._processed_events.append(now)
        self.prune_processed(now)
