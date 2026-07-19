# SPDX-License-Identifier: GPL-3.0-or-later
"""Health-aware budgets and remote DBus writes."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Mapping

import dbus

from venus_evcharger.core.shared import config_get_float
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.health.slo import GatewayPressureState, pressure_limited_queue_budgets
from venus_evcharger.dbus_adapter.jsonl import append_jsonl
from venus_evcharger.dbus_adapter.write.protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter.write.publish import DbusWriteSchedulerPublish
from venus_evcharger.dbus_adapter.write.support import (
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
UNKNOWN_QUEUE_CLASS_RANK = max(_QUEUE_CLASS_RANKS.values()) + 1


class DbusWriteSchedulerHealth(DbusWriteSchedulerPublish):
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

    def health(self, *, now: float | None = None) -> CommandPayload:
        current = time.time() if now is None else float(now)
        self.prune_processed(current)
        self.prune_lifecycle(current)
        return {
            "processed_commands_60s": len(self._processed_events),
            "last_processed_at": self.last_processed_at,
            "local_publish_burst_limit": self.local_publish_burst_limit,
            "dynamic_local_publish_burst_limit": self.dynamic_local_publish_burst_limit,
            "local_publish_tick_budget_ms": self.local_publish_tick_budget_seconds * 1000.0,
            "startup_registration_batch_limit": self.startup_registration_batch_limit,
            "startup_registration_tick_budget_ms": self.startup_registration_tick_budget_seconds * 1000.0,
            "queue_class_budgets": dict(sorted(self.queue_class_budgets.items())),
            "queue_class_usage_1s": self.queue_class_usage_1s(),
            "lifecycle_counts": dict(sorted(self._lifecycle_counts.items())),
            "lifecycle_counts_60s": self.lifecycle_counts_60s(),
        }

    def set_dynamic_local_publish_burst(self, burst: int, *, pressure_state: GatewayPressureState = "ok") -> None:
        """Adjust local publish capacity while preserving conservative DBus budgets."""
        normalized = max(1, int(burst))
        self.dynamic_local_publish_burst_limit = normalized
        self.queue_class_budgets = dict(self.base_queue_class_budgets)
        if normalized > self.local_publish_burst_limit:
            self.queue_class_budgets["gui-critical-publish"] = max(
                self.queue_class_budgets["gui-critical-publish"],
                normalized,
            )
            self.queue_class_budgets["local-publish"] = max(
                self.queue_class_budgets["local-publish"],
                normalized,
            )
        self.queue_class_budgets = pressure_limited_queue_budgets(
            self.queue_class_budgets,
            base_local_publish_burst=self.local_publish_burst_limit,
            pressure_state=pressure_state,
        )
        if pressure_state == "ok":
            return
        self.dynamic_local_publish_burst_limit = min(
            self.dynamic_local_publish_burst_limit,
            max(1, int(self.queue_class_budgets["gui-critical-publish"])),
        )

    def prune_processed(self, now: float) -> None:
        cutoff = now - 60.0
        while self._processed_events and self._processed_events[0] < cutoff:
            self._processed_events.popleft()

    @staticmethod
    def _queue_class_budgets(defaults: Mapping[str, object]) -> dict[str, int]:
        return {
            "startup/register": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetStartupRegister", 100.0))),
            "gui-critical-publish": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetGuiCriticalPublish", 50.0))),
            "local-publish": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetLocalPublish", 30.0))),
            "remote-write": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetRemoteWrite", 2.0))),
            "read-fast": max(1, int(config_get_float(defaults, "DbusGatewayQueueBudgetReadFast", 4.0))),
            "read-slow": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetReadSlow", 2.0))),
            "discovery": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetDiscovery", 1.0))),
            "introspection": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetIntrospection", 1.0))),
            "diagnostic": max(0, int(config_get_float(defaults, "DbusGatewayQueueBudgetDiagnostic", 1.0))),
        }

    def budget_available(self, command: CommandMapping, now: float) -> bool:
        queue_class = str(command.get("queue_class") or command_queue_class(command))
        limit = int(self.queue_class_budgets.get(queue_class, 1))
        return self.budget_usage(queue_class, now) < limit

    def budget_usage(self, queue_class: str, now: float) -> int:
        return sum(1 for timestamp, item_class in self._budget_events if item_class == queue_class and now - timestamp <= 1.0)

    def record_budget(self, command: CommandMapping) -> None:
        now = time.time()
        self._budget_events.append((now, str(command.get("queue_class") or command_queue_class(command))))
        self.prune_budget(now)

    def prune_budget(self, now: float) -> None:
        cutoff = now - 1.0
        while self._budget_events and self._budget_events[0][0] < cutoff:
            self._budget_events.popleft()

    def queue_class_usage_1s(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _timestamp, queue_class in self._budget_events:
            counts[queue_class] = counts.get(queue_class, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def prioritized_commands(commands: CommandFileList) -> CommandFileList:
        now = time.time()
        return sorted(
            commands,
            key=lambda item: (
                effective_command_priority_rank(item[1], now),
                _QUEUE_CLASS_RANKS.get(
                    str(item[1].get("queue_class") or command_queue_class(item[1])),
                    UNKNOWN_QUEUE_CLASS_RANK,
                ),
                float_or_zero(item[1].get("created_at")),
            ),
        )

    def record_lifecycle(self, command: CommandMapping, state: str) -> None:
        now = time.time()
        queue_class = str(command.get("queue_class") or command_queue_class(command))
        normalized_state = str(state or "unknown")
        self._lifecycle_counts[normalized_state] = self._lifecycle_counts.get(normalized_state, 0) + 1
        self._lifecycle_events.append((now, normalized_state, queue_class))
        self.prune_lifecycle(now)
        self._append_lifecycle_event(command, normalized_state, queue_class, now)

    def _append_lifecycle_event(
        self,
        command: CommandMapping,
        state: str,
        queue_class: str,
        now: float,
    ) -> None:
        path = str(self.adapter.command_lifecycle_path or "")
        if not path:
            return
        try:
            append_jsonl(
                path,
                lifecycle_payload(command, state, queue_class, now),
                max_bytes=max(0, int(self.adapter.command_lifecycle_max_bytes)),
            )
        except (OSError, TypeError, ValueError):
            logging.debug("Unable to append DBus gateway command lifecycle event", exc_info=True)

    def prune_lifecycle(self, now: float) -> None:
        cutoff = now - 60.0
        while self._lifecycle_events and self._lifecycle_events[0][0] < cutoff:
            self._lifecycle_events.popleft()

    def lifecycle_counts_60s(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _at, state, _queue_class in self._lifecycle_events:
            counts[state] = counts.get(state, 0) + 1
        return dict(sorted(counts.items()))

    def set_remote_value(self, command: CommandMapping) -> CommandOutcome:
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
            dbus_path_key(service, path),
            value,
            source=f"{service}{path}",
            confidence=0.9,
        )
        return "applied"

    def _write_remote_value(self, service: str, path: str, value: object, timeout: float) -> None:
        obj = self.adapter.connection.get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")
        iface.SetValue(value, timeout=timeout)


def effective_command_priority_rank(command: CommandMapping, now: float) -> float:
    rank = float(priority_rank(command.get("priority")))
    if aged_refresh_command(command, now):
        return min(rank, _AGED_REFRESH_PRIORITY_RANK)
    return rank


def aged_refresh_command(command: CommandMapping, now: float) -> bool:
    queue_class = str(command.get("queue_class") or command_queue_class(command))
    created_at = float_or_zero(command.get("created_at"))
    return queue_class in _AGING_QUEUE_CLASSES and created_at > 0.0 and now - created_at >= _AGED_REFRESH_SECONDS


def remote_command_target(command: CommandMapping) -> tuple[str, str] | None:
    service = str(command.get("service") or "")
    path = str(command.get("path") or "")
    return (service, path) if service and path else None


def remote_command_timeout(command: CommandMapping) -> float:
    return float_or_zero(command.get("timeout")) or 1.0
