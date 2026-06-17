# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway write-scheduler mixins."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any

import dbus

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_adapter_components import CommandOutcome
from venus_evcharger.dbus_adapter_write_support import (
    _float_or_zero,
    _lifecycle_payload,
    _priority_rank,
)
from venus_evcharger.dbus_gateway import command_queue_class, dbus_path_key

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
        return self._budget_usage(queue_class, now) < limit

    def _budget_usage(self, queue_class: str, now: float) -> int:
        return sum(1 for timestamp, item_class in self._budget_events if item_class == queue_class and now - timestamp <= 1.0)

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
        now = time.time()
        return sorted(
            commands,
            key=lambda item: (
                _effective_command_priority_rank(item[1], now),
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
        self._append_lifecycle_event(command, normalized_state, queue_class, now)

    def _append_lifecycle_event(
        self,
        command: Mapping[str, Any],
        state: str,
        queue_class: str,
        now: float,
    ) -> None:
        path = str(getattr(self.adapter, "command_lifecycle_path", "") or "")
        if not path:
            return
        try:
            self._ensure_lifecycle_directory(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(compact_json(_lifecycle_payload(command, state, queue_class, now)) + "\n")
        except Exception:  # pylint: disable=broad-except
            logging.debug("Unable to append DBus gateway command lifecycle event", exc_info=True)

    @staticmethod
    def _ensure_lifecycle_directory(path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

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


def _effective_command_priority_rank(command: Mapping[str, Any], now: float) -> float:
    rank = float(_priority_rank(command.get("priority")))
    if _aged_refresh_command(command, now):
        return min(rank, _AGED_REFRESH_PRIORITY_RANK)
    return rank


def _aged_refresh_command(command: Mapping[str, Any], now: float) -> bool:
    queue_class = str(command.get("queue_class") or command_queue_class(command))
    created_at = _float_or_zero(command.get("created_at"))
    return queue_class in _AGING_QUEUE_CLASSES and created_at > 0.0 and now - created_at >= _AGED_REFRESH_SECONDS
