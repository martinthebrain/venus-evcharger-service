#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process health publishing.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from venus_evcharger.dbus_adapter.health.backpressure import backpressure_snapshot
from venus_evcharger.dbus_adapter.health.freshness import (
    cache_freshness,
    max_publication_field_age,
    missing_publication_field_count,
    publication_field_age,
    publication_field_float,
)
from venus_evcharger.dbus_adapter.health.gui import (
    ACTIVE_SESSION_GUI_FRESHNESS_FIELDS,
    GUI_CONTROL_FRESHNESS_FIELDS,
    GUI_MEASUREMENT_FRESHNESS_FIELDS,
)
from venus_evcharger.dbus_adapter.health.history import append_health_log
from venus_evcharger.dbus_adapter.health.queue import oldest_command_age, queue_class_health, queue_health
from venus_evcharger.dbus_adapter.health.slo import (
    SloThresholds,
    effective_gui_max_age_seconds,
    max_core_read_age,
    regulated_publish_burst,
    runtime_pressure_state,
    slo_checks_from_observed,
    slo_payload,
    slo_targets,
    stale_core_read_keys,
)
from venus_evcharger.dbus_adapter.process.diagnostics import DbusAdapterDiagnostics
from venus_evcharger.dbus_adapter.process.protocols.health import DbusAdapterHealthContext
from venus_evcharger.dbus_adapter.read.keys import CORE_ENERGY_READ_KEYS
from venus_evcharger.dbus_gateway_core import float_or_zero
from venus_evcharger.ipc.command_types import CommandPayload

SESSION_ACTIVE_POWER_WATTS = 50.0
SESSION_ACTIVE_CURRENT_AMPS = 0.2


