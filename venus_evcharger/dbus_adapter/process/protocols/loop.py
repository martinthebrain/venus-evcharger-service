#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-loop contracts for adapter process components."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.dbus_adapter.process.protocols.roles import (
    HealthRole,
    IntrospectionRole,
    IoRole,
    PublicationRole,
    RuntimeRole,
    SocketRole,
)
from venus_evcharger.dbus_adapter.process.protocols.runtime import MainLoopLike
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.resources import ResourceMonitor
from venus_evcharger.dbus_adapter.tick_health import TickHealth
from venus_evcharger.dbus_adapter.write.scheduler import DbusWriteScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, GatewayPaths
from venus_evcharger.ipc.command_types import CommandPayload


class DbusAdapterLoopContext(Protocol):  # pragma: no cover
    """Main-loop scheduling surface required by ``DbusAdapterLoop``."""

    paths: GatewayPaths
    cache: DbusCacheStore
    circuit: DbusCircuitBreaker
    resource_monitor: ResourceMonitor
    tick_health: TickHealth
    read_executor: DbusReadExecutor
    write_scheduler: DbusWriteScheduler
    tick_seconds: float
    min_tick_seconds: float
    max_tick_seconds: float
    slo_mainloop_gap_max_ms: float
    slo_core_read_max_age_seconds: float
    _main_loop: MainLoopLike | None
    _stop: bool
    _next_work_tick_monotonic: float
    _last_resource_snapshot: CommandPayload
    _last_tick_at: float
    _last_tick_monotonic: float
    _last_tick_duration_ms: float
    _prefer_read_next: bool
    @property
    def runtime_role(self) -> RuntimeRole: ...
    @property
    def socket_role(self) -> SocketRole: ...
    @property
    def publication_role(self) -> PublicationRole: ...
    @property
    def health_role(self) -> HealthRole: ...
    @property
    def io_role(self) -> IoRole: ...
    @property
    def introspection_role(self) -> IntrospectionRole: ...
