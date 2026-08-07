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
from dataclasses import dataclass

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
from venus_evcharger.dbus_adapter.health.queue import (
    ADVISORY_QUEUE_CLASSES,
    command_queue_class_name,
    queue_class_health,
    queue_health,
)
from venus_evcharger.dbus_adapter.health.slo import (
    GatewayPressureState,
    SloThresholds,
    core_read_missing_count,
    core_read_nonfresh_count,
    effective_gui_max_age_seconds,
    max_core_read_age,
    regulated_publish_burst,
    runtime_pressure_state,
    slo_checks_from_observed,
    slo_payload,
    slo_targets,
    stale_core_read_keys,
)
from venus_evcharger.dbus_adapter.health.state import (
    GatewayHealthStateLatch,
    operational_health_state,
    performance_health_state,
)
from venus_evcharger.dbus_adapter.process.protocols.health import DbusAdapterHealthContext
from venus_evcharger.dbus_adapter.read.keys import CORE_ENERGY_READ_KEYS
from venus_evcharger.dbus_gateway_core import float_or_zero
from venus_evcharger.ipc.command_types import CommandPayload

SESSION_ACTIVE_POWER_WATTS = 50.0
SESSION_ACTIVE_CURRENT_AMPS = 0.2
MIN_PUBLICATION_SCHEDULER_TOLERANCE_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class GatewayControlSnapshot:
    """One coherent health/control evaluation shared by a gateway tick."""

    captured_at: float
    monotonic_at: float
    health: CommandPayload
    queue_age_seconds: float
    core_read_age_seconds: float
    eventloop_gap_ms: float
    eventloop_max_duration_ms: float
    resource_state: str
    pressure_state: GatewayPressureState
    stale_core_reads: tuple[str, ...]