class DbusAdapterHealth(DbusAdapterDiagnostics):
    def append_health_log(self: DbusAdapterHealthContext, health: Mapping[str, object]) -> None:
        if not self.health_log_due():
            return
        self._last_health_log_monotonic = time.monotonic()
        try:
            append_health_log(self.health_log_path, health, max_bytes=self.health_log_max_bytes)
        except (OSError, RuntimeError, TypeError, ValueError):
            logging.debug("Unable to append DBus gateway health history", exc_info=True)

    def health_log_due(self: DbusAdapterHealthContext) -> bool:
        if not self.health_log_path or self.health_log_interval_seconds <= 0.0:
            return False
        return bool(time.monotonic() - self._last_health_log_monotonic >= self.health_log_interval_seconds)

    def health_snapshot(self: DbusAdapterHealthContext) -> CommandPayload:
        current_monotonic = time.monotonic()
        current_time = time.time()
        pending = self.commands.load_pending()
        effective_pending = self.commands.coalesce(pending)
        core_pending = self.core_command_mailbox.load_pending()
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
            "registered_path_count": self.registered_publication_path_count,
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

    def cache_freshness_snapshot(self: DbusAdapterHealthContext, now: float) -> CommandPayload:
        return cache_freshness(self.cache, now)

    def slo_snapshot(
        self: DbusAdapterHealthContext,
        *,
        queue_health: Mapping[str, object],
        cache_freshness: Mapping[str, object],
        now: float,
        current_monotonic: float,
    ) -> CommandPayload:
        observed = self.slo_observed(queue_health, cache_freshness, now, current_monotonic)
        thresholds = self.slo_thresholds()
        checks = slo_checks_from_observed(observed, thresholds)
        return slo_payload(checks, slo_targets(thresholds), observed)

    def slo_thresholds(self: DbusAdapterHealthContext) -> SloThresholds:
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
        queue_health: Mapping[str, object],
        cache_freshness: Mapping[str, object],
        now: float,
        current_monotonic: float,
    ) -> dict[str, float]:
        eventloop = self.tick_health.snapshot(now=current_monotonic)
        measurement_age = self.max_publication_field_age(GUI_MEASUREMENT_FRESHNESS_FIELDS, now)
        control_age = self.max_publication_field_age(GUI_CONTROL_FRESHNESS_FIELDS, now)
        session_fields = self.gui_session_freshness_fields(now)
        session_age = self.max_publication_field_age(session_fields, now)
        return {
            "gui_max_age_s": max(measurement_age, control_age, session_age),
            "gui_measurement_max_age_s": measurement_age,
            "gui_control_max_age_s": control_age,
            "gui_session_max_age_s": session_age,
            "gui_missing_field_count": self.missing_publication_field_count(self.gui_freshness_fields(now)),
            "gui_measurement_missing_field_count": self.missing_publication_field_count(
                GUI_MEASUREMENT_FRESHNESS_FIELDS
            ),
            "gui_control_missing_field_count": self.missing_publication_field_count(GUI_CONTROL_FRESHNESS_FIELDS),
            "gui_session_missing_field_count": self.missing_publication_field_count(session_fields),
            "core_read_max_age_s": max_core_read_age(cache_freshness),
            "queue_oldest_age_s": float_or_zero(queue_health.get("oldest_command_age_s")),
            "mainloop_max_gap_ms_60s": float_or_zero(eventloop.get("max_tick_gap_ms_60s")),
        }

    def gui_freshness_fields(self: DbusAdapterHealthContext, now: float) -> set[str]:
        fields = set(GUI_MEASUREMENT_FRESHNESS_FIELDS | GUI_CONTROL_FRESHNESS_FIELDS)
        fields.update(self.gui_session_freshness_fields(now))
        return fields

    def gui_session_freshness_fields(self: DbusAdapterHealthContext, now: float) -> set[str]:
        return set(ACTIVE_SESSION_GUI_FRESHNESS_FIELDS) if self.charging_session_active_for_gui(now) else set()

    def charging_session_active_for_gui(self: DbusAdapterHealthContext, now: float) -> bool:
        return (
            self.fresh_evcs_field_float("ac_power_w", now) >= SESSION_ACTIVE_POWER_WATTS
            or self.fresh_evcs_field_float("ac_current_a", now) >= SESSION_ACTIVE_CURRENT_AMPS
        )

    def fresh_evcs_field_float(self: DbusAdapterHealthContext, field: str, now: float) -> float:
        observation = self.publication_registry.evcs_field_observation(field)
        if publication_field_age(observation, now) > effective_gui_max_age_seconds(self.slo_thresholds()):
            return 0.0
        return publication_field_float(observation)

    def apply_slo_regulation(self: DbusAdapterHealthContext) -> None:
        now = time.time()
        pending = self.commands.coalesce(self.commands.load_pending())
        queue_age = oldest_command_age(pending, now)
        cache_freshness = self.cache_freshness_snapshot(now)
        core_read_age = max_core_read_age(cache_freshness)
        eventloop_gap_ms = float_or_zero(self.tick_health.snapshot().get("max_tick_gap_ms_60s"))
        thresholds = self.slo_thresholds()
        queue_metrics = {"oldest_command_age_s": queue_age}
        slo = self.slo_snapshot(
            queue_health=queue_metrics,
            cache_freshness=cache_freshness,
            now=now,
            current_monotonic=time.monotonic(),
        )
        backpressure = backpressure_snapshot(
            circuit_state=self.circuit.state(),
            queue_health=queue_metrics,
            slo=slo,
            queue_max_age_seconds=self.slo_queue_max_age_seconds,
        )
        pressure_state = runtime_pressure_state(
            str((self._last_resource_snapshot or {}).get("state", "ok")),
            str(backpressure.get("state", "ok")),
        )
        self.write_scheduler.set_dynamic_local_publish_burst(
            regulated_publish_burst(
                queue_age=queue_age,
                eventloop_gap_ms=eventloop_gap_ms,
                base_burst=self.write_scheduler.local_publish_burst_limit,
                thresholds=thresholds,
                pressure_state=pressure_state,
            ),
            pressure_state=pressure_state,
        )
        if core_read_age > self.slo_core_read_max_age_seconds:
            self.read_scheduler.force_due(
                stale_core_read_keys(
                    cache_freshness,
                    CORE_ENERGY_READ_KEYS,
                    max_age_seconds=self.slo_core_read_max_age_seconds,
                )
            )
        if self.circuit.state() != "ok" or pressure_state in {"slow", "protective"}:
            self.quiet_discovery_and_introspection(now)

    def quiet_discovery_and_introspection(self: DbusAdapterHealthContext, now: float) -> None:
        quiet_until = now + 60.0
        self.discovery.next_scan_at = max(self.discovery.next_scan_at, quiet_until)
        self._last_introspection_full_scan_at = max(self._last_introspection_full_scan_at, now)

    def max_publication_field_age(
        self: DbusAdapterHealthContext,
        fields: set[str] | frozenset[str],
        now: float,
    ) -> float:
        return max_publication_field_age(self.publication_registry, fields, now)

    def missing_publication_field_count(
        self: DbusAdapterHealthContext,
        fields: set[str] | frozenset[str],
    ) -> float:
        return missing_publication_field_count(self.publication_registry, fields)
