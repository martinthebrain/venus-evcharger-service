#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dedicated Victron DBus adapter process assembly.

This process is the only production component that should touch Victron DBus.
It owns reads, writes, introspection, the EV charger DBus service registration,
rate limiting, circuit breaking, and the RAM cache published to files.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Callable
from typing import TypeVar

_VELIB_PYTHON_PATH = "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"
if _VELIB_PYTHON_PATH not in sys.path:
    sys.path.insert(1, _VELIB_PYTHON_PATH)

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.process.config import adapter_settings, load_adapter_config
from venus_evcharger.dbus_adapter.process.diagnostics import DbusAdapterDiagnostics
from venus_evcharger.dbus_adapter.process.health import DbusAdapterHealth
from venus_evcharger.dbus_adapter.process.introspection import DbusAdapterIntrospection
from venus_evcharger.dbus_adapter.process.introspection_snapshot import DbusAdapterIntrospectionSnapshot
from venus_evcharger.dbus_adapter.process.io import DbusAdapterIo
from venus_evcharger.dbus_adapter.process.loop import DbusAdapterLoop
from venus_evcharger.dbus_adapter.process.protocols.runtime import MainLoopLike
from venus_evcharger.dbus_adapter.process.publication import DbusAdapterPublication
from venus_evcharger.dbus_adapter.process.runtime import DbusAdapterRuntime
from venus_evcharger.dbus_adapter.process.socket import DbusAdapterSocket
from venus_evcharger.dbus_adapter.process.write_context import DbusAdapterWriteContext
from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager, DbusRateLimiter
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.resources import (
    ResourceMonitor,
    ResourceMonitorSettings,
)
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter, DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_adapter.tick_health import TickHealth
from venus_evcharger.dbus_adapter.write.scheduler import DbusWriteScheduler
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    DbusGatewayCommandInbox,
    GatewayPaths,
)
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.core_commands import CoreCommandMailbox
from venus_evcharger.ipc.fast_publication import FastPublicationQueue
from venus_evcharger.ipc.publication_order import PUBLICATION_ORDER_STATE_NAME

_T = TypeVar("_T")


