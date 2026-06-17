# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway write-scheduler mixins."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from venus_evcharger.dbus_adapter_components import CommandOutcome
from venus_evcharger.dbus_adapter_write_support import (
    _is_local_publish_command,
    _local_publish_action_result,
    _stale_coalesced_paths,
)
from venus_evcharger.dbus_gateway import DbusCommandInbox, dbus_path_key

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


@dataclass(frozen=True)
class _LocalPublishCandidate:
    processed: int
    remaining_budget: int
    pending_commands: list[tuple[str, dict[str, Any]]]
    started: float


class DbusWriteSchedulerPublishMixin:
    def process_local_publish_burst(self, limit: int | None = None) -> int:
        if not self.adapter._dbusservice_registered:
            return 0
        remaining_budget = self.dynamic_local_publish_burst_limit if limit is None else int(limit)
        processed = 0
        pending_commands = self.adapter.commands.load_pending()
        pending = self._prioritized_commands(DbusCommandInbox.coalesce(pending_commands))
        started = time.monotonic()
        for path, command in pending:
            action = self._process_local_publish_candidate(
                path,
                command,
                _LocalPublishCandidate(
                    processed=processed,
                    remaining_budget=remaining_budget,
                    pending_commands=pending_commands,
                    started=started,
                ),
            )
            processed, stop = _local_publish_action_result(processed, action)
            if stop:
                break
        return processed

    def _process_local_publish_candidate(
        self,
        path: str,
        command: Mapping[str, Any],
        candidate: _LocalPublishCandidate,
    ) -> str:
        if self._local_publish_burst_done(candidate.processed, candidate.remaining_budget, candidate.started):
            return "break"
        if self._skip_local_publish_command(command):
            return "skip"
        outcome = self._process_loaded_command(path, command, pending_commands=candidate.pending_commands)
        return "processed" if outcome in ("applied", "dropped") else "break"

    def _local_publish_burst_done(self, processed: int, remaining_budget: int, started: float) -> bool:
        return processed >= max(0, remaining_budget) or self._budget_elapsed(started, self.local_publish_tick_budget_seconds)

    def _skip_local_publish_command(self, command: Mapping[str, Any]) -> bool:
        return not _is_local_publish_command(command) or not self._budget_available(command, time.time())

    @staticmethod
    def _budget_elapsed(started: float, budget_seconds: float) -> bool:
        return time.monotonic() - started >= budget_seconds

    def _next_local_publish_command(self) -> tuple[str, dict[str, Any]] | None:
        now = time.time()
        self._prune_budget(now)
        pending = self._prioritized_commands(DbusCommandInbox.coalesce(self.adapter.commands.load_pending()))
        for path, command in pending:
            if _is_local_publish_command(command) and self._budget_available(command, now):
                return path, command
        return None

    def drop_stale_coalesced_commands(
        self,
        processed_path: str,
        processed_command: Mapping[str, Any],
        *,
        pending_commands: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        key = str(processed_command.get("coalesce_key") or "")
        if not key:
            return
        commands = self.adapter.commands.load_pending() if pending_commands is None else pending_commands
        for stale_path in _stale_coalesced_paths(commands, processed_path=processed_path, key=key):
            self.adapter.commands.remove(stale_path)

    def register_path(self, command: Mapping[str, Any]) -> CommandOutcome:
        self.adapter._ensure_dbus_service()
        path = str(command.get("path") or "")
        if not path or path in self.registered_paths:
            return "applied"
        value = command.get("value")
        writeable = bool(command.get("writeable"))
        self.adapter._dbusservice.add_path(
            path,
            value,
            writeable=writeable,
            onchangecallback=self.handle_gui_write if writeable else None,
        )
        self.registered_paths.add(path)
        self.last_values[path] = value
        return "applied"

    def handle_gui_write(self, path: str, value: Any) -> bool:
        self.last_values[str(path)] = value
        self.adapter.core_commands.enqueue(
            {
                "kind": "user_command",
                "source": "dbus-gui",
                "path": str(path),
                "value": self.adapter._json_ready(value),
                "priority": "user",
                "coalesce_key": f"core:{path}",
            }
        )
        return True

    def publish_command(self, command: Mapping[str, Any], *, command_file: str = "") -> CommandOutcome:
        if str(command.get("kind")) == "publish_desired":
            return self._publish_desired(command, command_file=command_file)
        return self.publish_path(str(command.get("path") or ""), command.get("value"))

    def _publish_desired(self, command: Mapping[str, Any], *, command_file: str) -> CommandOutcome:
        paths = command.get("paths")
        if not isinstance(paths, Mapping):
            return "dropped"
        items = list(paths.items())
        if not items:
            return "applied"
        processed, outcome = self._publish_desired_items(items)
        if outcome != "applied":
            return outcome if processed == 0 else "deferred"
        return self._store_remaining_desired(command, command_file=command_file, items=items, processed=processed)

    def _publish_desired_items(self, items: list[tuple[Any, Any]]) -> tuple[int, CommandOutcome]:
        processed = 0
        for path, value in items[: self.local_publish_burst_limit]:
            outcome = self.publish_path(str(path), value)
            if outcome != "applied":
                return processed, outcome
            processed += 1
        return processed, "applied"

    def _store_remaining_desired(
        self,
        command: Mapping[str, Any],
        *,
        command_file: str,
        items: list[tuple[Any, Any]],
        processed: int,
    ) -> CommandOutcome:
        remaining = {str(path): value for path, value in items[processed:]}
        if not remaining:
            return "applied"
        if command_file:
            self.adapter.json_writer.write(command_file, {**dict(command), "paths": remaining})
        return "deferred"

    def publish_path(self, path: str, value: Any) -> CommandOutcome:
        if not path or self.last_values.get(path) == value:
            return "applied"
        self.adapter._ensure_dbus_service()
        if path not in self.registered_paths:
            logging.debug("Dropping publish for unregistered DBus path %s", path)
            return "dropped"
        self.adapter._timed_local_publish(lambda: self.adapter._dbusservice.__setitem__(path, value))
        self.last_values[path] = value
        source = f"{self.adapter.service_name}{path}"
        self.adapter.cache.update_value(
            dbus_path_key(self.adapter.service_name, path),
            value,
            source=source,
            confidence=1.0,
        )
        return "applied"

    def _record_processed(self) -> None:
        now = time.time()
        self.last_processed_at = now
        self._processed_events.append(now)
        self._prune_processed(now)
