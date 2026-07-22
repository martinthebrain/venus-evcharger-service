#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""IO contracts for adapter process components."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar

from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager, DbusRateLimiter
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter, DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox, GatewayPaths
from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.energy import EnergyTopologySnapshot
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsSnapshot

_T = TypeVar("_T")


class DbusAdapterIoContext(Protocol):  # pragma: no cover
    """DBus read, discovery, timing, and cache-publish surface."""

    connection: DbusConnectionManager
    rate_limiter: DbusRateLimiter
    circuit: DbusCircuitBreaker
    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    discovery: DbusDiscoveryManager
    energy_discovery: DbusEnergyDiscoveryManager
    read_scheduler: DbusReadScheduler
    read_executor: DbusReadExecutor
    publication_registry: GatewayPublicationRegistry
    json_writer: AtomicJsonWriter
    paths: GatewayPaths
    cache_publish_interval_seconds: float
    max_tick_seconds: float
    slo_gui_max_age_seconds: float
    _introspection_queue_depth: int
    _last_cache_publish_monotonic: float
    _last_cache_publish_sequence: int

    def poll_one_due_read_once(self) -> bool: ...
    def refresh_services_if_due_once(self) -> bool: ...
    def list_services(self) -> list[str]: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...
    def health_snapshot(self) -> CommandPayload: ...
    def append_health_log(self, health: Mapping[str, object]) -> None: ...
    def write_introspection_snapshot(self) -> None: ...
    def write_gateway_diagnostics(
        self,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
    ) -> None: ...


class DbusAdapterDiagnosticsContext(Protocol):  # pragma: no cover
    """State required to assemble one semantic diagnostics snapshot."""

    paths: GatewayPaths
    cache: DbusCacheStore
    publication_registry: GatewayPublicationRegistry
    json_writer: AtomicJsonWriter
    max_tick_seconds: float
    slo_gui_max_age_seconds: float
    _introspection_queue_depth: int

    def gateway_diagnostics_snapshot(
        self,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
    ) -> GatewayDiagnosticsSnapshot: ...