class DbusAdapter:
    """Single process owner for Victron DBus interaction."""

    runtime_role: DbusAdapterRuntime
    io_role: DbusAdapterIo
    introspection_role: DbusAdapterIntrospection
    introspection_snapshot_role: DbusAdapterIntrospectionSnapshot
    diagnostics_role: DbusAdapterDiagnostics
    publication_role: DbusAdapterPublication
    health_role: DbusAdapterHealth
    socket_role: DbusAdapterSocket
    loop_role: DbusAdapterLoop

    def __init__(self, config_path: str, *, paths: GatewayPaths | None = None) -> None:
        self.config_path = config_path
        self.config = load_adapter_config(config_path)
        defaults = self.config["DEFAULT"]
        settings = adapter_settings(defaults, explicit_paths=paths)
        self.paths = settings.paths
        self.connection = DbusConnectionManager()
        self.rate_limiter = DbusRateLimiter(
            read_interval_seconds=settings.rates.read_interval_seconds,
            write_interval_seconds=settings.rates.write_interval_seconds,
            introspection_interval_seconds=settings.rates.introspection_interval_seconds,
        )
        self.circuit = DbusCircuitBreaker()
        self.cache = DbusCacheStore(
            self.paths,
            stale_after_seconds=settings.stale_after_seconds,
        )
        self.commands = DbusGatewayCommandInbox(self.paths.command_dir)
        self.fast_publications = FastPublicationQueue(
            order_state_path=os.path.join(self.paths.run_dir, PUBLICATION_ORDER_STATE_NAME)
        )
        self.core_command_mailbox: CommandMailbox = CoreCommandMailbox(self.paths.core_command_dir)
        self.service_name = settings.service_name
        self._stop = False
        self._server: socket.socket | None = None
        self._main_loop: MainLoopLike | None = None
        self.read_scheduler = DbusReadScheduler(settings.read_specs)
        self.energy_discovery = DbusEnergyDiscoveryManager(
            settings.read_specs,
            max_prefix_services=max(1, int(defaults.get("AutoPvMaxServices", "10"))),
        )
        self.read_executor = DbusReadExecutor(self)
        self.min_tick_seconds = settings.timing.min_tick_seconds
        self.max_tick_seconds = settings.timing.max_tick_seconds
        self.tick_seconds = self.min_tick_seconds
        self._next_work_tick_monotonic = 0.0
        self._last_resource_snapshot: CommandPayload = {}
        self.discovery = DbusDiscoveryManager(
            interval_seconds=settings.timing.service_list_interval_seconds,
            missing_pv_interval_seconds=(
                settings.timing.missing_pv_discovery_interval_seconds
            ),
        )
        self.json_writer = AtomicJsonWriter()
        self.energy_publish_interval_seconds = settings.timing.energy_publish_interval_seconds
        self.health_publish_interval_seconds = settings.timing.health_publish_interval_seconds
        self.cache_publish_interval_seconds = settings.timing.cache_publish_interval_seconds
        self.cache_dirty_publish_interval_seconds = settings.timing.cache_dirty_publish_interval_seconds
        self.command_lifecycle_path = settings.files.command_lifecycle_path
        self.command_lifecycle_max_bytes = settings.files.command_lifecycle_max_bytes
        self.slo_gui_max_age_seconds = settings.slo.gui_max_age_seconds
        self.slo_core_read_max_age_seconds = settings.slo.core_read_max_age_seconds
        self.slo_queue_max_age_seconds = settings.slo.queue_max_age_seconds
        self.slo_mainloop_gap_max_ms = settings.slo.mainloop_gap_max_ms
        self.health_log_path = settings.files.health_log_path
        self.health_log_interval_seconds = settings.files.health_log_interval_seconds
        self.health_log_max_bytes = settings.files.health_log_max_bytes
        self.dbus_introspection_snapshot_path = settings.introspection.snapshot_path
        self.dbus_introspection_enabled = settings.introspection.enabled
        self._last_introspection_full_scan_at = 0.0
        self._introspection_queue_depth = 0
        self._last_energy_publish_monotonic = 0.0
        self._last_health_publish_monotonic = 0.0
        self._last_cache_publish_monotonic = 0.0
        self._last_cache_publish_sequence = -1
        self._last_topology_generation = -1
        self._last_health_log_monotonic = 0.0
        self._last_tick_at = 0.0
        self._last_tick_monotonic = 0.0
        self._last_tick_duration_ms = 0.0
        self.resource_monitor = ResourceMonitor(
            settings=ResourceMonitorSettings(
                sample_interval_seconds=settings.resources.sample_interval_seconds,
                recovery_hold_seconds=settings.resources.recovery_hold_seconds,
            ),
        )
        self.tick_health = TickHealth()
        self._prefer_read_next = True
        self.runtime_role = DbusAdapterRuntime(self)
        self.io_role = DbusAdapterIo(self)
        self.publication_registry = GatewayPublicationRegistry(
            self.config,
            evcs_service_name=self.service_name,
            cache=self.cache,
            core_commands=self.core_command_mailbox,
            timed_publish=self.io_role.timed_local_publish,
        )
        self.publication_role = DbusAdapterPublication(self)
        self.introspection_role = DbusAdapterIntrospection(self)
        self.introspection_snapshot_role = DbusAdapterIntrospectionSnapshot(self)
        self.diagnostics_role = DbusAdapterDiagnostics(self)
        write_context = DbusAdapterWriteContext(
            cache=self.cache,
            circuit=self.circuit,
            commands=self.commands,
            command_lifecycle_path=self.command_lifecycle_path,
            command_lifecycle_max_bytes=self.command_lifecycle_max_bytes,
            config=self.config,
            connection=self.connection,
            core_command_mailbox=self.core_command_mailbox,
            fast_publications=self.fast_publications,
            json_writer=self.json_writer,
            publication_registry=self.publication_registry,
            service_name=self.service_name,
            publication_role=self.publication_role,
            introspection_role=self.introspection_role,
            io_role=self.io_role,
        )
        self.write_scheduler = DbusWriteScheduler(write_context)
        self.health_role = DbusAdapterHealth(self)
        self.socket_role = DbusAdapterSocket(self)
        self.loop_role = DbusAdapterLoop(self)

    def run(self) -> None:
        self.loop_role.run()

    def tick(self) -> bool:
        return self.loop_role.tick()

    @property
    def evcs_service_registered(self) -> bool:
        return self.publication_role.evcs_service_registered

    @property
    def registered_publication_path_count(self) -> int:
        return self.publication_role.registered_publication_path_count

    def health_snapshot(self) -> CommandPayload:
        return self.health_role.health_snapshot()

    def process_non_write_command(self, command: CommandMapping) -> CommandOutcome:
        return self.introspection_role.process_non_write_command(command)

    def timed_dbus_operation(
        self,
        kind: str,
        operation: Callable[[], _T],
        *,
        source: str = "",
    ) -> _T:
        return self.io_role.timed_dbus_operation(
            kind,
            operation,
            source=source,
        )

    def timed_local_publish(self, operation: Callable[[], _T]) -> _T:
        return self.io_role.timed_local_publish(operation)


__all__ = ["DbusAdapter"]
