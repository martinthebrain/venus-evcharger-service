# SPDX-License-Identifier: GPL-3.0-or-later
"""Core command scheduling for the DBus gateway write scheduler."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.dbus_adapter_write_protocols import (
    DbusWriteSchedulerAdapter,
    DropStaleCoalescedCommands,
    ProcessLocalPublishBurst,
    PublishCommand,
)
from venus_evcharger.dbus_adapter_write_support import (
    budget_elapsed,
    command_kind,
    deadline_pair,
    has_startup_registration,
    is_local_publish_command,
    register_service_command,
    should_follow_with_local_burst,
)
from venus_evcharger.dbus_gateway import DbusCommandInbox
from venus_evcharger.dbus_gateway_command_types import CommandFile, CommandFileList, CommandMapping

GATEWAY_COMMAND_RETRY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class DbusWriteSchedulerCore:
    adapter: DbusWriteSchedulerAdapter
    local_publish_burst_limit: int
    startup_registration_batch_limit: int
    startup_registration_tick_budget_seconds: float
    drop_stale_coalesced_commands: DropStaleCoalescedCommands
    process_local_publish_burst: ProcessLocalPublishBurst
    publish_command: PublishCommand
    register_path: Callable[[CommandMapping], CommandOutcome]
    set_remote_value: Callable[[CommandMapping], CommandOutcome]
    budget_available: Callable[[CommandMapping, float], bool]
    prioritized_commands: Callable[[CommandFileList], CommandFileList]
    prune_budget: Callable[[float], None]
    record_budget: Callable[[CommandMapping], None]
    record_lifecycle: Callable[[CommandMapping, str], None]
    record_processed: Callable[[], None]

    def process_one(self, *, include_local_publish: bool = True) -> bool:
        pending = self.adapter.commands.load_pending()
        coalesced = self.prioritized_commands(DbusCommandInbox.coalesce(pending))
        if not coalesced:
            return False
        if self._startup_registration_pending(coalesced):
            return self.process_startup_registration_batch(coalesced)
        return self.process_next_scheduled_command(coalesced, include_local_publish=include_local_publish)

    def _startup_registration_pending(self, commands: CommandFileList) -> bool:
        return not self.adapter.dbus_service_registered and has_startup_registration(commands=commands)

    def process_next_scheduled_command(
        self,
        commands: CommandFileList,
        *,
        include_local_publish: bool,
    ) -> bool:
        selected = self.select_next_command(commands, include_local_publish=include_local_publish)
        if selected is None:
            return False
        path, command = selected
        outcome = self.process_loaded_command(path, command)
        if should_follow_with_local_burst(command, outcome):
            self.process_local_publish_burst(max(0, self.local_publish_burst_limit - 1))
        return True

    def process_startup_registration_batch(self, commands: CommandFileList) -> bool:
        started = time.monotonic()
        register_service = register_service_command(commands)
        did_paths, processed = self.process_startup_register_paths(commands, started)
        did_service = self._process_startup_register_service(
            register_service,
            processed=processed,
            started=started,
        )
        return did_paths or did_service

    def process_startup_register_paths(
        self,
        commands: CommandFileList,
        started: float,
    ) -> tuple[bool, int]:
        did_work = False
        processed = 0
        for path, command in commands:
            if self._startup_path_budget_exhausted(started, processed):
                break
            if command_kind(command) != "register_path":
                continue
            if self.register_path(command) != "applied":
                continue
            self.adapter.commands.remove(path)
            self.record_processed()
            did_work = True
            processed += 1
        return did_work, processed

    def _process_startup_register_service(
        self,
        register_service: CommandFile | None,
        *,
        processed: int,
        started: float,
    ) -> bool:
        if not self.should_process_startup_service(register_service, processed=processed, started=started):
            return False
        assert register_service is not None
        path, command = register_service
        if self.process_command(command, command_file=path) != "applied":
            return False
        self.adapter.commands.remove(path)
        self.record_processed()
        return True

    def should_process_startup_service(
        self,
        register_service: CommandFile | None,
        *,
        processed: int,
        started: float,
    ) -> bool:
        return (
            register_service is not None
            and not self.remaining_register_paths()
            and processed < self.startup_registration_batch_limit
            and not budget_elapsed(started, self.startup_registration_tick_budget_seconds)
        )

    def _startup_path_budget_exhausted(self, started: float, processed: int) -> bool:
        return (
            processed >= self.startup_registration_batch_limit
            or budget_elapsed(started, self.startup_registration_tick_budget_seconds)
        )

    def remaining_register_paths(self) -> bool:
        return any(command_kind(command) == "register_path" for _path, command in self.adapter.commands.load_pending())

    def select_next_command(
        self,
        commands: CommandFileList,
        *,
        include_local_publish: bool = True,
    ) -> CommandFile | None:
        now = time.time()
        self.prune_budget(now)
        for path, command in commands:
            if (include_local_publish or not is_local_publish_command(command)) and self.budget_available(command, now):
                return path, command
        return None

    def process_command(self, command: CommandMapping, *, command_file: str = "") -> CommandOutcome:
        priority = str(command.get("priority") or "diagnostic")
        if not self.adapter.circuit.allows_priority(priority):
            return "deferred"
        return self.dispatch_command(command, command_file=command_file)

    def dispatch_command(self, command: CommandMapping, *, command_file: str) -> CommandOutcome:
        kind = command_kind(command)
        handlers: dict[str, Callable[[CommandMapping], CommandOutcome]] = {
            "register_service": self._register_service_command,
            "register_path": self.register_path,
            "publish_value": lambda item: self.publish_command(item, command_file=command_file),
            "publish_desired": lambda item: self.publish_command(item, command_file=command_file),
            "publish_fields": lambda item: self.publish_command(item, command_file=command_file),
            "set_value": self.set_remote_value,
        }
        handler = handlers.get(kind)
        if handler is None:
            return self.adapter.process_non_write_command(command)
        return handler(command)

    def _register_service_command(self, _command: CommandMapping) -> CommandOutcome:
        self.adapter.register_dbus_service_name()
        return "applied"

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
        return deadline > 0.0 and created_at > 0.0 and time.time() > created_at + deadline
