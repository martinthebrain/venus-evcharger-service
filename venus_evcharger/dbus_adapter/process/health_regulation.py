#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway health-control snapshots and pressure regulation."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.dbus_adapter.health.queue import (
    ADVISORY_QUEUE_CLASSES,
    command_queue_class_name,
)
from venus_evcharger.dbus_adapter.health.slo import (
    GatewayPressureState,
    SloThresholds,
    regulated_publish_burst,
)
from venus_evcharger.dbus_adapter.process.protocols.health import DbusAdapterHealthContext
from venus_evcharger.ipc.command_types import CommandPayload


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
    critical_read_operations: int
    critical_queue_operations: int
    operation_p95_ms: float


def apply_control_regulation(
    context: DbusAdapterHealthContext,
    control: GatewayControlSnapshot,
    thresholds: SloThresholds,
) -> bool:
    """Apply bounded scheduler regulation and report advisory suspension."""
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
    return circuit_state != "ok" or control.pressure_state != "ok"


def suspend_advisory_work(
    context: DbusAdapterHealthContext,
    *,
    monotonic_at: float,
    captured_at: float,
) -> None:
    """Defer discovery and remove queued advisory DBus operations."""
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
        if context.operation_broker.owns_path(path):
            continue
        if command_queue_class_name(command) not in ADVISORY_QUEUE_CLASSES:
            continue
        context.write_scheduler.remove_pending(path, command)
        context.write_scheduler.record_lifecycle(command, "dropped")
