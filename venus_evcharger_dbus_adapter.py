#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dedicated Victron DBus adapter for the Venus EV charger service.

This process is the only production component that should touch Victron DBus.
It owns reads, writes, introspection, the EV charger DBus service registration,
rate limiting, circuit breaking, and the RAM cache published to files.
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import platform
import select
import signal
import socket
import sys
import time
import xml.etree.ElementTree as xml_et
from typing import Any, Callable, Mapping

sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from vedbus import VeDbusService

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.dbus_introspection import DBUS_INTROSPECTION_SCHEMA_VERSION
from venus_evcharger.dbus_adapter_components import (
    AtomicJsonWriter,
    CommandOutcome,
    DbusCircuitBreaker,
    DbusConnectionManager,
    DbusDiscoveryManager,
    DbusOperationDeferred,
    DbusRateLimiter,
    DbusReadScheduler,
    ResourceMonitor,
    TickHealth,
)
from venus_evcharger.dbus_adapter_read import DbusReadExecutor
from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_gateway import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    DbusCacheStore,
    DbusCommandInbox,
    FAST_READ_KEYS,
    GUI_CRITICAL_PUBLISH_PATHS,
    GatewayPaths,
    command_queue_class,
    dbus_path_key,
    gateway_paths,
)


class DbusAdapter:
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
        parser = configparser.ConfigParser()
        parser.optionxform = str  # type: ignore[method-assign]
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
                "interval": 2.0,
                "aggregate": "services-sum",
                "priority": "read",
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

    def run(self) -> None:  # pragma: no cover - Venus DBus/GLib process loop
        DBusGMainLoop(set_as_default=True)
        self._install_signal_handlers()
        os.makedirs(self.paths.run_dir, exist_ok=True)
        os.makedirs(self.paths.command_dir, exist_ok=True)
        os.makedirs(self.paths.core_command_dir, exist_ok=True)
        self._start_socket()
        self._ensure_dbus_service()
        self._main_loop = GLib.MainLoop()
        GLib.timeout_add(max(50, int(self.min_tick_seconds * 1000)), self._tick)
        try:
            self._main_loop.run()
        finally:
            self._stop = True
            self._close_socket()

    def _tick(self) -> bool:
        tick_started = time.monotonic()
        if self._stop:
            self._close_socket()
            return False
        if tick_started < self._next_work_tick_monotonic:
            return True
        self._last_tick_at = time.time()
        self._last_tick_monotonic = tick_started
        try:
            self._process_socket_once()
            self._process_introspection_requests_once()
            self._process_one_dbus_operation_once()
            self._publish_cache()
        except Exception as error:  # pylint: disable=broad-except
            self.circuit.record_error(error)
            logging.exception("DBus adapter tick failed: %s", error)
        finally:
            self._last_tick_duration_ms = (time.monotonic() - tick_started) * 1000.0
            self.tick_health.record(
                duration_ms=self._last_tick_duration_ms,
                expected_interval_s=self.tick_seconds,
                now=tick_started,
            )
            self._update_adaptive_tick()
            self._next_work_tick_monotonic = time.monotonic() + self.tick_seconds
        return not self._stop

    def _update_adaptive_tick(self) -> None:
        resources = self.resource_monitor.snapshot()
        self._last_resource_snapshot = resources
        self._apply_slo_regulation()
        resource_state = str(resources.get("state", "ok"))
        if float(self.tick_health.snapshot().get("max_tick_duration_ms_60s", 0.0) or 0.0) > self.slo_mainloop_gap_max_ms:
            resource_state = "busy"
        self.tick_seconds = self._adaptive_tick_seconds(
            circuit_state=self.circuit.state(),
            resource_state=resource_state,
        )

    def _adaptive_tick_seconds(self, *, circuit_state: str, resource_state: str) -> float:
        if circuit_state == "protective" or resource_state == "constrained":
            return self.max_tick_seconds
        if circuit_state == "degraded":
            return min(self.max_tick_seconds, max(self.min_tick_seconds * 2.5, 0.5))
        if resource_state == "busy":
            return min(self.max_tick_seconds, max(self.min_tick_seconds * 1.5, 0.3))
        return self.min_tick_seconds

    def _process_one_dbus_operation_once(self) -> bool:
        if not self.cache.services and self._refresh_services_if_due_once():
            return True
        self._enqueue_background_introspection_if_due()
        local_publish_count = self.write_scheduler.process_local_publish_burst()
        if self._prefer_read_next:
            if self._poll_one_due_read_once():
                self._prefer_read_next = False
                return True
            if self.write_scheduler.process_one(include_local_publish=False):
                self._prefer_read_next = True
                return True
        else:
            if self.write_scheduler.process_one(include_local_publish=False):
                self._prefer_read_next = True
                return True
            if self._poll_one_due_read_once():
                self._prefer_read_next = False
                return True
        return self._refresh_services_if_due_once() or local_publish_count > 0

    def _process_introspection_requests_once(self) -> None:
        if not self.dbus_introspection_enabled:
            return
        payload = self._read_introspection_request_payload()
        requests = payload.get("requests", [])
        if not isinstance(requests, list):
            return
        accepted = 0
        for request in requests:
            if not isinstance(request, dict):
                continue
            service = str(request.get("service", "") or "").strip()
            path = str(request.get("path", "") or "").strip()
            if not service or not path:
                continue
            self._enqueue_introspection_command(
                service,
                path,
                priority=int(request.get("priority", 100) or 100),
                source=str(request.get("source", "request") or "request"),
                reason=str(request.get("reason", "requested") or "requested"),
            )
            accepted += 1
        if accepted:
            self._introspection_queue_depth += accepted
            self._clear_introspection_request_payload()

    def _read_introspection_request_payload(self) -> dict[str, Any]:
        if not self.dbus_introspection_request_path:
            return {}
        try:
            with open(self.dbus_introspection_request_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _clear_introspection_request_payload(self) -> None:
        try:
            write_text_atomically(self.dbus_introspection_request_path, compact_json({"requests": []}))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug(
                "Unable to clear DBus introspection request payload %s: %s",
                self.dbus_introspection_request_path,
                error,
            )

    def _enqueue_introspection_command(
        self,
        service: str,
        path: str,
        *,
        priority: int,
        source: str,
        reason: str,
    ) -> None:
        self.commands.enqueue(
            {
                "kind": "introspect",
                "service": service,
                "path": path,
                "priority": "discovery" if priority < 90 else "optional",
                "source": source,
                "reason": reason,
                "timeout": float(self.config["DEFAULT"].get("DbusIntrospectionTimeoutSeconds", 1.0) or 1.0),
                "coalesce_key": f"introspect:{service}:{path}",
            }
        )

    def _enqueue_background_introspection_if_due(self) -> None:
        if not self.dbus_introspection_enabled:
            return
        now = time.time()
        interval = max(60.0, float(self.config["DEFAULT"].get("DbusIntrospectionFullScanIntervalSeconds", 21600.0)))
        if now - self._last_introspection_full_scan_at < interval:
            return
        if not self.cache.services or not self.circuit.allows_priority("discovery"):
            return
        self._last_introspection_full_scan_at = now
        for service, path, priority, source, reason in self._background_introspection_specs():
            self._enqueue_introspection_command(service, path, priority=priority, source=source, reason=reason)

    def _background_introspection_specs(self) -> list[tuple[str, str, int, str, str]]:
        defaults = self.config["DEFAULT"]
        specs: list[tuple[str, str, int, str, str]] = []
        grid_service = str(defaults.get("AutoGridService", "com.victronenergy.system")).strip()
        for path in (
            str(defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power")).strip(),
            str(defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power")).strip(),
            str(defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power")).strip(),
        ):
            if grid_service and path:
                specs.append((grid_service, path, 80, "grid", "configured-grid-path"))
        battery_path = str(defaults.get("AutoBatterySocPath", "/Soc")).strip()
        for service in self._configured_or_prefixed_services("AutoBatteryService", "AutoBatteryServicePrefix", "com.victronenergy.battery"):
            if battery_path:
                specs.append((service, battery_path, 70, "battery", "battery-service-discovery"))
        pv_path = str(defaults.get("AutoPvPath", "/Ac/Power")).strip()
        for service in self._configured_or_prefixed_services("AutoPvService", "AutoPvServicePrefix", "com.victronenergy.pvinverter"):
            if pv_path:
                specs.append((service, pv_path, 30, "pv", "pv-service-discovery"))
        return specs

    def _configured_or_prefixed_services(self, explicit_key: str, prefix_key: str, default_prefix: str) -> list[str]:
        defaults = self.config["DEFAULT"]
        explicit = str(defaults.get(explicit_key, "")).strip()
        if explicit:
            return [explicit] if explicit in self.cache.services else []
        prefix = str(defaults.get(prefix_key, default_prefix)).strip()
        return sorted(name for name in self.cache.services if name.startswith(prefix))[:10]

    def _install_signal_handlers(self) -> None:
        def _stop(_signum: int, _frame: object) -> None:
            self._stop = True
            if self._main_loop is not None:
                GLib.idle_add(self._main_loop.quit)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    def _start_socket(self) -> None:
        try:
            os.unlink(self.paths.socket_path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.paths.socket_path)
        server.listen(8)
        server.setblocking(False)
        self._server = server

    def _close_socket(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            os.unlink(self.paths.socket_path)
        except FileNotFoundError:
            pass

    def _process_socket_once(self) -> None:
        if self._server is None:
            return
        readable, _writable, _errors = select.select([self._server], [], [], 0.0)
        if not readable:
            return
        try:
            conn, _addr = self._server.accept()
        except BlockingIOError:
            return
        with conn:
            conn.settimeout(0.1)
            try:
                data = conn.recv(65536).decode("utf-8", errors="replace").strip()
            except socket.timeout:
                logging.debug("Gateway socket client connected without sending a request")
                return
            response = self._handle_socket_payload(data)
            conn.sendall((compact_json(response) + "\n").encode("utf-8"))

    def _handle_socket_payload(self, data: str) -> dict[str, Any]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as error:
            return {"ok": False, "error": str(error)}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "request must be an object"}
        request_type = str(payload.get("type") or payload.get("kind") or "")
        if request_type == "snapshot":
            return {"ok": True, "snapshot": self.cache.snapshot()}
        if request_type == "health":
            return {"ok": True, "dbus_health": self._health_snapshot()}
        if request_type == "refresh_value":
            self.commands.enqueue({**payload, "kind": "refresh_value", "source": payload.get("source", "socket")})
            return {"ok": True}
        if request_type == "refresh_services":
            self.commands.enqueue({**payload, "kind": "refresh_services", "source": payload.get("source", "socket")})
            return {"ok": True}
        if request_type in ("publish_desired", "publish_value", "set_value"):
            self.commands.enqueue({**payload, "kind": request_type, "source": payload.get("source", "socket")})
            return {"ok": True}
        return {"ok": False, "error": f"unsupported request type: {request_type}"}

    def _ensure_dbus_service(self) -> None:
        if self._dbusservice is not None:
            return
        self._dbusservice = VeDbusService(self.service_name, register=False)
        self._register_identity_paths()

    def _register_dbus_service_name(self) -> None:
        self._ensure_dbus_service()
        if self._dbusservice_registered:
            return
        self._dbusservice.register()
        self._dbusservice_registered = True
        logging.info("DBus adapter owns service %s", self.service_name)

    def _register_identity_paths(self) -> None:
        defaults = self.config["DEFAULT"]
        for path, value in self._identity_path_values(defaults).items():
            self._add_owned_path(path, value)

    def _identity_path_values(self, defaults: configparser.SectionProxy) -> dict[str, Any]:
        device_instance = self._device_instance(defaults)
        return {
            "/Mgmt/ProcessName": os.path.join(os.path.dirname(__file__), "venus_evcharger_service.py"),
            "/Mgmt/ProcessVersion": "Unknown version, and running on Python " + platform.python_version(),
            "/Mgmt/Connection": str(defaults.get("Connection", "Venus EV Charger Gateway")).strip(),
            "/DeviceInstance": device_instance,
            "/ProductId": 0xFFFF,
            "/ProductName": str(defaults.get("ProductName", "Venus EV Charger Service")).strip(),
            "/CustomName": str(defaults.get("CustomName", "Wallbox")).strip() or "Wallbox",
            "/FirmwareVersion": str(defaults.get("FirmwareVersion", "")).strip(),
            "/HardwareVersion": str(defaults.get("HardwareVersion", "")).strip(),
            "/Serial": str(defaults.get("Serial", f"gateway-{device_instance}")).strip(),
            "/Connected": 1 if self._configured_for_identity(defaults) else 0,
            "/Position": int(float(str(defaults.get("Position", "1")).strip() or "1")),
            "/UpdateIndex": 0,
        }

    @staticmethod
    def _device_instance(defaults: configparser.SectionProxy) -> int:
        try:
            return int(str(defaults.get("DeviceInstance", "60")).strip() or "60")
        except ValueError:
            return 60

    @staticmethod
    def _configured_for_identity(defaults: configparser.SectionProxy) -> bool:
        if str(defaults.get("Host", "")).strip():
            return True
        return any(
            str(defaults.get(key, "")).strip()
            for key in ("MeterConfigPath", "SwitchConfigPath", "ChargerConfigPath")
        )

    def _add_owned_path(self, path: str, value: Any) -> None:
        self._dbusservice.add_path(path, value)
        self.write_scheduler.registered_paths.add(path)
        self.write_scheduler.last_values[path] = value

    def _process_non_write_command(self, command: Mapping[str, Any]) -> CommandOutcome:
        kind = str(command.get("kind") or command.get("type") or "")
        if kind == "refresh_value":
            return self.read_executor.refresh_requested_value(command)
        if kind == "refresh_services":
            if self.circuit.state() != "ok":
                return "deferred"
            self.cache.update_services(self._list_services())
            return "applied"
        if kind == "introspect":
            if self.circuit.state() != "ok":
                return "deferred"
            return self._introspect_command(command)
        return "dropped"

    def _introspect_command(self, command: Mapping[str, Any]) -> CommandOutcome:
        service = str(command.get("service") or "")
        path = str(command.get("path") or "/")
        if not service:
            return "dropped"

        def _read() -> Any:
            obj = self.connection.bus().get_object(service, path, introspect=False)
            iface = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
            return iface.Introspect(timeout=float(command.get("timeout", 1.0)))

        try:
            xml_data = self._timed("introspection", _read)
        except DbusOperationDeferred:
            return "deferred"
        except Exception as error:  # pylint: disable=broad-except
            self.cache.mark_error(
                f"introspection:{service}:{path}",
                source=f"{service}{path}",
                error=error,
            )
            self._introspection_queue_depth = max(0, self._introspection_queue_depth - 1)
            logging.debug("Dropping failed DBus introspection command service=%s path=%s: %s", service, path, error)
            return "dropped"
        self.cache.update_value(f"introspection:{service}:{path}", xml_data, source=f"{service}{path}", confidence=0.5)
        self._introspection_queue_depth = max(0, self._introspection_queue_depth - 1)
        return "applied"

    def _poll_one_due_read_once(self) -> bool:
        now = time.time()
        due = self.read_scheduler.next_due(
            now=now,
            circuit_state=self.circuit.state(),
            priority_allowed=self.circuit.allows_priority,
        )
        if due is None:
            return False
        key, spec, interval = due
        outcome = self.read_executor.poll_read_spec(key, spec)
        if outcome == "applied":
            self.read_scheduler.record_success(key, now=now, interval=interval)
        elif outcome == "dropped":
            self.read_scheduler.record_error(key, now=now, interval=interval)
        return outcome != "deferred" or bool(self.read_executor.last_operation_performed)

    def _maybe_refresh_services(self) -> None:
        self._refresh_services_if_due_once()

    def _refresh_services_if_due_once(self) -> bool:
        now = time.time()
        if not self.discovery.due(now=now, priority_allowed=self.circuit.allows_priority):
            return False
        try:
            self.cache.update_services(self._list_services())
            self.discovery.record_success(now=now)
            return True
        except DbusOperationDeferred:
            return False
        except Exception as error:  # pylint: disable=broad-except
            self.discovery.record_error(error, now=now)
            return True

    def _list_services(self) -> list[str]:
        def _read() -> list[str]:
            obj = self.connection.bus().get_object("org.freedesktop.DBus", "/org/freedesktop/DBus", introspect=False)
            iface = dbus.Interface(obj, "org.freedesktop.DBus")
            return [str(name) for name in iface.ListNames()]

        return self._timed("read", _read)

    def _timed(self, kind: str, operation: Callable[[], Any]) -> Any:
        self.rate_limiter.require_due(kind)
        started = time.monotonic()
        try:
            result = operation()
            self.circuit.record_success((time.monotonic() - started) * 1000.0, kind=kind)
            return result
        except Exception as error:
            self.circuit.record_error(error, kind=kind)
            raise

    def _timed_local_publish(self, operation: Callable[[], Any]) -> Any:
        started = time.monotonic()
        try:
            result = operation()
            self.circuit.record_success((time.monotonic() - started) * 1000.0, kind="local_publish")
            return result
        except Exception as error:
            self.circuit.record_error(error, kind="local_publish")
            raise

    def _publish_cache(self) -> None:
        health = self._health_snapshot()
        self.cache.health.update(health)
        if self.cache_publish_interval_seconds > 0.0:
            now = time.monotonic()
            if (
                self.cache.sequence == self._last_cache_publish_sequence
                and now - self._last_cache_publish_monotonic < self.cache_publish_interval_seconds
            ):
                return
            self._last_cache_publish_monotonic = now
            self._last_cache_publish_sequence = self.cache.sequence
        self.cache.write_snapshot_files()
        self._append_health_log(health)
        self._write_introspection_snapshot()

    def _append_health_log(self, health: Mapping[str, Any]) -> None:
        if not self.health_log_path or self.health_log_interval_seconds <= 0.0:
            return
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_health_log_monotonic < self.health_log_interval_seconds:
            return
        self._last_health_log_monotonic = now_monotonic
        try:
            directory = os.path.dirname(self.health_log_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            queues = health.get("queues") if isinstance(health.get("queues"), Mapping) else {}
            eventloop = health.get("eventloop") if isinstance(health.get("eventloop"), Mapping) else {}
            cache_freshness = health.get("cache_freshness") if isinstance(health.get("cache_freshness"), Mapping) else {}
            backpressure = health.get("backpressure") if isinstance(health.get("backpressure"), Mapping) else {}
            payload = {
                "at": time.time(),
                "state": health.get("state", "unknown"),
                "backpressure": backpressure.get("state", "unknown"),
                "queue_oldest_age_s": queues.get("oldest_command_age_s", 0.0),
                "core_queue_oldest_age_s": queues.get("oldest_core_command_age_s", 0.0),
                "max_tick_gap_ms_60s": eventloop.get("max_tick_gap_ms_60s", 0.0),
                "timeouts_60s": health.get("timeouts_60s", 0),
                "cache_freshness": {
                    key: cache_freshness.get(key)
                    for key in (
                        "grid_power_w_age_s",
                        "grid_power_w_status",
                        "pv_power_w_age_s",
                        "pv_power_w_status",
                        "battery_soc_age_s",
                        "battery_soc_status",
                    )
                },
            }
            with open(self.health_log_path, "a", encoding="utf-8") as handle:
                handle.write(compact_json(payload) + "\n")
        except Exception:  # pylint: disable=broad-except
            logging.debug("Unable to append DBus gateway health history", exc_info=True)

    def _write_introspection_snapshot(self) -> None:
        if not self.dbus_introspection_enabled or not self.dbus_introspection_snapshot_path:
            return
        now = time.time()
        payload = {
            "schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION,
            "captured_at": now,
            "heartbeat_at": now,
            "worker_state": "gateway",
            "writer_pid": os.getpid(),
            "queue_depth": self._introspection_queue_depth,
            "last_full_scan_at": self._last_introspection_full_scan_at,
            "services": self._introspection_services_snapshot(now),
        }
        try:
            write_text_atomically(self.dbus_introspection_snapshot_path, compact_json(payload))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to write DBus introspection snapshot %s: %s", self.dbus_introspection_snapshot_path, error)

    def _introspection_services_snapshot(self, now: float) -> dict[str, Any]:
        services: dict[str, Any] = {}
        for key, entry in self.cache.values.items():
            if not key.startswith("introspection:") or not isinstance(entry, dict):
                continue
            service, path = self._split_introspection_cache_key(key)
            if not service:
                continue
            service_payload = services.setdefault(service, {"paths": {}, "last_updated_at": now})
            paths = service_payload.setdefault("paths", {})
            if isinstance(paths, dict):
                paths[path] = self._introspection_finding(entry, now)
            service_payload["last_updated_at"] = max(
                float(service_payload.get("last_updated_at", 0.0) or 0.0),
                float(entry.get("updated_at", now) or now),
            )
        return services

    @staticmethod
    def _split_introspection_cache_key(key: str) -> tuple[str, str]:
        remainder = key[len("introspection:") :]
        service, separator, path = remainder.partition(":")
        return service, path if separator else "/"

    @staticmethod
    def _introspection_finding(entry: Mapping[str, Any], now: float) -> dict[str, Any]:
        status = str(entry.get("status", "") or "")
        if status == "fresh":
            interfaces, children = DbusAdapter._parse_introspection_xml(entry.get("value", ""))
            return {
                "status": "fresh",
                "confidence": entry.get("confidence", 0.8),
                "interfaces": interfaces,
                "children": children,
                "source": entry.get("source", "gateway"),
                "reason": "gateway-introspection",
                "last_success_at": entry.get("updated_at", now),
                "last_error": "",
                "retry_after": now,
            }
        return {
            "status": "unresponsive-backoff" if status == "error" else status or "unknown",
            "confidence": 0.55,
            "interfaces": [],
            "children": [],
            "source": entry.get("source", "gateway"),
            "reason": "gateway-introspection",
            "last_success_at": None,
            "last_error": str(entry.get("last_error", "") or ""),
            "retry_after": now + 900.0,
        }

    @staticmethod
    def _parse_introspection_xml(xml_data: object) -> tuple[list[str], list[str]]:
        try:
            root = xml_et.fromstring(str(xml_data))
        except Exception:
            return [], []
        interfaces = [str(node.attrib.get("name", "")) for node in root.findall("interface") if node.attrib.get("name")]
        children = [str(node.attrib.get("name", "")) for node in root.findall("node") if node.attrib.get("name")]
        return interfaces, children

    def _health_snapshot(self) -> dict[str, Any]:
        current_monotonic = time.monotonic()
        current_time = time.time()
        pending = self.commands.load_pending()
        effective_pending = DbusCommandInbox.coalesce(pending)
        core_pending = self.core_commands.load_pending()
        write_scheduler_health = self.write_scheduler.health(now=current_time)
        queue_health = self._queue_health(
            effective_pending,
            core_pending,
            current_time,
            physical_count=len(pending),
            write_scheduler_health=write_scheduler_health,
        )
        cache_freshness = self._cache_freshness(current_time)
        slo = self._slo_snapshot(
            queue_health=queue_health,
            cache_freshness=cache_freshness,
            now=current_time,
            current_monotonic=current_monotonic,
        )
        heartbeat_age = (
            max(0.0, current_monotonic - self._last_tick_monotonic)
            if self._last_tick_monotonic > 0.0
            else 0.0
        )
        return {
            **self.circuit.health(),
            "pending_command_count": len(effective_pending),
            "physical_command_count": len(pending),
            "core_command_count": len(core_pending),
            "registered_path_count": len(self.write_scheduler.registered_paths),
            "last_tick_at": self._last_tick_at,
            "tick_duration_ms": self._last_tick_duration_ms,
            "discovery_last_success_at": self.discovery.last_success_at,
            "discovery_last_error": self.discovery.last_error,
            "discovery_next_scan_at": self.discovery.next_scan_at,
            "mainloop_heartbeat_age_s": heartbeat_age,
            "queues": queue_health,
            "queue_classes": self._queue_class_health(effective_pending, current_time),
            "write_scheduler": write_scheduler_health,
            "cache_freshness": cache_freshness,
            "slo": slo,
            "backpressure": self._backpressure_snapshot(slo=slo, queue_health=queue_health),
            "resources": self._last_resource_snapshot or self.resource_monitor.snapshot(),
            "adaptive_tick_seconds": self.tick_seconds,
            "min_tick_seconds": self.min_tick_seconds,
            "max_tick_seconds": self.max_tick_seconds,
            "eventloop": {
                "last_tick_at": self._last_tick_at,
                "tick_duration_ms": self._last_tick_duration_ms,
                "mainloop_heartbeat_age_s": heartbeat_age,
                **self.tick_health.snapshot(now=current_monotonic),
            },
        }

    @staticmethod
    def _queue_class_health(pending: list[tuple[str, dict[str, Any]]], now: float) -> dict[str, Any]:
        classes: dict[str, dict[str, Any]] = {}
        for _path, command in pending:
            queue_class = str(command.get("queue_class") or command_queue_class(command))
            entry = classes.setdefault(queue_class, {"pending": 0, "oldest_age_s": 0.0})
            entry["pending"] = int(entry["pending"]) + 1
            entry["oldest_age_s"] = max(
                float(entry["oldest_age_s"]),
                max(0.0, now - DbusAdapter._command_activity_at(command, now)),
            )
        return dict(sorted(classes.items()))

    @staticmethod
    def _queue_health(
        pending: list[tuple[str, dict[str, Any]]],
        core_pending: list[tuple[str, dict[str, Any]]],
        now: float,
        *,
        physical_count: int | None = None,
        write_scheduler_health: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        scheduler = write_scheduler_health or {}
        return {
            "pending_command_count": len(pending),
            "physical_command_count": len(pending) if physical_count is None else int(physical_count),
            "oldest_command_age_s": DbusAdapter._oldest_command_age(pending, now),
            "core_command_count": len(core_pending),
            "oldest_core_command_age_s": DbusAdapter._oldest_command_age(core_pending, now),
            "processed_commands_60s": int(scheduler.get("processed_commands_60s", 0) or 0),
            "queue_drain_rate_per_s": float(scheduler.get("processed_commands_60s", 0) or 0) / 60.0,
            "last_processed_at": float(scheduler.get("last_processed_at", 0.0) or 0.0),
        }

    @staticmethod
    def _oldest_command_age(commands: list[tuple[str, dict[str, Any]]], now: float) -> float:
        ages = [
            max(0.0, now - DbusAdapter._command_activity_at(command, now))
            for _path, command in commands
        ]
        return max(ages) if ages else 0.0

    @staticmethod
    def _command_activity_at(command: Mapping[str, Any], now: float) -> float:
        timestamp = command.get("updated_at") if command.get("updated_at") is not None else command.get("created_at")
        try:
            return float(timestamp if timestamp is not None else now)
        except (TypeError, ValueError):
            return now

    def _cache_freshness(self, now: float) -> dict[str, Any]:
        values = {
            key: self.cache._value_snapshot(value, now)  # pylint: disable=protected-access
            for key, value in self.cache.values.items()
        }
        status_counts: dict[str, int] = {}
        for value in values.values():
            status = str(value.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1
        important = {
            f"{key}_age_s": float(values.get(key, {}).get("age_s", 0.0) or 0.0)
            for key in ("grid_power_w", "pv_power_w", "battery_soc")
        }
        important.update(
            {
                f"{key}_status": str(values.get(key, {}).get("status", "missing"))
                for key in ("grid_power_w", "pv_power_w", "battery_soc")
            }
        )
        return {"value_count": len(values), "status_counts": status_counts, **important}

    def _slo_snapshot(
        self,
        *,
        queue_health: Mapping[str, Any],
        cache_freshness: Mapping[str, Any],
        now: float,
        current_monotonic: float,
    ) -> dict[str, Any]:
        eventloop = self.tick_health.snapshot(now=current_monotonic)
        gui_age = self._max_cached_path_age(GUI_CRITICAL_PUBLISH_PATHS, now)
        core_read_age = self._max_core_read_age(cache_freshness)
        queue_age = float(queue_health.get("oldest_command_age_s", 0.0) or 0.0)
        eventloop_gap_ms = float(eventloop.get("max_tick_gap_ms_60s", 0.0) or 0.0)
        checks = {
            "gui_fresh": gui_age <= self.slo_gui_max_age_seconds,
            "core_reads_fresh": core_read_age <= self.slo_core_read_max_age_seconds,
            "queue_age_ok": queue_age <= self.slo_queue_max_age_seconds,
            "mainloop_gap_ok": eventloop_gap_ms <= self.slo_mainloop_gap_max_ms,
        }
        violated = [name for name, ok in checks.items() if not ok]
        return {
            "state": "violated" if violated else "ok",
            "violated": violated,
            "checks": checks,
            "targets": {
                "gui_max_age_s": self.slo_gui_max_age_seconds,
                "core_read_max_age_s": self.slo_core_read_max_age_seconds,
                "queue_max_age_s": self.slo_queue_max_age_seconds,
                "mainloop_gap_max_ms": self.slo_mainloop_gap_max_ms,
            },
            "observed": {
                "gui_max_age_s": gui_age,
                "core_read_max_age_s": core_read_age,
                "queue_oldest_age_s": queue_age,
                "mainloop_max_gap_ms_60s": eventloop_gap_ms,
            },
        }

    def _backpressure_snapshot(
        self,
        *,
        slo: Mapping[str, Any],
        queue_health: Mapping[str, Any],
    ) -> dict[str, Any]:
        circuit_state = self.circuit.state()
        queue_age = float(queue_health.get("oldest_command_age_s", 0.0) or 0.0)
        violated = list(slo.get("violated", []) or [])
        reasons: list[str] = []
        if circuit_state != "ok":
            reasons.append(f"dbus-{circuit_state}")
        if queue_age > self.slo_queue_max_age_seconds:
            reasons.append("queue-age")
        reasons.extend(str(item) for item in violated)
        if circuit_state == "protective":
            state = "protective"
        elif circuit_state == "degraded" or queue_age > self.slo_queue_max_age_seconds * 2.0:
            state = "slow"
        elif reasons:
            state = "congested"
        else:
            state = "ok"
        return {
            "state": state,
            "core_should_throttle": state != "ok",
            "suppress_optional_commands": state in {"slow", "protective"},
            "prefer_coalescing": state != "ok",
            "reason": ",".join(dict.fromkeys(reasons)) if reasons else "ok",
        }

    def _apply_slo_regulation(self) -> None:
        now = time.time()
        pending = DbusCommandInbox.coalesce(self.commands.load_pending())
        queue_age = self._oldest_command_age(pending, now)
        cache_freshness = self._cache_freshness(now)
        core_read_age = self._max_core_read_age(cache_freshness)
        eventloop_gap_ms = float(self.tick_health.snapshot().get("max_tick_gap_ms_60s", 0.0) or 0.0)
        burst = self.write_scheduler.local_publish_burst_limit
        if queue_age > self.slo_queue_max_age_seconds:
            burst = min(max(burst * 3, burst + 4), 50)
        if eventloop_gap_ms > self.slo_mainloop_gap_max_ms:
            burst = max(1, min(burst, max(1, self.write_scheduler.local_publish_burst_limit // 2)))
        self.write_scheduler.set_dynamic_local_publish_burst(burst)
        if core_read_age > self.slo_core_read_max_age_seconds:
            self.read_scheduler.force_due(FAST_READ_KEYS)
        if self.circuit.state() != "ok":
            quiet_until = now + 60.0
            self.discovery.next_scan_at = max(self.discovery.next_scan_at, quiet_until)
            self._last_introspection_full_scan_at = max(self._last_introspection_full_scan_at, now)

    def _max_cached_path_age(self, paths: set[str], now: float) -> float:
        ages: list[float] = []
        for path in paths:
            entry = self.cache.values.get(dbus_path_key(self.service_name, path))
            if isinstance(entry, Mapping):
                updated_at = float(entry.get("updated_at", 0.0) or 0.0)
                if updated_at > 0.0:
                    ages.append(max(0.0, now - updated_at))
        return max(ages) if ages else 0.0

    @staticmethod
    def _max_core_read_age(cache_freshness: Mapping[str, Any]) -> float:
        ages = [
            float(cache_freshness.get(f"{key}_age_s", 0.0) or 0.0)
            for key in ("grid_power_w", "pv_power_w", "battery_soc")
            if f"{key}_age_s" in cache_freshness
        ]
        return max(ages) if ages else 0.0

    @staticmethod
    def _json_ready(value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)


def _logging_level_from_config(config: configparser.ConfigParser) -> int:
    value = str(config["DEFAULT"].get("Logging", "INFO")).strip().upper()
    return getattr(logging, value, logging.INFO)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Venus EV charger DBus adapter.")
    parser.add_argument("config_path", nargs="?", default="/data/etc/venus-evcharger-service/config.ini")
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args(argv)
    config = configparser.ConfigParser()
    config.read(args.config_path)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d dbus-adapter] %(message)s",
        level=_logging_level_from_config(config),
    )
    adapter = DbusAdapter(args.config_path, paths=gateway_paths(args.run_dir or None))
    adapter.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entrypoint
    raise SystemExit(main())
