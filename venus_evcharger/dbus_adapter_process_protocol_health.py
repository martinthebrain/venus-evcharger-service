#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Health/SLO contract for DBus adapter process mixins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from venus_evcharger.dbus_adapter_components import (
    DbusCircuitBreaker,
    DbusDiscoveryManager,
    DbusReadScheduler,
    ResourceMonitor,
    TickHealth,
)
from venus_evcharger.dbus_adapter_health_slo import SloThresholds
from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox


class DbusAdapterHealthContext(Protocol):  # pragma: no cover
    """Health, SLO, and backpressure surface."""

    cache: DbusCacheStore
    commands: DbusCommandInbox
    core_commands: DbusCommandInbox
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
    slo_gui_max_age_seconds: float
    slo_core_read_max_age_seconds: float
    slo_queue_max_age_seconds: float
    slo_mainloop_gap_max_ms: float
    _last_resource_snapshot: dict[str, Any]
    _last_health_log_monotonic: float
    _last_tick_at: float
    _last_tick_monotonic: float
    _last_tick_duration_ms: float
    _last_introspection_full_scan_at: float

    def health_log_due(self) -> bool: ...
    def health_snapshot(self) -> dict[str, Any]: ...
    def cache_freshness_snapshot(self, now: float) -> dict[str, Any]: ...
    def slo_snapshot(
        self,
        *,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, Any]: ...
    def slo_observed(
        self,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, float]: ...
    def slo_thresholds(self) -> SloThresholds: ...
    def gui_freshness_paths(self, now: float) -> set[str]: ...
    def gui_session_freshness_paths(self, now: float) -> set[str]: ...
    def charging_session_active_for_gui(self, now: float) -> bool: ...
    def fresh_cached_path_float(self, path: str, now: float) -> float: ...
    def apply_slo_regulation(self) -> None: ...
    def quiet_discovery_and_introspection(self, now: float) -> None: ...
    def max_cached_path_age_for_paths(self, paths: set[str], now: float) -> float: ...
    def missing_cached_path_count_for_paths(self, paths: set[str]) -> float: ...
