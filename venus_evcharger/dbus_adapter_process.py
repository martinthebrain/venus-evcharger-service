#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dedicated Victron DBus adapter for the Venus EV charger service.

This process is the only production component that should touch Victron DBus.
It owns reads, writes, introspection, the EV charger DBus service registration,
rate limiting, circuit breaking, and the RAM cache published to files.
"""

from __future__ import annotations

import configparser
import json  # noqa: F401 - re-exported by venus_evcharger_dbus_adapter for compatibility tests
import logging
import os
import select  # noqa: F401 - re-exported by venus_evcharger_dbus_adapter for compatibility tests
import signal  # noqa: F401 - re-exported by venus_evcharger_dbus_adapter for compatibility tests
import socket
import sys
import time  # noqa: F401 - re-exported by venus_evcharger_dbus_adapter for compatibility tests
from typing import Any

sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)

import dbus  # noqa: F401
from dbus.mainloop.glib import DBusGMainLoop  # noqa: F401
from gi.repository import GLib  # noqa: F401

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
from venus_evcharger.dbus_adapter_process_health import DbusAdapterHealthMixin
from venus_evcharger.dbus_adapter_process_introspection import DbusAdapterIntrospectionMixin
from venus_evcharger.dbus_adapter_process_introspection_snapshot import DbusAdapterIntrospectionSnapshotMixin
from venus_evcharger.dbus_adapter_process_io import DbusAdapterIoMixin
from venus_evcharger.dbus_adapter_process_loop import DbusAdapterLoopMixin
from venus_evcharger.dbus_adapter_process_runtime import DbusAdapterRuntimeMixin
from venus_evcharger.dbus_adapter_read import DbusReadExecutor
from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    DbusCommandInbox,
    GatewayPaths,
    gateway_paths,
)


class _CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps DBus path and option casing intact."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


class DbusAdapter(
    DbusAdapterLoopMixin,
    DbusAdapterIntrospectionMixin,
    DbusAdapterRuntimeMixin,
    DbusAdapterIoMixin,
    DbusAdapterIntrospectionSnapshotMixin,
    DbusAdapterHealthMixin,
):
    """Single process owner for Victron DBus interaction."""

    def __init__(self, config_path: str, *, paths: GatewayPaths | None = None) -> None:
        self.config_path = config_path
        self.config = self._load_config(config_path)
        defaults = self.config["DEFAULT"]
        self.paths = paths or gateway_paths(defaults.get("DbusGatewayRunDir", ""))
        self.connection = DbusConnectionManager()
        self.rate_limiter = DbusRateLimiter(
            read_interval_seconds=float(defaults.get("DbusGatewayReadIntervalSeconds", 0.25)),
            write_interval_seconds=float(defaults.get("DbusGatewayWriteIntervalSeconds", 0.35)),
            introspection_interval_seconds=float(defaults.get("DbusGatewayIntrospectionIntervalSeconds", 2.0)),
        )
        self.circuit = DbusCircuitBreaker()
        self.cache = DbusCacheStore(
            self.paths,
            stale_after_seconds=float(defaults.get("DbusGatewayStaleAfterSeconds", 10.0)),
        )
        self.commands = DbusCommandInbox(self.paths.command_dir)
        self.core_commands = DbusCommandInbox(self.paths.core_command_dir)
        self.service_name = self._evcharger_service_name(defaults)
        self._dbusservice: Any = None
        self._dbusservice_registered = False
        self.write_scheduler = DbusWriteScheduler(self)
        self._stop = False
        self._server: socket.socket | None = None
        self._main_loop: Any = None
        self.read_scheduler = DbusReadScheduler(self._configured_read_specs(defaults))
        self.read_executor = DbusReadExecutor(self)
        configured_tick = float(defaults.get("DbusGatewayTickSeconds", 0.2))
        self.min_tick_seconds = max(0.05, float(defaults.get("DbusGatewayMinTickSeconds", configured_tick)))
        self.max_tick_seconds = max(
            self.min_tick_seconds,
            float(defaults.get("DbusGatewayMaxTickSeconds", 1.0)),
        )
        self.tick_seconds = self.min_tick_seconds
        self._next_work_tick_monotonic = 0.0
        self._last_resource_snapshot: dict[str, Any] = {}
        self.discovery = DbusDiscoveryManager(
            interval_seconds=float(defaults.get("DbusGatewayServiceListIntervalSeconds", 900.0))
        )
        self.json_writer = AtomicJsonWriter()
        self.cache_publish_interval_seconds = max(
            0.0,
            float(defaults.get("DbusGatewayCachePublishIntervalSeconds", 0.0)),
        )
        self.command_lifecycle_path = str(
            defaults.get(
                "DbusGatewayCommandLifecyclePath",
                os.path.join(self.paths.run_dir, "dbus-command-lifecycle.jsonl"),
            )
        ).strip()
        self.slo_gui_max_age_seconds = max(0.1, float(defaults.get("DbusGatewaySloGuiMaxAgeSeconds", 2.0)))
        self.slo_core_read_max_age_seconds = max(
            0.1,
            float(defaults.get("DbusGatewaySloCoreReadMaxAgeSeconds", 5.0)),
        )
        self.slo_queue_max_age_seconds = max(0.1, float(defaults.get("DbusGatewaySloQueueMaxAgeSeconds", 10.0)))
        self.slo_mainloop_gap_max_ms = max(10.0, float(defaults.get("DbusGatewaySloMainloopGapMaxMs", 500.0)))
        self.health_log_path = str(
            defaults.get("DbusGatewayHealthLogPath", os.path.join(self.paths.run_dir, "dbus-health-history.jsonl"))
        ).strip()
        self.health_log_interval_seconds = max(0.0, float(defaults.get("DbusGatewayHealthLogIntervalSeconds", 10.0)))
        deviceinstance = self._device_instance(defaults)
        self.dbus_introspection_snapshot_path = str(
            defaults.get(
                "DbusIntrospectionSnapshotPath",
                f"/run/dbus-venus-evcharger-dbus-map-{deviceinstance}.json",
            )
        ).strip()
        self.dbus_introspection_request_path = str(
            defaults.get(
                "DbusIntrospectionRequestPath",
                f"/run/dbus-venus-evcharger-dbus-map-requests-{deviceinstance}.json",
            )
        ).strip()
        self.dbus_introspection_enabled = str(defaults.get("DbusIntrospectionEnabled", "1")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
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

    @staticmethod
    def _load_config(path: str) -> configparser.ConfigParser:
        parser = _CasePreservingConfigParser()
        loaded = parser.read(path)
        if not loaded:
            raise ValueError(f"Unable to read config file: {path}")
        return parser

    @staticmethod
    def _evcharger_service_name(defaults: configparser.SectionProxy) -> str:
        base = str(defaults.get("ServiceName", "com.victronenergy.evcharger")).strip() or "com.victronenergy.evcharger"
        try:
            device_instance = int(str(defaults.get("DeviceInstance", "60")).strip() or "60")
        except ValueError:
            device_instance = 60
        return f"{base}.http_{device_instance}"

    @staticmethod
    def _configured_read_specs(defaults: configparser.SectionProxy) -> dict[str, dict[str, Any]]:
        grid_paths = [
            str(defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power")).strip(),
            str(defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power")).strip(),
            str(defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power")).strip(),
        ]
        battery_service = str(defaults.get("AutoBatteryService", "")).strip()
        if battery_service.endswith(".example"):
            battery_service = ""
        return {
            "grid_power_w": {
                "service": str(defaults.get("AutoGridService", "com.victronenergy.system")).strip(),
                "paths": [path for path in grid_paths if path],
                "interval": 2.0,
                "aggregate": "sum",
                "priority": "read",
            },
            "pv_power_w": {
                "service": str(defaults.get("AutoPvService", "")).strip(),
                "prefix": str(defaults.get("AutoPvServicePrefix", "com.victronenergy.pvinverter")).strip(),
                "path": str(defaults.get("AutoPvPath", "/Ac/Power")).strip(),
                "dc_service": str(defaults.get("AutoDcPvService", "com.victronenergy.system")).strip(),
                "dc_path": str(defaults.get("AutoDcPvPath", "/Dc/Pv/Power")).strip(),
                "use_dc_pv": str(defaults.get("AutoUseDcPv", "1")).strip().lower()
                in ("1", "true", "yes", "on"),
                "interval": 2.0,
                "aggregate": "pv-total",
                "priority": "read",
                "optional_zero_on_error": True,
                "optional_confidence": 0.2,
            },
            "battery_soc": {
                "service": battery_service,
                "prefix": str(defaults.get("AutoBatteryServicePrefix", "com.victronenergy.battery")).strip(),
                "path": str(defaults.get("AutoBatterySocPath", "/Dc/Battery/Soc")).strip(),
                "aggregate": "first-service" if not battery_service else "",
                "interval": 2.0,
                "priority": "read",
            },
        }
def _logging_level_from_config(config: configparser.ConfigParser) -> int:
    value = str(config["DEFAULT"].get("Logging", "INFO")).strip().upper()
    return getattr(logging, value, logging.INFO)
