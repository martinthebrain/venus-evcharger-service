#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dedicated Victron DBus adapter for the Venus EV charger service.

This process is the only production component that should touch Victron DBus.
It owns reads, writes, introspection, the EV charger DBus service registration,
rate limiting, circuit breaking, and the RAM cache published to files.
"""

from __future__ import annotations

import configparser
import logging
import os
import socket
import sys

sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)

from venus_evcharger.dbus_adapter_components import (
    AtomicJsonWriter,
    DbusCircuitBreaker,
    DbusConnectionManager,
    DbusDiscoveryManager,
    DbusRateLimiter,
    DbusReadScheduler,
    ResourceMonitor,
    TickHealth,
)
from venus_evcharger.dbus_adapter_process_config import adapter_settings, load_adapter_config
from venus_evcharger.dbus_adapter_process_loop import DbusAdapterLoop
from venus_evcharger.dbus_adapter_process_protocol_runtime import MainLoopLike
from venus_evcharger.dbus_adapter_read import DbusReadExecutor
from venus_evcharger.dbus_adapter_service_protocol import DbusServiceLike
from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    DbusCommandInbox,
    GatewayPaths,
)
from venus_evcharger.dbus_gateway_command_types import CommandPayload


class DbusAdapter(DbusAdapterLoop):
    """Single process owner for Victron DBus interaction."""

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
        self.commands = DbusCommandInbox(self.paths.command_dir)
        self.core_commands = DbusCommandInbox(self.paths.core_command_dir)
        self.service_name = settings.service_name
        self._dbusservice: DbusServiceLike | None = None
        self._dbusservice_registered = False
        self.write_scheduler = DbusWriteScheduler(self)
        self._stop = False
        self._server: socket.socket | None = None
        self._main_loop: MainLoopLike | None = None
        self.read_scheduler = DbusReadScheduler(settings.read_specs)
        self.read_executor = DbusReadExecutor(self)
        self.min_tick_seconds = settings.timing.min_tick_seconds
        self.max_tick_seconds = settings.timing.max_tick_seconds
        self.tick_seconds = self.min_tick_seconds
        self._next_work_tick_monotonic = 0.0
        self._last_resource_snapshot: CommandPayload = {}
        self.discovery = DbusDiscoveryManager(interval_seconds=settings.timing.service_list_interval_seconds)
        self.json_writer = AtomicJsonWriter()
        self.cache_publish_interval_seconds = settings.timing.cache_publish_interval_seconds
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
        self.dbus_introspection_request_path = settings.introspection.request_path
        self.dbus_introspection_enabled = settings.introspection.enabled
        self._last_introspection_full_scan_at = 0.0
        self._introspection_queue_depth = 0
        self._last_cache_publish_monotonic = 0.0
        self._last_cache_publish_sequence = -1
        self._last_health_log_monotonic = 0.0
        self._last_tick_at = 0.0
        self._last_tick_monotonic = 0.0
        self._last_tick_duration_ms = 0.0
        self.resource_monitor = ResourceMonitor()
        self.tick_health = TickHealth()
        self._prefer_read_next = True

def _logging_level_from_config(config: configparser.ConfigParser) -> int:
    value = str(config["DEFAULT"].get("Logging", "INFO")).strip().upper()
    return getattr(logging, value, logging.INFO)


__all__ = [
    "DbusAdapter",
    "_logging_level_from_config",
    "configparser",
    "logging",
    "os",
]
