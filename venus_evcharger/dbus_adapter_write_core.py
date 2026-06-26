# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway write-scheduler mixins."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.dbus_adapter_write_support import (
    command_kind,
    deadline_pair,
    has_startup_registration,
    is_local_publish_command,
    register_service_command,
    should_follow_with_local_burst,
)
from venus_evcharger.dbus_gateway import DbusCommandInbox

_QUEUE_CLASS_RANKS = {
    "startup/register": 0,
    "gui-critical-publish": 1,
    "remote-write": 2,
    "local-publish": 3,
    "read-fast": 4,
    "read-slow": 5,
    "discovery": 6,
    "introspection": 7,
    "diagnostic": 8,
}

class DbusWriteSchedulerCoreMixin:
    adapter: Any
    local_publish_burst_limit: int
    startup_registration_batch_limit: int
    startup_registration_tick_budget_seconds: float
    drop_stale_coalesced_commands: Callable[..., None]
    process_local_publish_burst: Callable[..., int]
    publish_command: Callable[..., CommandOutcome]
    register_path: Callable[[Mapping[str, Any]], CommandOutcome]
    set_remote_value: Callable[[Mapping[str, Any]], CommandOutcome]
    budget_available: Callable[[Mapping[str, Any], float], bool]
    _budget_elapsed: Callable[[float, float], bool]
    prioritized_commands: Callable[[list[tuple[str, dict[str, Any]]]], list[tuple[str, dict[str, Any]]]]
    prune_budget: Callable[[float], None]
    record_budget: Callable[[Mapping[str, Any]], None]
    record_lifecycle: Callable[[Mapping[str, Any], str], None]
    record_processed: Callable[[], None]

    def process_one(self, *, include_local_publish: bool = True) -> bool:
        pending = self.adapter.commands.load_pending()
        coalesced = self.prioritized_commands(DbusCommandInbox.coalesce(pending))
        if not coalesced:
            return False
        if self._startup_registration_pending(coalesced):
            return self.process_startup_registration_batch(coalesced)
        return self.process_next_scheduled_command(coalesced, include_local_publish=include_local_publish)

    def _startup_registration_pending(self, commands: list[tuple[str, dict[str, Any]]]) -> bool:
        return not self.adapter._dbusservice_registered and has_startup_registration(commands=commands)

    def process_next_scheduled_command(
        self,
        commands: list[tuple[str, dict[str, Any]]],
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

    def process_startup_registration_batch(self, commands: list[tuple[str, dict[str, Any]]]) -> bool:
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
        commands: list[tuple[str, dict[str, Any]]],
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
        register_service: tuple[str, dict[str, Any]] | None,
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
        register_service: tuple[str, dict[str, Any]] | None,
        *,
        processed: int,
        started: float,
    ) -> bool:
        return (
            register_service is not None
            and not self.remaining_register_paths()
            and processed < self.startup_registration_batch_limit
            and not self._budget_elapsed(started, self.startup_registration_tick_budget_seconds)
        )

    def _startup_path_budget_exhausted(self, started: float, processed: int) -> bool:
        return (
            processed >= self.startup_registration_batch_limit
            or self._budget_elapsed(started, self.startup_registration_tick_budget_seconds)
        )

    def remaining_register_paths(self) -> bool:
        return any(command_kind(command) == "register_path" for _path, command in self.adapter.commands.load_pending())

    def select_next_command(
        self,
        commands: list[tuple[str, dict[str, Any]]],
        *,
        include_local_publish: bool = True,
    ) -> tuple[str, dict[str, Any]] | None:
        now = time.time()
        self.prune_budget(now)
        for path, command in commands:
            if (include_local_publish or not is_local_publish_command(command)) and self.budget_available(command, now):
                return path, command
        return None

    def process_command(self, command: Mapping[str, Any], *, command_file: str = "") -> CommandOutcome:
        priority = str(command.get("priority") or "diagnostic")
        if not self.adapter.circuit.allows_priority(priority):
            return "deferred"
        return self.dispatch_command(command, command_file=command_file)

    def dispatch_command(self, command: Mapping[str, Any], *, command_file: str) -> CommandOutcome:
        kind = command_kind(command)
        handlers: dict[str, Callable[[Mapping[str, Any]], CommandOutcome]] = {
            "register_service": self._register_service_command,
            "register_path": self.register_path,
            "publish_value": lambda item: self.publish_command(item, command_file=command_file),
            "publish_desired": lambda item: self.publish_command(item, command_file=command_file),
            "set_value": self.set_remote_value,
        }
        handler = handlers.get(kind)
        if handler is None:
            return cast(CommandOutcome, self.adapter.process_non_write_command(command))
        return handler(command)

    def _register_service_command(self, _command: Mapping[str, Any]) -> CommandOutcome:
        self.adapter._register_dbus_service_name()
        return "applied"

    def process_loaded_command(
        self,
        path: str,
        command: Mapping[str, Any],
        *,
        pending_commands: list[tuple[str, dict[str, Any]]] | None = None,
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
        command: Mapping[str, Any],
        *,
        pending_commands: list[tuple[str, dict[str, Any]]] | None,
    ) -> CommandOutcome:
        self.adapter.commands.remove(path)
        self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
        self.record_lifecycle(command, "expired")
        self.record_processed()
        return "dropped"

    def command_outcome(self, path: str, command: Mapping[str, Any]) -> CommandOutcome:
        try:
            return self.process_command(command, command_file=path)
        except DbusOperationDeferred:
            return "deferred"
        except Exception as error:  # pylint: disable=broad-except
            logging.exception("Gateway command failed; keeping for retry path=%s: %s", path, error)
            return "deferred"

    def _apply_command_result(
        self,
        path: str,
        command: Mapping[str, Any],
        outcome: CommandOutcome,
        *,
        pending_commands: list[tuple[str, dict[str, Any]]] | None,
    ) -> None:
        if outcome == "deferred":
            self.record_budget(command)
        if outcome in ("applied", "dropped"):
            self.adapter.commands.remove(path)
            self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
            self.record_processed()
            self.record_budget(command)

    @staticmethod
    def command_expired(command: Mapping[str, Any]) -> bool:
        deadline, created_at = deadline_pair(command)
        return deadline > 0.0 and created_at > 0.0 and time.time() > created_at + deadline
