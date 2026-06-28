# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway write-scheduler mixins."""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from collections.abc import Mapping

import dbus

from venus_evcharger.core.shared import compact_json, config_get_float
from venus_evcharger.dbus_adapter_components import CommandOutcome
from venus_evcharger.dbus_adapter_write_protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter_write_support import (
    float_or_zero,
    lifecycle_payload,
    priority_rank,
)
from venus_evcharger.dbus_gateway import command_queue_class, dbus_path_key
from venus_evcharger.dbus_gateway_command_types import CommandFileList, CommandMapping, CommandPayload

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

_AGED_REFRESH_SECONDS = 15.0
_AGED_REFRESH_PRIORITY_RANK = 1.5
_AGING_QUEUE_CLASSES = {"read-fast", "read-slow", "discovery", "introspection"}


class DbusWriteSchedulerHealthMixin:
    adapter: DbusWriteSchedulerAdapter
    base_queue_class_budgets: dict[str, int]
    dynamic_local_publish_burst_limit: int
    last_processed_at: float
    local_publish_burst_limit: int
    local_publish_tick_budget_seconds: float
    queue_class_budgets: dict[str, int]
    startup_registration_batch_limit: int
    startup_registration_tick_budget_seconds: float
    _budget_events: deque[tuple[float, str]]
    _lifecycle_counts: dict[str, int]
    _lifecycle_events: deque[tuple[float, str, str]]
    _processed_events: deque[float]

    def health(self, *, now: float | None = None) -> CommandPayload:  # pragma: no mutate block
        current = time.time() if now is None else float(now)  # pragma: no mutate
        self.prune_processed(current)
        self.prune_lifecycle(current)
        return {
            "processed_commands_60s": len(self._processed_events),  # pragma: no mutate
            "last_processed_at": self.last_processed_at,  # pragma: no mutate
            "local_publish_burst_limit": self.local_publish_burst_limit,  # pragma: no mutate
            "dynamic_local_publish_burst_limit": self.dynamic_local_publish_burst_limit,  # pragma: no mutate
            "local_publish_tick_budget_ms": self.local_publish_tick_budget_seconds * 1000.0,  # pragma: no mutate
            "startup_registration_batch_limit": self.startup_registration_batch_limit,  # pragma: no mutate
            "startup_registration_tick_budget_ms": self.startup_registration_tick_budget_seconds * 1000.0,  # pragma: no mutate
            "queue_class_budgets": dict(sorted(self.queue_class_budgets.items())),  # pragma: no mutate
            "queue_class_usage_1s": self.queue_class_usage_1s(),  # pragma: no mutate
            "lifecycle_counts": dict(sorted(self._lifecycle_counts.items())),  # pragma: no mutate
            "lifecycle_counts_60s": self.lifecycle_counts_60s(),  # pragma: no mutate
        }

    def set_dynamic_local_publish_burst(self, burst: int) -> None:  # pragma: no mutate block
        """Adjust local publish capacity while preserving conservative DBus budgets."""
        normalized = max(1, int(burst))  # pragma: no mutate
        self.dynamic_local_publish_burst_limit = normalized  # pragma: no mutate
        self.queue_class_budgets = dict(self.base_queue_class_budgets)  # pragma: no mutate
        if normalized <= self.local_publish_burst_limit:
            return
        self.queue_class_budgets["gui-critical-publish"] = max(
            self.queue_class_budgets.get("gui-critical-publish", 1),  # pragma: no mutate
            normalized,  # pragma: no mutate
        )
        self.queue_class_budgets["local-publish"] = max(
            self.queue_class_budgets.get("local-publish", 1),  # pragma: no mutate
            normalized,  # pragma: no mutate
        )

    def prune_processed(self, now: float) -> None:  # pragma: no mutate block
        cutoff = now - 60.0  # pragma: no mutate
        while self._processed_events and self._processed_events[0] < cutoff:
            self._processed_events.popleft()

    @staticmethod
    def _queue_class_budgets(defaults: Mapping[str, object]) -> dict[str, int]:  # pragma: no mutate block
        return {
            "startup/register": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetStartupRegister", 100.0))),  # pragma: no mutate
            "gui-critical-publish": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetGuiCriticalPublish", 50.0))),  # pragma: no mutate
            "local-publish": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetLocalPublish", 30.0))),  # pragma: no mutate
            "remote-write": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetRemoteWrite", 2.0))),  # pragma: no mutate
            "read-fast": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetReadFast", 4.0))),  # pragma: no mutate
            "read-slow": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetReadSlow", 2.0))),  # pragma: no mutate
            "discovery": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetDiscovery", 1.0))),  # pragma: no mutate
            "introspection": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetIntrospection", 1.0))),  # pragma: no mutate
            "diagnostic": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetDiagnostic", 1.0))),  # pragma: no mutate
        }

    def budget_available(self, command: CommandMapping, now: float) -> bool:  # pragma: no mutate block
        queue_class = str(command.get("queue_class") or command_queue_class(command))  # pragma: no mutate
        limit = int(self.queue_class_budgets.get(queue_class, 1))  # pragma: no mutate
        if limit <= 0:
            return False
        return self.budget_usage(queue_class, now) < limit  # pragma: no mutate

    def budget_usage(self, queue_class: str, now: float) -> int:
        return sum(1 for timestamp, item_class in self._budget_events if item_class == queue_class and now - timestamp <= 1.0)  # pragma: no mutate

    def record_budget(self, command: CommandMapping) -> None:
        now = time.time()  # pragma: no mutate
        self._budget_events.append((now, str(command.get("queue_class") or command_queue_class(command))))  # pragma: no mutate
        self.prune_budget(now)

    def prune_budget(self, now: float) -> None:  # pragma: no mutate block
        cutoff = now - 1.0  # pragma: no mutate
        while self._budget_events and self._budget_events[0][0] < cutoff:
            self._budget_events.popleft()

    def queue_class_usage_1s(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _timestamp, queue_class in self._budget_events:
            counts[queue_class] = counts.get(queue_class, 0) + 1  # pragma: no mutate
        return dict(sorted(counts.items()))  # pragma: no mutate

    @staticmethod
    def prioritized_commands(commands: CommandFileList) -> CommandFileList:  # pragma: no mutate block
        now = time.time()  # pragma: no mutate
        return sorted(
            commands,
            key=lambda item: (
                effective_command_priority_rank(item[1], now),  # pragma: no mutate
                _QUEUE_CLASS_RANKS.get(str(item[1].get("queue_class") or command_queue_class(item[1])), 99),  # pragma: no mutate
                float_or_zero(item[1].get("created_at")),  # pragma: no mutate
            ),
        )

    def record_lifecycle(self, command: CommandMapping, state: str) -> None:  # pragma: no mutate block
        now = time.time()  # pragma: no mutate
        queue_class = str(command.get("queue_class") or command_queue_class(command))  # pragma: no mutate
        normalized_state = str(state or "unknown")  # pragma: no mutate
        self._lifecycle_counts[normalized_state] = self._lifecycle_counts.get(normalized_state, 0) + 1  # pragma: no mutate
        self._lifecycle_events.append((now, normalized_state, queue_class))  # pragma: no mutate
        self.prune_lifecycle(now)
        self._append_lifecycle_event(command, normalized_state, queue_class, now)

    def _append_lifecycle_event(
        self,
        command: CommandMapping,
        state: str,
        queue_class: str,
        now: float,
    ) -> None:  # pragma: no mutate block
        path = str(self.adapter.command_lifecycle_path or "")  # pragma: no mutate
        if not path:
            return
        try:
            self._ensure_lifecycle_directory(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(compact_json(lifecycle_payload(command, state, queue_class, now)) + "\n")  # pragma: no mutate
        except (OSError, TypeError, ValueError):
            logging.debug("Unable to append DBus gateway command lifecycle event", exc_info=True)

    @staticmethod
    def _ensure_lifecycle_directory(path: str) -> None:
        directory = os.path.dirname(path)  # pragma: no mutate
        if directory:
            os.makedirs(directory, exist_ok=True)

    def prune_lifecycle(self, now: float) -> None:  # pragma: no mutate block
        cutoff = now - 60.0  # pragma: no mutate
        while self._lifecycle_events and self._lifecycle_events[0][0] < cutoff:
            self._lifecycle_events.popleft()

    def lifecycle_counts_60s(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _at, state, _queue_class in self._lifecycle_events:
            counts[state] = counts.get(state, 0) + 1  # pragma: no mutate
        return dict(sorted(counts.items()))  # pragma: no mutate

    def set_remote_value(self, command: CommandMapping) -> CommandOutcome:  # pragma: no mutate block
        target = remote_command_target(command)
        if target is None:
            return "dropped"
        service, path = target
        value = command.get("value")

        self.adapter.timed_dbus_operation(
            "write",
            lambda: self._write_remote_value(service, path, value, remote_command_timeout(command)),
        )
        self.adapter.cache.update_value(
            dbus_path_key(service, path),  # pragma: no mutate
            value,  # pragma: no mutate
            source=f"{service}{path}",  # pragma: no mutate
            confidence=0.9,  # pragma: no mutate
        )
        return "applied"  # pragma: no mutate

    def _write_remote_value(self, service: str, path: str, value: object, timeout: float) -> None:
        obj = self.adapter.connection.bus().get_object(service, path, introspect=False)  # pragma: no mutate
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")  # pragma: no mutate
        iface.SetValue(value, timeout=timeout)  # pragma: no mutate


def effective_command_priority_rank(command: CommandMapping, now: float) -> float:
    rank = float(priority_rank(command.get("priority")))  # pragma: no mutate
    if aged_refresh_command(command, now):
        return min(rank, _AGED_REFRESH_PRIORITY_RANK)  # pragma: no mutate
    return rank  # pragma: no mutate


def aged_refresh_command(command: CommandMapping, now: float) -> bool:
    queue_class = str(command.get("queue_class") or command_queue_class(command))  # pragma: no mutate
    created_at = float_or_zero(command.get("created_at"))  # pragma: no mutate
    return queue_class in _AGING_QUEUE_CLASSES and created_at > 0.0 and now - created_at >= _AGED_REFRESH_SECONDS  # pragma: no mutate


def remote_command_target(command: CommandMapping) -> tuple[str, str] | None:
    service = str(command.get("service") or "")  # pragma: no mutate
    path = str(command.get("path") or "")  # pragma: no mutate
    return (service, path) if service and path else None


def remote_command_timeout(command: CommandMapping) -> float:
    return float_or_zero(command.get("timeout")) or 1.0
