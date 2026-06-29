# SPDX-License-Identifier: GPL-3.0-or-later
"""Local publish scheduling for the DBus gateway write scheduler."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from venus_evcharger.dbus_adapter_components import CommandOutcome
from venus_evcharger.dbus_adapter_write_core import DbusWriteSchedulerCore
from venus_evcharger.dbus_adapter_write_protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter_write_support import (
    budget_elapsed,
    is_local_publish_command,
    local_publish_action_result,
    stale_coalesced_paths,
)
from venus_evcharger.dbus_gateway import (
    PUBLISH_PATH_RANKS,
    DbusCommandInbox,
    dbus_path_key,
)
from venus_evcharger.dbus_gateway_command_types import CommandFile, CommandFileList, CommandMapping
from venus_evcharger.dbus_gateway_core import _json_ready


@dataclass(frozen=True)
class _LocalPublishCandidate:
    processed: int
    remaining_budget: int
    pending_commands: CommandFileList
    started: float


class DbusWriteSchedulerPublish(DbusWriteSchedulerCore):
    adapter: DbusWriteSchedulerAdapter
    dynamic_local_publish_burst_limit: int
    last_processed_at: float
    last_values: dict[str, object]
    local_publish_burst_limit: int
    local_publish_tick_budget_seconds: float
    registered_paths: set[str]
    budget_available: Callable[[CommandMapping, float], bool]
    prioritized_commands: Callable[[CommandFileList], CommandFileList]
    _processed_events: deque[float]
    prune_budget: Callable[[float], None]
    prune_processed: Callable[[float], None]

    def process_local_publish_burst(self, limit: int | None = None) -> int:  # pragma: no mutate block
        if not self.adapter.dbus_service_registered:
            return 0
        remaining_budget = self.dynamic_local_publish_burst_limit if limit is None else int(limit)
        processed = 0
        pending_commands = self.adapter.commands.load_pending()
        pending = self.prioritized_commands(DbusCommandInbox.coalesce(pending_commands))
        started = time.monotonic()
        for path, command in pending:  # pragma: no branch
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
            processed, stop = local_publish_action_result(processed, action)
            if stop:
                break
        return processed

    def _process_local_publish_candidate(
        self,
        path: str,
        command: CommandMapping,
        candidate: _LocalPublishCandidate,
    ) -> str:  # pragma: no mutate block
        if self._local_publish_burst_done(candidate.processed, candidate.remaining_budget, candidate.started):
            return "break"
        if self._skip_local_publish_command(command):
            return "skip"
        outcome = self.process_loaded_command(path, command, pending_commands=candidate.pending_commands)
        return "processed" if outcome in ("applied", "dropped") else "break"

    def _local_publish_burst_done(self, processed: int, remaining_budget: int, started: float) -> bool:  # pragma: no mutate block
        return processed >= max(0, remaining_budget) or budget_elapsed(started, self.local_publish_tick_budget_seconds)

    def _skip_local_publish_command(self, command: CommandMapping) -> bool:  # pragma: no mutate block
        return not is_local_publish_command(command) or not self.budget_available(command, time.time())

    def next_local_publish_command(self) -> CommandFile | None:  # pragma: no mutate block
        now = time.time()
        self.prune_budget(now)
        pending = self.prioritized_commands(DbusCommandInbox.coalesce(self.adapter.commands.load_pending()))
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
    ) -> None:  # pragma: no mutate block
        key = str(processed_command.get("coalesce_key") or "")
        if not key:
            return
        commands = self.adapter.commands.load_pending() if pending_commands is None else pending_commands
        for stale_path in stale_coalesced_paths(commands, processed_path=processed_path, key=key):
            self.adapter.commands.remove(stale_path)

    def register_path(self, command: CommandMapping) -> CommandOutcome:  # pragma: no mutate block
        path = str(command.get("path") or "")
        if not path or path in self.registered_paths:
            return "applied"
        value = command.get("value")
        writeable = bool(command.get("writeable"))
        self.adapter.dbus_service.add_path(
            path,
            value,
            writeable=writeable,
            onchangecallback=self.handle_gui_write if writeable else None,
        )
        self.registered_paths.add(path)
        self.last_values[path] = value
        return "applied"

    def handle_gui_write(self, path: str, value: object) -> bool:  # pragma: no mutate block
        self.last_values[str(path)] = value
        self.adapter.core_commands.enqueue(
            {
                "kind": "user_command",
                "source": "dbus-gui",
                "path": str(path),
                "value": _json_ready(value),
                "priority": "user",
                "coalesce_key": f"core:{path}",
            }
        )
        return True

    def publish_command(self, command: CommandMapping, *, command_file: str = "") -> CommandOutcome:  # pragma: no mutate block
        if str(command.get("kind")) == "publish_desired":
            return self._publish_desired(command, command_file=command_file)
        return self.publish_path(str(command.get("path") or ""), command.get("value"))

    def _publish_desired(self, command: CommandMapping, *, command_file: str) -> CommandOutcome:  # pragma: no mutate block
        paths = command.get("paths")
        if not isinstance(paths, Mapping):
            return "dropped"
        items = _prioritized_publish_items(paths)
        if not items:
            return "applied"
        processed, outcome = self._publish_desired_items(items)
        if outcome != "applied":
            return outcome if processed == 0 else "deferred"
        return self._store_remaining_desired(command, command_file=command_file, items=items, processed=processed)

    def _publish_desired_items(self, items: list[tuple[str, object]]) -> tuple[int, CommandOutcome]:  # pragma: no mutate block
        processed = 0
        for path, value in items[: self.local_publish_burst_limit]:
            outcome = self.publish_path(str(path), value)
            if outcome != "applied":
                return processed, outcome
            processed += 1
        return processed, "applied"

    def _store_remaining_desired(
        self,
        command: CommandMapping,
        *,
        command_file: str,
        items: list[tuple[str, object]],
        processed: int,
    ) -> CommandOutcome:  # pragma: no mutate block
        remaining = {str(path): value for path, value in items[processed:]}
        if not remaining:
            return "applied"
        if command_file:
            self.adapter.json_writer.write(command_file, {**dict(command), "paths": remaining})
        return "deferred"

    def publish_path(self, path: str, value: object) -> CommandOutcome:  # pragma: no mutate block
        if not path:
            return "applied"
        if self.last_values.get(path) == value:
            if path in self.registered_paths:
                self._refresh_local_publish_cache(path, value)
            return "applied"
        if path not in self.registered_paths:
            logging.debug("Dropping publish for unregistered DBus path %s", path)
            return "dropped"
        self.timed_local_publish(lambda: self.adapter.dbus_service.__setitem__(path, value))
        self.last_values[path] = value
        self._refresh_local_publish_cache(path, value)
        return "applied"

    def _refresh_local_publish_cache(self, path: str, value: object) -> None:  # pragma: no mutate block
        source = f"{self.adapter.service_name}{path}"
        self.adapter.cache.update_value(
            dbus_path_key(self.adapter.service_name, path),
            value,
            source=source,
            confidence=1.0,
        )

    def timed_local_publish(self, operation: Callable[[], object]) -> object:  # pragma: no mutate block
        return self.adapter.timed_local_publish(operation)

    def record_processed(self) -> None:  # pragma: no mutate block
        now = time.time()
        self.last_processed_at = now
        self._processed_events.append(now)
        self.prune_processed(now)


def _prioritized_publish_items(paths: Mapping[object, object]) -> list[tuple[str, object]]:
    items = [(str(path), value) for path, value in paths.items()]
    return sorted(items, key=lambda item: (PUBLISH_PATH_RANKS.get(item[0], 9), item[0]))
