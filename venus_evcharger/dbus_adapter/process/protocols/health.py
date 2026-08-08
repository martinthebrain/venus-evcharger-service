#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Health and SLO contracts for adapter process components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from venus_evcharger.dbus_adapter.async_broker import DbusAsyncOperationBroker
from venus_evcharger.dbus_adapter.process.protocols.roles import PublicationRole
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.resources import ResourceMonitor
from venus_evcharger.dbus_adapter.scheduling import DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_adapter.tick_health import TickHealth
from venus_evcharger.dbus_adapter.write.scheduler import DbusWriteScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.command_types import CommandPayload

if TYPE_CHECKING:
    from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry


class DbusAdapterHealthContext(Protocol):  # pragma: no cover
    """Health, SLO, and backpressure surface."""

    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    core_command_mailbox: CommandMailbox
    circuit: DbusCircuitBreaker
    operation_broker: DbusAsyncOperationBroker
    discovery: DbusDiscoveryManager
    energy_discovery: DbusEnergyDiscoveryManager
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
    def publication_role(self) -> PublicationRole: ...
    @property
    def publication_registry(self) -> GatewayPublicationRegistry: ...
