# SPDX-License-Identifier: GPL-3.0-or-later
"""Core command processing for scheduled gateway operations."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.dbus_adapter.write.protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter.write.support import command_ready, deadline_pair, is_local_publish_command
from venus_evcharger.ipc.command_types import CommandFile, CommandFileList, CommandMapping
from venus_evcharger.ipc.gateway_operations import SEMANTIC_GATEWAY_KINDS
from venus_evcharger.ipc.gateway_publication import SEMANTIC_PUBLICATION_KINDS
from venus_evcharger.ipc.generic_shelly_configuration import DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND

GATEWAY_COMMAND_RETRY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class DbusWriteSchedulerCore(ABC):
    """Schedule typed gateway commands without exposing DBus paths."""

    adapter: DbusWriteSchedulerAdapter
    local_publish_burst_limit: int
    last_scheduled_outcome: CommandOutcome | None

    @abstractmethod
    def drop_stale_coalesced_commands(
        self,
        processed_path: str,
        processed_command: CommandMapping,
        *,
        pending_commands: CommandFileList | None = None,
    ) -> None:
        """Remove older commands superseded by one completed command."""

    @abstractmethod
    def process_local_publish_burst(self, limit: int | None = None) -> int:
        """Process a bounded burst of local semantic publications."""

    @abstractmethod
    def process_semantic_operation(
        self,
        command: CommandMapping,
        *,
        command_file: str,
    ) -> CommandOutcome:
        """Execute one adapter-owned semantic system operation."""

    @abstractmethod
    def process_publication(self, command: CommandMapping) -> CommandOutcome:
        """Apply one semantic EVCS or companion publication command."""

    @abstractmethod
    def budget_available(self, command: CommandMapping, now: float) -> bool:
        """Return whether the command's queue class still has budget."""

    @staticmethod
    @abstractmethod
    def prioritized_commands(commands: CommandFileList) -> CommandFileList:
        """Return pending commands in deterministic scheduler order."""

    @abstractmethod
    def prune_budget(self, now: float) -> None:
        """Discard queue-budget observations outside the active window."""

    @abstractmethod
    def prune_processed(self, now: float) -> None:
        """Discard processed-command observations outside the active window."""

    @abstractmethod
    def record_budget(self, command: CommandMapping) -> None:
        """Record one command against its queue-class budget."""

    @abstractmethod
    def record_lifecycle(self, command: CommandMapping, state: str) -> None:
        """Record one externally visible command lifecycle transition."""

    @abstractmethod
    def record_processed(self) -> None:
        """Record one completed command for health accounting."""

    def process_one(self, *, include_local_publish: bool = True) -> bool:
        self.last_scheduled_outcome = None
        pending = self.adapter.commands.load_pending()
        commands = self.prioritized_commands(self.adapter.commands.coalesce(pending))
        selected = self.select_next_command(commands, include_local_publish=include_local_publish)
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
    ) -> CommandFile | None:
        now = time.time()
        self.prune_budget(now)
        for path, command in commands:
            if self._command_selectable(command, include_local_publish=include_local_publish, now=now):
                return path, command
        return None

    def _command_selectable(
        self,
        command: CommandMapping,
        *,
        include_local_publish: bool,
        now: float,
    ) -> bool:
        return (
            command_ready(command, now)
            and (include_local_publish or not is_local_publish_command(command))
            and self.budget_available(command, now)
        )

    def process_command(self, command: CommandMapping, *, command_file: str = "") -> CommandOutcome:
        if self._command_blocked(command):
            return "deferred"
        return self._dispatch_command(command, command_file=command_file)

    def _command_blocked(self, command: CommandMapping) -> bool:
        priority = str(command.get("priority") or "diagnostic")
        return not self.adapter.circuit.allows_priority(priority)

    def _dispatch_command(self, command: CommandMapping, *, command_file: str) -> CommandOutcome:
        kind = self._command_kind(command)
        if kind in SEMANTIC_PUBLICATION_KINDS:
            return self.process_publication(command)
        if self._is_semantic_operation(kind):
            return self.process_semantic_operation(command, command_file=command_file)
        return self.adapter.process_non_write_command(command)

    @staticmethod
    def _command_kind(command: CommandMapping) -> str:
        return str(command.get("kind") or command.get("type") or "")

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
        outcome = self.command_outcome(path, command)
        self._apply_command_result(path, command, outcome, pending_commands=pending_commands)
        self.record_lifecycle(command, outcome)
        return outcome

    def _drop_expired_command(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None,
    ) -> CommandOutcome:
        self.adapter.commands.remove(path)
        self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
        self.record_lifecycle(command, "expired")
        self.record_processed()
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
            self.record_budget(command)
        if outcome in ("applied", "dropped"):
            self.adapter.commands.remove(path)
            self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
            self.record_processed()
            self.record_budget(command)

    @staticmethod
    def command_expired(command: CommandMapping) -> bool:
        deadline, created_at = deadline_pair(command)
        return bool(deadline > 0.0 and created_at > 0.0 and time.time() > created_at + deadline)
