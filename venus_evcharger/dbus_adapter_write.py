# SPDX-License-Identifier: GPL-3.0-or-later
"""Write scheduling for the dedicated DBus adapter."""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Any, Mapping

import dbus

from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_gateway import DbusCommandInbox, command_queue_class, dbus_path_key

_PRIORITY_RANKS = {"safety": 0, "user": 1, "publish": 2, "read": 3, "optional": 4, "discovery": 5, "diagnostic": 6}
_QUEUE_CLASS_RANKS = {
    "startup/register": 0,
    "gui-critical-publish": 1,
    "remote-write": 2,
    "read-fast": 3,
    "local-publish": 4,
    "read-slow": 5,
    "discovery": 6,
    "introspection": 7,
    "diagnostic": 8,
}


class DbusWriteScheduler:
    """Schedule and coalesce DBus writes owned by the adapter process."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.registered_paths: set[str] = set()
        self.last_values: dict[str, Any] = {}
        defaults = adapter.config["DEFAULT"]
        self.local_publish_burst_limit = max(1, int(float(defaults.get("DbusGatewayLocalPublishBurstLimit", 20))))
        self.local_publish_tick_budget_seconds = max(
            0.001,
            float(defaults.get("DbusGatewayLocalPublishTickBudgetMs", 75.0)) / 1000.0,
        )
        self.dynamic_local_publish_burst_limit = self.local_publish_burst_limit
        self.startup_registration_batch_limit = max(
            1,
            int(float(defaults.get("DbusGatewayStartupRegistrationBatchLimit", 100))),
        )
        self.startup_registration_tick_budget_seconds = max(
            0.001,
            float(defaults.get("DbusGatewayStartupRegistrationTickBudgetMs", 150.0)) / 1000.0,
        )
        self.queue_class_budgets = self._queue_class_budgets(defaults)
        self.base_queue_class_budgets = dict(self.queue_class_budgets)
        self._processed_events: deque[float] = deque()
        self._budget_events: deque[tuple[float, str]] = deque()
        self._lifecycle_events: deque[tuple[float, str, str]] = deque()
        self._lifecycle_counts: dict[str, int] = {}
        self.last_processed_at = 0.0

    def process_one(self, *, include_local_publish: bool = True) -> bool:
        pending = self.adapter.commands.load_pending()
        coalesced = self._prioritized_commands(DbusCommandInbox.coalesce(pending))
        if not coalesced:
            return False
        if not self.adapter._dbusservice_registered and _has_startup_registration(commands=coalesced):
            return self.process_startup_registration_batch(coalesced)
        selected = self._select_next_command(coalesced, include_local_publish=include_local_publish)
        if selected is None:
            return False
        path, command = selected
        outcome = self._process_loaded_command(path, command)
        if outcome in ("applied", "dropped") and _is_local_publish_command(command):
            self.process_local_publish_burst(max(0, self.local_publish_burst_limit - 1))
        return True

    def process_startup_registration_batch(self, commands: list[tuple[str, dict[str, Any]]]) -> bool:
        did_work = False
        register_service: tuple[str, dict[str, Any]] | None = None
        processed = 0
        started = time.monotonic()
        for path, command in commands:
            if self._budget_elapsed(started, self.startup_registration_tick_budget_seconds):
                break
            kind = str(command.get("kind") or command.get("type") or "")
            if kind == "register_path":
                if processed >= self.startup_registration_batch_limit:
                    break
                if self.register_path(command) == "applied":
                    self.adapter.commands.remove(path)
                    did_work = True
                    processed += 1
                    self._record_processed()
            elif kind == "register_service":
                register_service = (path, command)
        remaining_register_paths = any(
            str(command.get("kind") or command.get("type") or "") == "register_path"
            for _path, command in self.adapter.commands.load_pending()
        )
        if (
            register_service is not None
            and not remaining_register_paths
            and processed < self.startup_registration_batch_limit
            and not self._budget_elapsed(started, self.startup_registration_tick_budget_seconds)
        ):
            path, command = register_service
            if self.process_command(command, command_file=path) == "applied":
                self.adapter.commands.remove(path)
                did_work = True
                self._record_processed()
        return did_work

    def _select_next_command(
        self,
        commands: list[tuple[str, dict[str, Any]]],
        *,
        include_local_publish: bool = True,
    ) -> tuple[str, dict[str, Any]] | None:
        now = time.time()
        self._prune_budget(now)
        for path, command in commands:
            if (include_local_publish or not _is_local_publish_command(command)) and self._budget_available(command, now):
                return path, command
        return None

    def process_command(self, command: Mapping[str, Any], *, command_file: str = "") -> CommandOutcome:
        kind = str(command.get("kind") or command.get("type") or "")
        priority = str(command.get("priority") or "diagnostic")
        if not self.adapter.circuit.allows_priority(priority):
            return "deferred"
        if kind == "register_service":
            self.adapter._register_dbus_service_name()
            return "applied"
        if kind == "register_path":
            return self.register_path(command)
        if kind in ("publish_value", "publish_desired"):
            return self.publish_command(command, command_file=command_file)
        if kind == "set_value":
            return self.set_remote_value(command)
        return self.adapter._process_non_write_command(command)

    def _process_loaded_command(
        self,
        path: str,
        command: Mapping[str, Any],
        *,
        pending_commands: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> CommandOutcome:
        if self._is_expired(command):
            self.adapter.commands.remove(path)
            self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
            self._record_lifecycle(command, "expired")
            self._record_processed()
            return "dropped"
        try:
            outcome = self.process_command(command, command_file=path)
        except DbusOperationDeferred:
            outcome = "deferred"
        except Exception as error:  # pylint: disable=broad-except
            logging.exception("Gateway command failed; keeping for retry path=%s: %s", path, error)
            outcome = "deferred"
        if outcome in ("applied", "dropped"):
            self.adapter.commands.remove(path)
            self.drop_stale_coalesced_commands(path, command, pending_commands=pending_commands)
            self._record_processed()
            self._record_budget(command)
        self._record_lifecycle(command, outcome)
        return outcome

    @staticmethod
    def _is_expired(command: Mapping[str, Any]) -> bool:
        try:
            deadline = float(command.get("deadline_s", 0.0) or 0.0)
            created_at = float(command.get("created_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        return deadline > 0.0 and created_at > 0.0 and time.time() > created_at + deadline

    def process_local_publish_burst(self, limit: int | None = None) -> int:
        if not self.adapter._dbusservice_registered:
            return 0
        remaining_budget = self.dynamic_local_publish_burst_limit if limit is None else int(limit)
        processed = 0
        pending_commands = self.adapter.commands.load_pending()
        pending = self._prioritized_commands(DbusCommandInbox.coalesce(pending_commands))
        started = time.monotonic()
        for path, command in pending:
            if processed >= max(0, remaining_budget):
                break
            if self._budget_elapsed(started, self.local_publish_tick_budget_seconds):
                break
            if not _is_local_publish_command(command) or not self._budget_available(command, time.time()):
                continue
            outcome = self._process_loaded_command(path, command, pending_commands=pending_commands)
            if outcome not in ("applied", "dropped"):
                break
            processed += 1
        return processed

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
        for path, command in commands:
            if path != processed_path and str(command.get("coalesce_key") or "") == key:
                self.adapter.commands.remove(path)

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
            paths = command.get("paths")
            if isinstance(paths, Mapping):
                items = list(paths.items())
                if not items:
                    return "applied"
                processed = 0
                remaining_items = items
                for path, value in items[: self.local_publish_burst_limit]:
                    item_outcome = self.publish_path(str(path), value)
                    if item_outcome != "applied":
                        return item_outcome if processed == 0 else "deferred"
                    processed += 1
                    remaining_items = items[processed:]
                remaining = {str(remaining_path): remaining_value for remaining_path, remaining_value in remaining_items}
                if remaining and command_file:
                    self.adapter.json_writer.write(command_file, {**dict(command), "paths": remaining})
                    return "deferred"
                return "applied"
            return "dropped"
        return self.publish_path(str(command.get("path") or ""), command.get("value"))

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

    def health(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        self._prune_processed(current)
        self._prune_lifecycle(current)
        return {
            "processed_commands_60s": len(self._processed_events),
            "last_processed_at": self.last_processed_at,
            "local_publish_burst_limit": self.local_publish_burst_limit,
            "dynamic_local_publish_burst_limit": self.dynamic_local_publish_burst_limit,
            "local_publish_tick_budget_ms": self.local_publish_tick_budget_seconds * 1000.0,
            "startup_registration_batch_limit": self.startup_registration_batch_limit,
            "startup_registration_tick_budget_ms": self.startup_registration_tick_budget_seconds * 1000.0,
            "queue_class_budgets": dict(sorted(self.queue_class_budgets.items())),
            "queue_class_usage_1s": self._queue_class_usage_1s(),
            "lifecycle_counts": dict(sorted(self._lifecycle_counts.items())),
            "lifecycle_counts_60s": self._lifecycle_counts_60s(),
        }

    def set_dynamic_local_publish_burst(self, burst: int) -> None:
        """Adjust local publish capacity while preserving conservative DBus budgets."""
        normalized = max(1, int(burst))
        self.dynamic_local_publish_burst_limit = normalized
        self.queue_class_budgets = dict(self.base_queue_class_budgets)
        if normalized <= self.local_publish_burst_limit:
            return
        self.queue_class_budgets["gui-critical-publish"] = max(
            self.queue_class_budgets.get("gui-critical-publish", 1),
            normalized,
        )
        self.queue_class_budgets["local-publish"] = max(
            self.queue_class_budgets.get("local-publish", 1),
            normalized,
        )

    def _prune_processed(self, now: float) -> None:
        cutoff = now - 60.0
        while self._processed_events and self._processed_events[0] < cutoff:
            self._processed_events.popleft()

    @staticmethod
    def _queue_class_budgets(defaults: Mapping[str, Any]) -> dict[str, int]:
        return {
            "startup/register": max(1, int(float(defaults.get("DbusGatewayQueueBudgetStartupRegister", 100)))),
            "gui-critical-publish": max(1, int(float(defaults.get("DbusGatewayQueueBudgetGuiCriticalPublish", 50)))),
            "local-publish": max(1, int(float(defaults.get("DbusGatewayQueueBudgetLocalPublish", 30)))),
            "remote-write": max(1, int(float(defaults.get("DbusGatewayQueueBudgetRemoteWrite", 2)))),
            "read-fast": max(1, int(float(defaults.get("DbusGatewayQueueBudgetReadFast", 4)))),
            "read-slow": max(0, int(float(defaults.get("DbusGatewayQueueBudgetReadSlow", 2)))),
            "discovery": max(0, int(float(defaults.get("DbusGatewayQueueBudgetDiscovery", 1)))),
            "introspection": max(0, int(float(defaults.get("DbusGatewayQueueBudgetIntrospection", 1)))),
            "diagnostic": max(0, int(float(defaults.get("DbusGatewayQueueBudgetDiagnostic", 1)))),
        }

    def _budget_available(self, command: Mapping[str, Any], now: float) -> bool:
        queue_class = str(command.get("queue_class") or command_queue_class(command))
        limit = int(self.queue_class_budgets.get(queue_class, 1))
        if limit <= 0:
            return False
        return sum(1 for timestamp, item_class in self._budget_events if item_class == queue_class and now - timestamp <= 1.0) < limit

    def _record_budget(self, command: Mapping[str, Any]) -> None:
        now = time.time()
        self._budget_events.append((now, str(command.get("queue_class") or command_queue_class(command))))
        self._prune_budget(now)

    def _prune_budget(self, now: float) -> None:
        cutoff = now - 1.0
        while self._budget_events and self._budget_events[0][0] < cutoff:
            self._budget_events.popleft()

    def _queue_class_usage_1s(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _timestamp, queue_class in self._budget_events:
            counts[queue_class] = counts.get(queue_class, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _prioritized_commands(commands: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        return sorted(
            commands,
            key=lambda item: (
                _priority_rank(item[1].get("priority")),
                _QUEUE_CLASS_RANKS.get(str(item[1].get("queue_class") or command_queue_class(item[1])), 99),
                _float_or_zero(item[1].get("created_at")),
            ),
        )

    def _record_lifecycle(self, command: Mapping[str, Any], state: str) -> None:
        now = time.time()
        queue_class = str(command.get("queue_class") or command_queue_class(command))
        normalized_state = str(state or "unknown")
        self._lifecycle_counts[normalized_state] = self._lifecycle_counts.get(normalized_state, 0) + 1
        self._lifecycle_events.append((now, normalized_state, queue_class))
        self._prune_lifecycle(now)
        path = str(getattr(self.adapter, "command_lifecycle_path", "") or "")
        if not path:
            return
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(
                    compact_json(
                        {
                            "at": now,
                            "state": normalized_state,
                            "queue_class": queue_class,
                            "kind": command.get("kind") or command.get("type") or "",
                            "id": command.get("id", ""),
                            "coalesce_key": command.get("coalesce_key", ""),
                        }
                    )
                    + "\n"
                )
        except Exception:  # pylint: disable=broad-except
            logging.debug("Unable to append DBus gateway command lifecycle event", exc_info=True)

    def _prune_lifecycle(self, now: float) -> None:
        cutoff = now - 60.0
        while self._lifecycle_events and self._lifecycle_events[0][0] < cutoff:
            self._lifecycle_events.popleft()

    def _lifecycle_counts_60s(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _at, state, _queue_class in self._lifecycle_events:
            counts[state] = counts.get(state, 0) + 1
        return dict(sorted(counts.items()))

    def set_remote_value(self, command: Mapping[str, Any]) -> CommandOutcome:
        service = str(command.get("service") or "")
        path = str(command.get("path") or "")
        if not service or not path:
            return "dropped"

        def _write() -> None:
            obj = self.adapter.connection.bus().get_object(service, path, introspect=False)
            iface = dbus.Interface(obj, "com.victronenergy.BusItem")
            iface.SetValue(command.get("value"), timeout=float(command.get("timeout", 1.0)))

        self.adapter._timed("write", _write)
        self.adapter.cache.update_value(
            dbus_path_key(service, path),
            command.get("value"),
            source=f"{service}{path}",
            confidence=0.9,
        )
        return "applied"


def _priority_rank(priority: object) -> int:
    return _PRIORITY_RANKS.get(str(priority or "diagnostic").strip().lower(), _PRIORITY_RANKS["diagnostic"])


def _float_or_zero(value: object) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _has_startup_registration(*, commands: list[tuple[str, dict[str, Any]]]) -> bool:
    return any(
        str(command.get("kind") or command.get("type") or "") in {"register_path", "register_service"}
        for _path, command in commands
    )


def _is_local_publish_command(command: Mapping[str, Any]) -> bool:
    return str(command.get("kind") or command.get("type") or "") in {"publish_value", "publish_desired"}
