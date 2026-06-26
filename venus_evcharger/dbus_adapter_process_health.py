#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from venus_evcharger.dbus_adapter_health_backpressure import backpressure_snapshot
from venus_evcharger.dbus_adapter_health_freshness import (
    cache_freshness,
    cached_entry_age,
    cached_entry_float,
    max_cached_path_age,
    missing_cached_path_count,
)
from venus_evcharger.dbus_adapter_health_gui import (
    ACTIVE_SESSION_GUI_FRESHNESS_PATHS,
    GUI_CONTROL_FRESHNESS_PATHS,
    GUI_MEASUREMENT_FRESHNESS_PATHS,
)
from venus_evcharger.dbus_adapter_health_history import append_health_log
from venus_evcharger.dbus_adapter_health_queue import oldest_command_age, queue_class_health, queue_health
from venus_evcharger.dbus_adapter_health_slo import (
    SloThresholds,
    effective_gui_max_age_seconds,
    max_core_read_age,
    regulated_publish_burst,
    slo_checks_from_observed,
    slo_payload,
    slo_targets,
    stale_core_read_keys,
)
from venus_evcharger.dbus_adapter_process_protocol_health import DbusAdapterHealthContext
from venus_evcharger.dbus_gateway import FAST_READ_KEYS, DbusCommandInbox, dbus_path_key

SESSION_ACTIVE_POWER_WATTS = 50.0
SESSION_ACTIVE_CURRENT_AMPS = 0.2


