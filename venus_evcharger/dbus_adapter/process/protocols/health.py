#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Health and SLO contracts for adapter process components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from venus_evcharger.dbus_adapter.health.freshness import EvcsPublicationObservations
from venus_evcharger.dbus_adapter.health.slo import SloThresholds
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker
from venus_evcharger.dbus_adapter.resources import ResourceMonitor, TickHealth
from venus_evcharger.dbus_adapter.scheduling import DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_adapter.write.scheduler import DbusWriteScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.command_types import CommandPayload


class DbusAdapterHealthContext(Protocol):  # pragma: no cover
    """Health, SLO, and backpressure surface."""

    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    core_command_mailbox: CommandMailbox
    circuit: DbusCircuitBreaker
    discovery: DbusDiscoveryManager
    read_scheduler: DbusReadScheduler
    write_scheduler: DbusWriteScheduler
    resource_monitor: ResourceMonitor
    tick_health: TickHealth
    service_name: str
    tick_seconds: float
    min_tick_seconds: float
    max_tick_seconds: float
    health_log_path: str
    health_log_interval_seconds: float
    health_log_max_bytes: int
    slo_gui_max_age_seconds: float
    slo_core_read_max_age_seconds: float
    slo_queue_max_age_seconds: float
    slo_mainloop_gap_max_ms: float
    _last_resource_snapshot: CommandPayload
    _last_health_log_monotonic: float
    _last_tick_at: float
    _last_tick_monotonic: float
    _last_tick_duration_ms: float
    _last_introspection_full_scan_at: float

    @property
    def publication_registry(self) -> EvcsPublicationObservations: ...

    @property
    def registered_publication_path_count(self) -> int: ...

    def health_log_due(self) -> bool: ...
    def health_snapshot(self) -> CommandPayload: ...
    def cache_freshness_snapshot(self, now: float) -> CommandPayload: ...
    def slo_snapshot(
        self,
        *,
        queue_health: Mapping[str, object],
        cache_freshness: Mapping[str, object],
        now: float,
        current_monotonic: float,
    ) -> CommandPayload: ...
    def slo_observed(
        self,
        queue_health: Mapping[str, object],
        cache_freshness: Mapping[str, object],
        now: float,
        current_monotonic: float,
    ) -> dict[str, float]: ...
    def slo_thresholds(self) -> SloThresholds: ...
    def gui_freshness_fields(self, now: float) -> set[str]: ...
    def gui_session_freshness_fields(self, now: float) -> set[str]: ...
    def charging_session_active_for_gui(self, now: float) -> bool: ...
    def fresh_evcs_field_float(self, field: str, now: float) -> float: ...
    def apply_slo_regulation(self) -> None: ...
    def suspend_advisory_work(self, now: float) -> None: ...
    def max_publication_field_age(self, fields: set[str] | frozenset[str], now: float) -> float: ...
    def missing_publication_field_count(self, fields: set[str] | frozenset[str]) -> float: ...
