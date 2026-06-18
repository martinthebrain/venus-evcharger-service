#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_adapter_process_protocols import DbusAdapterHealthContext
from venus_evcharger.dbus_gateway import (
    FAST_READ_KEYS,
    DbusCommandInbox,
    command_queue_class,
    dbus_path_key,
)

LIVE_GUI_FRESHNESS_PATHS = {
    "/Ac/Power",
    "/Ac/Current",
}
ACTIVE_SESSION_GUI_FRESHNESS_PATHS = {
    "/Ac/Energy/Forward",
    "/Session/Time",
    "/Session/Energy",
}
BACKPRESSURE_SLO_REASONS = {"core_reads_fresh", "queue_age_ok"}
SESSION_ACTIVE_POWER_WATTS = 50.0
SESSION_ACTIVE_CURRENT_AMPS = 0.2


class DbusAdapterHealthMixin:
    def _append_health_log(self: DbusAdapterHealthContext, health: Mapping[str, Any]) -> None:  # pragma: no mutate block
        if not self._health_log_due():
            return
        self._last_health_log_monotonic = time.monotonic()
        try:
            _ensure_parent_dir(self.health_log_path)
            payload = _health_log_payload(health)
            with open(self.health_log_path, "a", encoding="utf-8") as handle:
                handle.write(compact_json(payload) + "\n")
        except Exception:  # pylint: disable=broad-except
            logging.debug("Unable to append DBus gateway health history", exc_info=True)

    def _health_log_due(self: DbusAdapterHealthContext) -> bool:  # pragma: no mutate block
        if not self.health_log_path or self.health_log_interval_seconds <= 0.0:
            return False
        return bool(time.monotonic() - self._last_health_log_monotonic >= self.health_log_interval_seconds)

    def _health_snapshot(self: DbusAdapterHealthContext) -> dict[str, Any]:  # pragma: no mutate block
        current_monotonic = time.monotonic()
        current_time = time.time()
        pending = self.commands.load_pending()
        effective_pending = DbusCommandInbox.coalesce(pending)
        core_pending = self.core_commands.load_pending()
        write_scheduler_health = self.write_scheduler.health(now=current_time)
        queue_health = self._queue_health(
            effective_pending,
            core_pending,
            current_time,
            physical_count=len(pending),
            write_scheduler_health=write_scheduler_health,
        )
        cache_freshness = self._cache_freshness(current_time)
        slo = self._slo_snapshot(
            queue_health=queue_health,
            cache_freshness=cache_freshness,
            now=current_time,
            current_monotonic=current_monotonic,
        )
        heartbeat_age = (
            max(0.0, current_monotonic - self._last_tick_monotonic)
            if self._last_tick_monotonic > 0.0
            else 0.0
        )
        return {
            **self.circuit.health(),
            "pending_command_count": len(effective_pending),
            "physical_command_count": len(pending),
            "core_command_count": len(core_pending),
            "registered_path_count": len(self.write_scheduler.registered_paths),
            "last_tick_at": self._last_tick_at,
            "tick_duration_ms": self._last_tick_duration_ms,
            "discovery_last_success_at": self.discovery.last_success_at,
            "discovery_last_error": self.discovery.last_error,
            "discovery_next_scan_at": self.discovery.next_scan_at,
            "mainloop_heartbeat_age_s": heartbeat_age,
            "queues": queue_health,
            "queue_classes": self._queue_class_health(effective_pending, current_time),
            "write_scheduler": write_scheduler_health,
            "cache_freshness": cache_freshness,
            "slo": slo,
            "backpressure": self._backpressure_snapshot(slo=slo, queue_health=queue_health),
            "resources": self._last_resource_snapshot or self.resource_monitor.snapshot(),
            "adaptive_tick_seconds": self.tick_seconds,
            "min_tick_seconds": self.min_tick_seconds,
            "max_tick_seconds": self.max_tick_seconds,
            "eventloop": {
                "last_tick_at": self._last_tick_at,
                "tick_duration_ms": self._last_tick_duration_ms,
                "mainloop_heartbeat_age_s": heartbeat_age,
                **self.tick_health.snapshot(now=current_monotonic),
            },
        }

    @staticmethod
    def _queue_class_health(pending: list[tuple[str, dict[str, Any]]], now: float) -> dict[str, Any]:  # pragma: no mutate block
        classes: dict[str, dict[str, Any]] = {}
        for _path, command in pending:
            queue_class = str(command.get("queue_class") or command_queue_class(command))
            entry = classes.setdefault(queue_class, {"pending": 0, "oldest_age_s": 0.0})
            entry["pending"] = int(entry["pending"]) + 1
            entry["oldest_age_s"] = max(
                float(entry["oldest_age_s"]),
                0.0,
                now - DbusAdapterHealthMixin._command_activity_at(command, now),
            )
        return dict(sorted(classes.items()))

    @staticmethod
    def _queue_health(
        pending: list[tuple[str, dict[str, Any]]],
        core_pending: list[tuple[str, dict[str, Any]]],
        now: float,
        *,
        physical_count: int | None = None,
        write_scheduler_health: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:  # pragma: no mutate block
        scheduler = write_scheduler_health or {}
        return {
            "pending_command_count": len(pending),
            "physical_command_count": _physical_command_count(pending, physical_count),
            "oldest_command_age_s": DbusAdapterHealthMixin._oldest_command_age(pending, now),
            "core_command_count": len(core_pending),
            "oldest_core_command_age_s": DbusAdapterHealthMixin._oldest_command_age(core_pending, now),
            "processed_commands_60s": int(scheduler.get("processed_commands_60s", 0) or 0),
            "queue_drain_rate_per_s": float(scheduler.get("processed_commands_60s", 0) or 0) / 60.0,
            "last_processed_at": float(scheduler.get("last_processed_at", 0.0) or 0.0),
        }

    @staticmethod
    def _oldest_command_age(commands: list[tuple[str, dict[str, Any]]], now: float) -> float:  # pragma: no mutate block
        ages = [
            max(0.0, now - DbusAdapterHealthMixin._command_activity_at(command, now))
            for _path, command in commands
        ]
        return max(ages) if ages else 0.0

    @staticmethod
    def _command_activity_at(command: Mapping[str, Any], now: float) -> float:  # pragma: no mutate block
        timestamp = command.get("updated_at") if command.get("updated_at") is not None else command.get("created_at")
        try:
            return float(timestamp if timestamp is not None else now)
        except (TypeError, ValueError):
            return now

    def _cache_freshness(self: DbusAdapterHealthContext, now: float) -> dict[str, Any]:  # pragma: no mutate block
        values = {
            key: self.cache._value_snapshot(value, now)  # pylint: disable=protected-access
            for key, value in self.cache.values.items()
        }
        return {"value_count": len(values), "status_counts": _status_counts(values), **_important_freshness(values)}

    def _slo_snapshot(
        self: DbusAdapterHealthContext,
        *,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, Any]:  # pragma: no mutate block
        observed = self._slo_observed(queue_health, cache_freshness, now, current_monotonic)
        checks = self._slo_checks_from_observed(observed)
        return _slo_payload(checks, self._slo_targets(), observed)

    def _slo_observed(
        self: DbusAdapterHealthContext,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, float]:  # pragma: no mutate block
        eventloop = self.tick_health.snapshot(now=current_monotonic)
        return {
            "gui_max_age_s": self._max_cached_path_age(self._gui_freshness_paths(now), now),
            "core_read_max_age_s": self._max_core_read_age(cache_freshness),
            "queue_oldest_age_s": float(queue_health.get("oldest_command_age_s", 0.0) or 0.0),
            "mainloop_max_gap_ms_60s": float(eventloop.get("max_tick_gap_ms_60s", 0.0) or 0.0),
        }

    def _slo_checks_from_observed(
        self: DbusAdapterHealthContext,
        observed: Mapping[str, float],
    ) -> dict[str, bool]:  # pragma: no mutate block
        return self._slo_checks(
            float(observed.get("gui_max_age_s", 0.0)),
            float(observed.get("core_read_max_age_s", 0.0)),
            float(observed.get("queue_oldest_age_s", 0.0)),
            float(observed.get("mainloop_max_gap_ms_60s", 0.0)),
        )

    def _slo_targets(self: DbusAdapterHealthContext) -> dict[str, float]:  # pragma: no mutate block
        return {
            "gui_max_age_s": self.slo_gui_max_age_seconds,
            "core_read_max_age_s": self.slo_core_read_max_age_seconds,
            "queue_max_age_s": self.slo_queue_max_age_seconds,
            "mainloop_gap_max_ms": self._effective_mainloop_gap_max_ms(),
        }

    def _slo_checks(
        self: DbusAdapterHealthContext,
        gui_age: float,
        core_read_age: float,
        queue_age: float,
        eventloop_gap_ms: float,
    ) -> dict[str, bool]:  # pragma: no mutate block
        return {
            "gui_fresh": gui_age <= self._effective_gui_max_age_seconds(),
            "core_reads_fresh": core_read_age <= self.slo_core_read_max_age_seconds,
            "queue_age_ok": queue_age <= self.slo_queue_max_age_seconds,
            "mainloop_gap_ok": eventloop_gap_ms <= self._effective_mainloop_gap_max_ms(),
        }

    def _effective_gui_max_age_seconds(self: DbusAdapterHealthContext) -> float:  # pragma: no mutate block
        return max(self.slo_gui_max_age_seconds, self.slo_core_read_max_age_seconds * 2.0)

    def _effective_mainloop_gap_max_ms(self: DbusAdapterHealthContext) -> float:  # pragma: no mutate block
        adaptive_tick_ms = max(self.tick_seconds, self.max_tick_seconds) * 1000.0
        return max(self.slo_mainloop_gap_max_ms, adaptive_tick_ms * 2.5)

    def _gui_freshness_paths(self: DbusAdapterHealthContext, now: float) -> set[str]:
        paths = set(LIVE_GUI_FRESHNESS_PATHS)
        if self._charging_session_active_for_gui(now):
            paths.update(ACTIVE_SESSION_GUI_FRESHNESS_PATHS)
        return paths

    def _charging_session_active_for_gui(self: DbusAdapterHealthContext, now: float) -> bool:
        return (
            self._fresh_cached_path_float("/Ac/Power", now) >= SESSION_ACTIVE_POWER_WATTS
            or self._fresh_cached_path_float("/Ac/Current", now) >= SESSION_ACTIVE_CURRENT_AMPS
        )

    def _fresh_cached_path_float(self: DbusAdapterHealthContext, path: str, now: float) -> float:
        entry = self.cache.values.get(dbus_path_key(self.service_name, path))
        if _cached_entry_age(entry, now) > self._effective_gui_max_age_seconds():
            return 0.0
        return _cached_entry_float(entry)

    def _backpressure_snapshot(
        self: DbusAdapterHealthContext,
        *,
        slo: Mapping[str, Any],
        queue_health: Mapping[str, Any],
    ) -> dict[str, Any]:  # pragma: no mutate block
        circuit_state = self.circuit.state()
        queue_age = float(queue_health.get("oldest_command_age_s", 0.0) or 0.0)
        reasons = self._backpressure_reasons(circuit_state, queue_age, slo)
        state = self._backpressure_state(circuit_state, queue_age, reasons)
        return {
            "state": state,
            "core_should_throttle": state != "ok",
            "suppress_optional_commands": state in {"slow", "protective"},
            "prefer_coalescing": state != "ok",
            "reason": ",".join(dict.fromkeys(reasons)) if reasons else "ok",
        }

    def _backpressure_reasons(
        self: DbusAdapterHealthContext,
        circuit_state: str,
        queue_age: float,
        slo: Mapping[str, Any],
    ) -> list[str]:  # pragma: no mutate block
        reasons = [f"dbus-{circuit_state}"] if circuit_state != "ok" else []
        if queue_age > self.slo_queue_max_age_seconds:
            reasons.append("queue-age")
        reasons.extend(self._backpressure_slo_reasons(slo))
        return reasons

    @staticmethod
    def _backpressure_slo_reasons(slo: Mapping[str, Any]) -> list[str]:  # pragma: no mutate block
        return [str(item) for item in list(slo.get("violated", []) or []) if item in BACKPRESSURE_SLO_REASONS]

    def _backpressure_state(
        self: DbusAdapterHealthContext,
        circuit_state: str,
        queue_age: float,
        reasons: list[str],
    ) -> str:  # pragma: no mutate block
        if circuit_state == "protective":
            return "protective"
        if circuit_state == "degraded" or queue_age > self.slo_queue_max_age_seconds * 2.0:
            return "slow"
        return "congested" if reasons else "ok"

    def _apply_slo_regulation(self: DbusAdapterHealthContext) -> None:  # pragma: no mutate block
        now = time.time()
        pending = DbusCommandInbox.coalesce(self.commands.load_pending())
        queue_age = self._oldest_command_age(pending, now)
        cache_freshness = self._cache_freshness(now)
        core_read_age = self._max_core_read_age(cache_freshness)
        eventloop_gap_ms = float(self.tick_health.snapshot().get("max_tick_gap_ms_60s", 0.0) or 0.0)
        self.write_scheduler.set_dynamic_local_publish_burst(self._regulated_publish_burst(queue_age, eventloop_gap_ms))
        if core_read_age > self.slo_core_read_max_age_seconds:
            self.read_scheduler.force_due(self._stale_core_read_keys(cache_freshness))
        if self.circuit.state() != "ok":
            self._quiet_discovery_and_introspection(now)

    def _regulated_publish_burst(
        self: DbusAdapterHealthContext,
        queue_age: float,
        eventloop_gap_ms: float,
    ) -> int:  # pragma: no mutate block
        burst = self.write_scheduler.local_publish_burst_limit
        if queue_age > self.slo_queue_max_age_seconds:
            burst = min(max(burst * 3, burst + 4), 50)
        if eventloop_gap_ms > self._effective_mainloop_gap_max_ms():
            burst = max(1, min(burst, max(1, self.write_scheduler.local_publish_burst_limit // 2)))
        return int(burst)

    def _quiet_discovery_and_introspection(self: DbusAdapterHealthContext, now: float) -> None:  # pragma: no mutate block
        quiet_until = now + 60.0
        self.discovery.next_scan_at = max(self.discovery.next_scan_at, quiet_until)
        self._last_introspection_full_scan_at = max(self._last_introspection_full_scan_at, now)

    def _max_cached_path_age(self: DbusAdapterHealthContext, paths: set[str], now: float) -> float:  # pragma: no mutate block
        ages = [_cached_entry_age(self.cache.values.get(dbus_path_key(self.service_name, path)), now) for path in paths]
        ages = [age for age in ages if age > 0.0]
        return max(ages) if ages else 0.0

    @staticmethod
    def _max_core_read_age(cache_freshness: Mapping[str, Any]) -> float:  # pragma: no mutate block
        ages = [
            float(cache_freshness.get(f"{key}_age_s", 0.0) or 0.0)
            for key in ("grid_power_w", "pv_power_w", "battery_soc")
            if f"{key}_age_s" in cache_freshness
        ]
        return max(ages) if ages else 0.0

    def _stale_core_read_keys(self: DbusAdapterHealthContext, cache_freshness: Mapping[str, Any]) -> set[str]:
        return {
            key
            for key in FAST_READ_KEYS
            if self._core_read_stale(key, cache_freshness)
        }

    def _core_read_stale(self: DbusAdapterHealthContext, key: str, cache_freshness: Mapping[str, Any]) -> bool:
        status_key = f"{key}_status"
        age_key = f"{key}_age_s"
        if status_key not in cache_freshness or age_key not in cache_freshness:
            return True
        if str(cache_freshness[status_key]) != "fresh":
            return True
        return float(cache_freshness[age_key] or 0.0) > self.slo_core_read_max_age_seconds

    @staticmethod
    def _json_ready(value: Any) -> Any:  # pragma: no mutate block
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)



def _ensure_parent_dir(path: str) -> None:  # pragma: no mutate block
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _mapping_child(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:  # pragma: no mutate block
    value = parent.get(key)
    return value if isinstance(value, Mapping) else {}


def _health_log_payload(health: Mapping[str, Any]) -> dict[str, Any]:  # pragma: no mutate block
    queues = _mapping_child(health, "queues")
    eventloop = _mapping_child(health, "eventloop")
    cache_freshness = _mapping_child(health, "cache_freshness")
    backpressure = _mapping_child(health, "backpressure")
    return {
        "at": time.time(),
        "state": health.get("state", "unknown"),
        "backpressure": backpressure.get("state", "unknown"),
        "queue_oldest_age_s": queues.get("oldest_command_age_s", 0.0),
        "core_queue_oldest_age_s": queues.get("oldest_core_command_age_s", 0.0),
        "max_tick_gap_ms_60s": eventloop.get("max_tick_gap_ms_60s", 0.0),
        "timeouts_60s": health.get("timeouts_60s", 0),
        "cache_freshness": _health_log_cache_freshness(cache_freshness),
    }


def _health_log_cache_freshness(cache_freshness: Mapping[str, Any]) -> dict[str, Any]:  # pragma: no mutate block
    return {
        key: cache_freshness.get(key)
        for key in (
            "grid_power_w_age_s",
            "grid_power_w_status",
            "pv_power_w_age_s",
            "pv_power_w_status",
            "battery_soc_age_s",
            "battery_soc_status",
        )
    }


def _physical_command_count(pending: list[tuple[str, dict[str, Any]]], physical_count: int | None) -> int:  # pragma: no mutate block
    return len(pending) if physical_count is None else int(physical_count)


def _status_counts(values: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:  # pragma: no mutate block
    counts: dict[str, int] = {}
    for value in values.values():
        status = str(value.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _important_freshness(values: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:  # pragma: no mutate block
    important: dict[str, Any] = {
        f"{key}_age_s": float(values.get(key, {}).get("age_s", 0.0) or 0.0) for key in FAST_READ_KEYS
    }
    important.update({f"{key}_status": str(values.get(key, {}).get("status", "missing")) for key in FAST_READ_KEYS})
    return important


def _cached_entry_age(entry: object, now: float) -> float:  # pragma: no mutate block
    if not isinstance(entry, Mapping):
        return 0.0
    updated_at = float(entry.get("updated_at", 0.0) or 0.0)
    return max(0.0, now - updated_at) if updated_at > 0.0 else 0.0


def _cached_entry_float(entry: object) -> float:  # pragma: no mutate block
    if not isinstance(entry, Mapping):
        return 0.0
    try:
        return float(entry.get("value", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _slo_payload(
    checks: Mapping[str, bool],
    targets: Mapping[str, float],
    observed: Mapping[str, float],
) -> dict[str, Any]:  # pragma: no mutate block
    violated = [name for name, ok in checks.items() if not ok]
    return {
        "state": "violated" if violated else "ok",
        "violated": violated,
        "checks": dict(checks),
        "targets": dict(targets),
        "observed": dict(observed),
    }