class DbusAdapterHealthMixin:
    def append_health_log(self: DbusAdapterHealthContext, health: Mapping[str, Any]) -> None:  # pragma: no mutate block
        if not self._health_log_due():
            return
        self._last_health_log_monotonic = time.monotonic()
        try:
            append_health_log(self.health_log_path, health)
        except Exception:  # pylint: disable=broad-except
            logging.debug("Unable to append DBus gateway health history", exc_info=True)

    def _health_log_due(self: DbusAdapterHealthContext) -> bool:  # pragma: no mutate block
        if not self.health_log_path or self.health_log_interval_seconds <= 0.0:
            return False
        return bool(time.monotonic() - self._last_health_log_monotonic >= self.health_log_interval_seconds)

    def health_snapshot(self: DbusAdapterHealthContext) -> dict[str, Any]:  # pragma: no mutate block
        current_monotonic = time.monotonic()
        current_time = time.time()
        pending = self.commands.load_pending()
        effective_pending = DbusCommandInbox.coalesce(pending)
        core_pending = self.core_commands.load_pending()
        write_scheduler_health = self.write_scheduler.health(now=current_time)
        queue_metrics = queue_health(
            effective_pending,
            core_pending,
            current_time,
            physical_count=len(pending),
            write_scheduler_health=write_scheduler_health,
        )
        freshness = self.cache_freshness_snapshot(current_time)
        slo = self.slo_snapshot(
            queue_health=queue_metrics,
            cache_freshness=freshness,
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
            "queues": queue_metrics,
            "queue_classes": queue_class_health(effective_pending, current_time),
            "write_scheduler": write_scheduler_health,
            "cache_freshness": freshness,
            "slo": slo,
            "backpressure": backpressure_snapshot(
                circuit_state=self.circuit.state(),
                queue_health=queue_metrics,
                slo=slo,
                queue_max_age_seconds=self.slo_queue_max_age_seconds,
            ),
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

    def cache_freshness_snapshot(self: DbusAdapterHealthContext, now: float) -> dict[str, Any]:  # pragma: no mutate block
        return cache_freshness(self.cache, now)

    def slo_snapshot(
        self: DbusAdapterHealthContext,
        *,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, Any]:  # pragma: no mutate block
        observed = self.slo_observed(queue_health, cache_freshness, now, current_monotonic)
        thresholds = self.slo_thresholds()
        checks = slo_checks_from_observed(observed, thresholds)
        return slo_payload(checks, slo_targets(thresholds), observed)

    def slo_thresholds(self: DbusAdapterHealthContext) -> SloThresholds:  # pragma: no mutate block
        return SloThresholds(
            gui_max_age_seconds=self.slo_gui_max_age_seconds,
            core_read_max_age_seconds=self.slo_core_read_max_age_seconds,
            queue_max_age_seconds=self.slo_queue_max_age_seconds,
            mainloop_gap_max_ms=self.slo_mainloop_gap_max_ms,
            tick_seconds=self.tick_seconds,
            max_tick_seconds=self.max_tick_seconds,
        )

    def slo_observed(
        self: DbusAdapterHealthContext,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, float]:  # pragma: no mutate block
        eventloop = self.tick_health.snapshot(now=current_monotonic)
        measurement_age = self.max_cached_path_age_for_paths(GUI_MEASUREMENT_FRESHNESS_PATHS, now)
        control_age = self.max_cached_path_age_for_paths(GUI_CONTROL_FRESHNESS_PATHS, now)
        session_paths = self.gui_session_freshness_paths(now)
        session_age = self.max_cached_path_age_for_paths(session_paths, now)
        return {
            "gui_max_age_s": max(measurement_age, control_age, session_age),
            "gui_measurement_max_age_s": measurement_age,
            "gui_control_max_age_s": control_age,
            "gui_session_max_age_s": session_age,
            "gui_missing_path_count": self.missing_cached_path_count_for_paths(self.gui_freshness_paths(now)),
            "gui_measurement_missing_path_count": self.missing_cached_path_count_for_paths(GUI_MEASUREMENT_FRESHNESS_PATHS),
            "gui_control_missing_path_count": self.missing_cached_path_count_for_paths(GUI_CONTROL_FRESHNESS_PATHS),
            "gui_session_missing_path_count": self.missing_cached_path_count_for_paths(session_paths),
            "core_read_max_age_s": max_core_read_age(cache_freshness),
            "queue_oldest_age_s": float(queue_health.get("oldest_command_age_s", 0.0) or 0.0),
            "mainloop_max_gap_ms_60s": float(eventloop.get("max_tick_gap_ms_60s", 0.0) or 0.0),
        }

    def gui_freshness_paths(self: DbusAdapterHealthContext, now: float) -> set[str]:
        paths = set(GUI_MEASUREMENT_FRESHNESS_PATHS | GUI_CONTROL_FRESHNESS_PATHS)
        paths.update(self.gui_session_freshness_paths(now))
        return paths

    def gui_session_freshness_paths(self: DbusAdapterHealthContext, now: float) -> set[str]:
        return set(ACTIVE_SESSION_GUI_FRESHNESS_PATHS) if self.charging_session_active_for_gui(now) else set()

    def charging_session_active_for_gui(self: DbusAdapterHealthContext, now: float) -> bool:
        return (
            self.fresh_cached_path_float("/Ac/Power", now) >= SESSION_ACTIVE_POWER_WATTS
            or self.fresh_cached_path_float("/Ac/Current", now) >= SESSION_ACTIVE_CURRENT_AMPS
        )

    def fresh_cached_path_float(self: DbusAdapterHealthContext, path: str, now: float) -> float:
        entry = self.cache.values.get(dbus_path_key(self.service_name, path))
        if cached_entry_age(entry, now) > effective_gui_max_age_seconds(self.slo_thresholds()):
            return 0.0
        return cached_entry_float(entry)

    def apply_slo_regulation(self: DbusAdapterHealthContext) -> None:  # pragma: no mutate block
        now = time.time()
        pending = DbusCommandInbox.coalesce(self.commands.load_pending())
        queue_age = oldest_command_age(pending, now)
        cache_freshness = self.cache_freshness_snapshot(now)
        core_read_age = max_core_read_age(cache_freshness)
        eventloop_gap_ms = float(self.tick_health.snapshot().get("max_tick_gap_ms_60s", 0.0) or 0.0)
        thresholds = self.slo_thresholds()
        self.write_scheduler.set_dynamic_local_publish_burst(
            regulated_publish_burst(
                queue_age=queue_age,
                eventloop_gap_ms=eventloop_gap_ms,
                base_burst=self.write_scheduler.local_publish_burst_limit,
                thresholds=thresholds,
            )
        )
        if core_read_age > self.slo_core_read_max_age_seconds:
            self.read_scheduler.force_due(
                stale_core_read_keys(
                    cache_freshness,
                    FAST_READ_KEYS,
                    max_age_seconds=self.slo_core_read_max_age_seconds,
                )
            )
        if self.circuit.state() != "ok":
            self.quiet_discovery_and_introspection(now)

    def quiet_discovery_and_introspection(self: DbusAdapterHealthContext, now: float) -> None:  # pragma: no mutate block
        quiet_until = now + 60.0
        self.discovery.next_scan_at = max(self.discovery.next_scan_at, quiet_until)
        self._last_introspection_full_scan_at = max(self._last_introspection_full_scan_at, now)

    def max_cached_path_age_for_paths(self: DbusAdapterHealthContext, paths: set[str], now: float) -> float:  # pragma: no mutate block
        return max_cached_path_age(self.cache.values, self.service_name, paths, now)

    def missing_cached_path_count_for_paths(self: DbusAdapterHealthContext, paths: set[str]) -> float:  # pragma: no mutate block
        return missing_cached_path_count(self.cache.values, self.service_name, paths)
