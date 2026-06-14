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
import select
import signal
import socket
import time
from typing import Any, Callable, Mapping

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from vedbus import VeDbusService

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_adapter_components import (
    AtomicJsonWriter,
    CommandOutcome,
    DbusCircuitBreaker,
    DbusConnectionManager,
    DbusDiscoveryManager,
    DbusOperationDeferred,
    DbusRateLimiter,
    DbusReadScheduler,
)
from venus_evcharger.dbus_adapter_read import DbusReadExecutor
from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_gateway import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    DbusCacheStore,
    DbusCommandInbox,
    GatewayPaths,
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
        self.write_scheduler = DbusWriteScheduler(self)
        self._stop = False
        self._server: socket.socket | None = None
        self._main_loop: Any = None
        self.read_scheduler = DbusReadScheduler(self._configured_read_specs(defaults))
        self.read_executor = DbusReadExecutor(self)
        self.tick_seconds = max(0.1, float(defaults.get("DbusGatewayTickSeconds", 0.5)))
        self.discovery = DbusDiscoveryManager(
            interval_seconds=float(defaults.get("DbusGatewayServiceListIntervalSeconds", 900.0))
        )
        self.json_writer = AtomicJsonWriter()
        self.cache_publish_interval_seconds = max(
            0.0,
            float(defaults.get("DbusGatewayCachePublishIntervalSeconds", 0.0)),
        )
        self._last_cache_publish_monotonic = 0.0
        self._last_cache_publish_sequence = -1
        self._last_tick_at = 0.0
        self._last_tick_monotonic = 0.0
        self._last_tick_duration_ms = 0.0

    @staticmethod
    def _load_config(path: str) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.optionxform = str  # type: ignore[method-assign]
        parser.read(path)
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
                "service": str(defaults.get("AutoBatteryService", "com.victronenergy.system")).strip(),
                "path": str(defaults.get("AutoBatterySocPath", "/Dc/Battery/Soc")).strip(),
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
        GLib.timeout_add(max(100, int(self.tick_seconds * 1000)), self._tick)
        try:
            self._main_loop.run()
        finally:
            self._stop = True
            self._close_socket()

    def _tick(self) -> bool:
        tick_started = time.monotonic()
        self._last_tick_at = time.time()
        self._last_tick_monotonic = tick_started
        if self._stop:
            self._close_socket()
            return False
        try:
            self._process_socket_once()
            self._process_one_dbus_operation_once()
            self._publish_cache()
        except Exception as error:  # pylint: disable=broad-except
            self.circuit.record_error(error)
            logging.exception("DBus adapter tick failed: %s", error)
        finally:
            self._last_tick_duration_ms = (time.monotonic() - tick_started) * 1000.0
        return not self._stop

    def _process_one_dbus_operation_once(self) -> bool:
        return (
            self.write_scheduler.process_one()
            or self._poll_one_due_read_once()
            or self._refresh_services_if_due_once()
        )

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
        self._dbusservice.register()
        logging.info("DBus adapter owns service %s", self.service_name)

    def _process_non_write_command(self, command: Mapping[str, Any]) -> CommandOutcome:
        kind = str(command.get("kind") or command.get("type") or "")
        if kind == "refresh_value":
            return self.read_executor.refresh_requested_value(command)
        if kind == "refresh_services":
            self.cache.update_services(self._list_services())
            return "applied"
        if kind == "introspect":
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

        xml_data = self._timed("introspection", _read)
        self.cache.update_value(f"introspection:{service}:{path}", xml_data, source=f"{service}{path}", confidence=0.5)
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
        return True

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
            self.circuit.record_error(error)
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
            self.circuit.record_success((time.monotonic() - started) * 1000.0)
            return result
        except Exception as error:
            self.circuit.record_error(error)
            raise

    def _publish_cache(self) -> None:
        self.cache.health.update(self._health_snapshot())
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

    def _health_snapshot(self) -> dict[str, Any]:
        current_monotonic = time.monotonic()
        return {
            **self.circuit.health(),
            "pending_command_count": len(self.commands.load_pending()),
            "core_command_count": len(self.core_commands.load_pending()),
            "registered_path_count": len(self.write_scheduler.registered_paths),
            "last_tick_at": self._last_tick_at,
            "tick_duration_ms": self._last_tick_duration_ms,
            "discovery_last_success_at": self.discovery.last_success_at,
            "discovery_last_error": self.discovery.last_error,
            "discovery_next_scan_at": self.discovery.next_scan_at,
            "mainloop_heartbeat_age_s": (
                max(0.0, current_monotonic - self._last_tick_monotonic)
                if self._last_tick_monotonic > 0.0
                else 0.0
            ),
        }

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
