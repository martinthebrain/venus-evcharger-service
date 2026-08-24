#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""IO contracts for adapter process components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from venus_evcharger.dbus_adapter.async_broker import DbusAsyncOperationBroker
from venus_evcharger.dbus_adapter.async_protocols import DbusConnectionLifecycle
from venus_evcharger.dbus_adapter.process.protocols.roles import (
    DiagnosticsRole,
    HealthRole,
    IntrospectionRole,
    IntrospectionSnapshotRole,
)
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusRateLimiter
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter, DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox, GatewayPaths

if TYPE_CHECKING:
    from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry


class DbusAdapterIoContext(Protocol):  # pragma: no cover
    """DBus read, discovery, timing, and cache-publish surface."""

    rate_limiter: DbusRateLimiter
    circuit: DbusCircuitBreaker
    operation_broker: DbusAsyncOperationBroker
    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    discovery: DbusDiscoveryManager
    energy_discovery: DbusEnergyDiscoveryManager
    read_scheduler: DbusReadScheduler
    read_executor: DbusReadExecutor
    json_writer: AtomicJsonWriter
    paths: GatewayPaths
    cache_publish_interval_seconds: float
    cache_dirty_publish_interval_seconds: float
    energy_publish_interval_seconds: float
    health_publish_interval_seconds: float
    max_tick_seconds: float
    slo_gui_max_age_seconds: float
    slo_core_read_max_age_seconds: float
    _introspection_queue_depth: int
    _last_energy_publish_monotonic: float
    _last_health_publish_monotonic: float
    _last_cache_publish_monotonic: float
    _last_cache_publish_sequence: int
    _last_topology_generation: int
    @property
    def connection(self) -> DbusConnectionLifecycle: ...
    @property
    def health_role(self) -> HealthRole: ...
    @property
    def diagnostics_role(self) -> DiagnosticsRole: ...
    @property
    def introspection_role(self) -> IntrospectionRole: ...
    @property
    def introspection_snapshot_role(self) -> IntrospectionSnapshotRole: ...
    @property
    def publication_registry(self) -> GatewayPublicationRegistry: ...


class DbusAdapterDiagnosticsContext(Protocol):  # pragma: no cover
    """State required to assemble one semantic diagnostics snapshot."""

    paths: GatewayPaths
    cache: DbusCacheStore
    json_writer: AtomicJsonWriter
    max_tick_seconds: float
    slo_gui_max_age_seconds: float
    _introspection_queue_depth: int

    @property
    def publication_registry(self) -> GatewayPublicationRegistry: ...