class DbusAdapterHealth:
    def __init__(self, context: DbusAdapterHealthContext) -> None:
        self._context = context
        self._state_latch = GatewayHealthStateLatch()

    def append_health_log(self, health: Mapping[str, object]) -> None:
        if not self.health_log_due():
            return
        self._context._last_health_log_monotonic = time.monotonic()
        try:
            append_health_log(
                self._context.health_log_path,
                health,
                max_bytes=self._context.health_log_max_bytes,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logging.debug("Unable to append DBus gateway health history", exc_info=True)

    def health_log_due(self) -> bool:
        context = self._context
        if not context.health_log_path or context.health_log_interval_seconds <= 0.0:
            return False
        return bool(
            time.monotonic() - context._last_health_log_monotonic
            >= context.health_log_interval_seconds
        )

    def health_snapshot(self) -> CommandPayload:
        return dict(self.control_snapshot().health)

    def control_snapshot(self) -> GatewayControlSnapshot:
        context = self._context
        current_monotonic = time.monotonic()
        current_time = time.time()
        pending_snapshot = context.write_scheduler.pending_snapshot()
        pending = pending_snapshot.physical_list()
        effective_pending = pending_snapshot.effective_list()
        core_pending = context.core_command_mailbox.load_pending()
        resources = context.resource_monitor.snapshot()
        context._last_resource_snapshot = resources
        eventloop = context.tick_health.snapshot(now=current_monotonic)
        write_scheduler_health = context.write_scheduler.health(now=current_time)
        queue_metrics = queue_health(
            effective_pending,
            core_pending,
            current_time,
            physical_count=len(pending),
            write_scheduler_health=write_scheduler_health,
        )
        freshness = self.cache_freshness_snapshot(current_time)
        thresholds = self.slo_thresholds()
        slo = self.slo_snapshot(
            queue_health=queue_metrics,
            cache_freshness=freshness,
            current_monotonic=current_monotonic,
            eventloop=eventloop,
            thresholds=thresholds,
        )
        circuit_health = context.circuit.health()
        circuit_state = str(circuit_health.get("state", "ok"))
        backpressure = backpressure_snapshot(
            circuit_state=circuit_state,
            queue_health=queue_metrics,
            slo=slo,
            queue_max_age_seconds=context.slo_queue_max_age_seconds,
        )
        resource_state = str(resources.get("state", "ok"))
        backpressure_state = str(backpressure.get("state", "ok"))
        pressure_state = runtime_pressure_state(
            resource_state,
            backpressure_state,
        )
        operational_state = operational_health_state(circuit_state)
        performance_state = performance_health_state(
            slo_state=str(slo.get("state", "violated")),
            resource_state=resource_state,
            backpressure_state=backpressure_state,
        )
        aggregate = self._state_latch.observe(
            operational_state,
            performance_state,
            monotonic_at=current_monotonic,
            captured_at=current_time,
        )
        dormant_evidence = context.energy_discovery.dormant_evidence()
        dormant_source_ids = frozenset(
            evidence.source_id for evidence in dormant_evidence
        )
        source_unavailability_reasons = (
            context.energy_discovery.source_unavailability_reasons(
                dormant_source_ids=dormant_source_ids,
            )
        )
        heartbeat_age = (
            max(0.0, current_monotonic - context._last_tick_monotonic)
            if context._last_tick_monotonic > 0.0
            else 0.0
        )
        health: CommandPayload = {
            **circuit_health,
            "operational_state": operational_state,
            "performance_state": performance_state,
            "state": aggregate.state,
            "state_changed_at": aggregate.changed_at,
            "state_recovery_pending": aggregate.recovery_pending,
            "pending_command_count": len(effective_pending),
            "physical_command_count": len(pending),
            "core_command_count": len(core_pending),
            "registered_path_count": context.publication_role.registered_publication_path_count,
            "last_tick_at": context._last_tick_at,
            "tick_duration_ms": context._last_tick_duration_ms,
            "discovery_last_success_at": context.discovery.last_success_at,
            "discovery_last_error": context.discovery.last_error,
            "discovery_next_scan_at": context.discovery.next_scan_at,
            "discovery_next_scan_in_s": max(
                0.0,
                context.discovery.next_scan_monotonic - current_monotonic,
            ),
            "discovery_active_interval_s": (
                context.discovery.active_interval_seconds
            ),
            "dormant_energy_source_ids": [
                evidence.source_id for evidence in dormant_evidence
            ],
            "dormant_energy_source_evidence": [
                evidence.to_payload() for evidence in dormant_evidence
            ],
            "energy_source_unavailability_reasons": (
                source_unavailability_reasons
            ),
            "mainloop_heartbeat_age_s": heartbeat_age,
            "queues": queue_metrics,
            "queue_classes": queue_class_health(effective_pending, current_time),
            "write_scheduler": write_scheduler_health,
            "cache_freshness": freshness,
            "slo": slo,
            "backpressure": backpressure,
            "resources": resources,
            "publication_freshness_deadline_s": effective_gui_max_age_seconds(
                thresholds
            ),
            "adaptive_tick_seconds": context.tick_seconds,
            "min_tick_seconds": context.min_tick_seconds,
            "max_tick_seconds": context.max_tick_seconds,
            "eventloop": {
                "last_tick_at": context._last_tick_at,
                "tick_duration_ms": context._last_tick_duration_ms,
                "mainloop_heartbeat_age_s": heartbeat_age,
                **eventloop,
            },
        }
        return GatewayControlSnapshot(
            captured_at=current_time,
            monotonic_at=current_monotonic,
            health=health,
            queue_age_seconds=float_or_zero(queue_metrics.get("oldest_slo_command_age_s")),
            core_read_age_seconds=max_core_read_age(freshness),
            eventloop_gap_ms=_eventloop_metric(
                eventloop,
                "max_glib_callback_lateness_ms_60s",
                "max_tick_gap_ms_60s",
            ),
            eventloop_max_duration_ms=_eventloop_metric(
                eventloop,
                "max_blocking_time_ms_60s",
                "max_tick_duration_ms_60s",
            ),
            resource_state=resource_state,
            pressure_state=pressure_state,
            stale_core_reads=tuple(
                sorted(
                    stale_core_read_keys(
                        freshness,
                        CORE_ENERGY_READ_KEYS,
                        max_age_seconds=context.slo_core_read_max_age_seconds,
                    )
                )
            ),
        )

    def cache_freshness_snapshot(self, now: float) -> CommandPayload:
        return cache_freshness(self._context.cache, now)

    def slo_snapshot(
        self,
        *,
        queue_health: Mapping[str, object],
        cache_freshness: Mapping[str, object],
        current_monotonic: float,
        eventloop: Mapping[str, object] | None = None,
        thresholds: SloThresholds | None = None,
    ) -> CommandPayload:
        observed = (
            self.slo_observed(queue_health, cache_freshness, current_monotonic)
            if eventloop is None
            else self.slo_observed(
                queue_health,
                cache_freshness,
                current_monotonic,
                eventloop=eventloop,
            )
        )
        policy = self.slo_thresholds() if thresholds is None else thresholds
        checks = slo_checks_from_observed(observed, policy)
        return slo_payload(checks, slo_targets(policy), observed)

    def slo_thresholds(self) -> SloThresholds:
        context = self._context
        return SloThresholds(
            gui_max_age_seconds=context.slo_gui_max_age_seconds,
            core_read_max_age_seconds=context.slo_core_read_max_age_seconds,
            queue_max_age_seconds=context.slo_queue_max_age_seconds,
            mainloop_gap_max_ms=context.slo_mainloop_gap_max_ms,
            publication_scheduler_tolerance_seconds=max(
                MIN_PUBLICATION_SCHEDULER_TOLERANCE_SECONDS,
                context.tick_seconds,
            ),
        )

    def slo_observed(
        self,
        queue_health: Mapping[str, object],
        cache_freshness: Mapping[str, object],
        current_monotonic: float,
        *,
        eventloop: Mapping[str, object] | None = None,
    ) -> dict[str, float]:
        eventloop_metrics = (
            self._context.tick_health.snapshot(now=current_monotonic)
            if eventloop is None
            else eventloop
        )
        measurement_age = self.max_publication_field_age(
            GUI_MEASUREMENT_FRESHNESS_FIELDS,
            current_monotonic,
        )
        control_age = self.max_publication_field_age(
            GUI_CONTROL_FRESHNESS_FIELDS,
            current_monotonic,
            service_heartbeat_fields=GUI_CONTROL_FRESHNESS_FIELDS,
        )
        session_fields = self.gui_session_freshness_fields(current_monotonic)
        session_age = self.max_publication_field_age(
            session_fields,
            current_monotonic,
        )
        return {
            "gui_max_age_s": max(measurement_age, control_age, session_age),
            "gui_measurement_max_age_s": measurement_age,
            "gui_control_max_age_s": control_age,
            "gui_session_max_age_s": session_age,
            "gui_missing_field_count": self.missing_publication_field_count(
                self.gui_freshness_fields(current_monotonic)
            ),
            "gui_measurement_missing_field_count": self.missing_publication_field_count(
                GUI_MEASUREMENT_FRESHNESS_FIELDS
            ),
            "gui_control_missing_field_count": self.missing_publication_field_count(GUI_CONTROL_FRESHNESS_FIELDS),
            "gui_session_missing_field_count": self.missing_publication_field_count(session_fields),
            "core_read_max_age_s": max_core_read_age(cache_freshness),
            "core_read_missing_count": core_read_missing_count(cache_freshness),
            "core_read_nonfresh_count": core_read_nonfresh_count(cache_freshness),
            "queue_oldest_age_s": float_or_zero(queue_health.get("oldest_slo_command_age_s")),
            "mainloop_max_gap_ms_60s": _eventloop_metric(
                eventloop_metrics,
                "max_glib_callback_lateness_ms_60s",
                "max_tick_gap_ms_60s",
            ),
        }

    def gui_freshness_fields(self, monotonic_at: float) -> set[str]:
        fields = set(GUI_MEASUREMENT_FRESHNESS_FIELDS | GUI_CONTROL_FRESHNESS_FIELDS)
        fields.update(self.gui_session_freshness_fields(monotonic_at))
        return fields

    def gui_session_freshness_fields(self, monotonic_at: float) -> set[str]:
        return (
            set(ACTIVE_SESSION_GUI_FRESHNESS_FIELDS)
            if self.charging_session_active_for_gui(monotonic_at)
            else set()
        )

    def charging_session_active_for_gui(self, monotonic_at: float) -> bool:
        return (
            self.fresh_evcs_field_float("ac_power_w", monotonic_at)
            >= SESSION_ACTIVE_POWER_WATTS
            or self.fresh_evcs_field_float("ac_current_a", monotonic_at)
            >= SESSION_ACTIVE_CURRENT_AMPS
        )

    def fresh_evcs_field_float(self, field: str, monotonic_at: float) -> float:
        observation = self._context.publication_registry.evcs_field_observation(field)
        if publication_field_age(
            observation,
            monotonic_at,
        ) > effective_gui_max_age_seconds(self.slo_thresholds()):
            return 0.0
        return publication_field_float(observation)

    def apply_slo_regulation(
        self,
        snapshot: GatewayControlSnapshot | None = None,
    ) -> GatewayControlSnapshot:
        context = self._context
        control = self.control_snapshot() if snapshot is None else snapshot
        thresholds = self.slo_thresholds()
        context.write_scheduler.set_dynamic_local_publish_burst(
            regulated_publish_burst(
                queue_age=control.queue_age_seconds,
                eventloop_gap_ms=control.eventloop_gap_ms,
                base_burst=context.write_scheduler.local_publish_burst_limit,
                thresholds=thresholds,
                pressure_state=control.pressure_state,
            ),
            pressure_state=control.pressure_state,
        )
        if control.stale_core_reads:
            context.read_scheduler.expedite_healthy(control.stale_core_reads)
        circuit_state = str(
            control.health.get(
                "operational_state",
                control.health.get("state", "ok"),
            )
        )
        if circuit_state != "ok" or control.pressure_state != "ok":
            self.suspend_advisory_work(
                monotonic_at=control.monotonic_at,
                captured_at=control.captured_at,
            )
        return control

    def suspend_advisory_work(
        self,
        *,
        monotonic_at: float,
        captured_at: float,
    ) -> None:
        context = self._context
        if context.discovery.last_success_at > 0.0:
            context.discovery.defer_for(
                monotonic_at=monotonic_at,
                captured_at=captured_at,
                seconds=60.0,
            )
        context._last_introspection_full_scan_at = max(
            context._last_introspection_full_scan_at,
            captured_at,
        )
        for path, command in context.write_scheduler.pending_snapshot().physical:
            if command_queue_class_name(command) not in ADVISORY_QUEUE_CLASSES:
                continue
            context.write_scheduler.remove_pending(path, command)
            context.write_scheduler.record_lifecycle(command, "dropped")

    def max_publication_field_age(
        self,
        fields: set[str] | frozenset[str],
        monotonic_at: float,
        *,
        service_heartbeat_fields: set[str] | frozenset[str] = frozenset(),
    ) -> float:
        return max_publication_field_age(
            self._context.publication_registry,
            fields,
            monotonic_at,
            service_heartbeat_fields=service_heartbeat_fields,
        )

    def missing_publication_field_count(
        self,
        fields: set[str] | frozenset[str],
    ) -> float:
        return missing_publication_field_count(self._context.publication_registry, fields)


def _eventloop_metric(
    metrics: Mapping[str, object],
    preferred: str,
    legacy: str,
) -> float:
    value = metrics.get(preferred) if preferred in metrics else metrics.get(legacy)
    return float_or_zero(value)
