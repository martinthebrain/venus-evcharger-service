#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import platform
import select
import signal
import socket
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from gi.repository import GLib
from vedbus import VeDbusService

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_adapter_process_protocols import DbusAdapterRuntimeContext


class DbusAdapterRuntimeMixin:
    _server: socket.socket | None

    def _install_signal_handlers(self: DbusAdapterRuntimeContext) -> None:
        def _stop(_signum: int, _frame: object) -> None:
            self._stop = True
            if self._main_loop is not None:
                GLib.idle_add(self._main_loop.quit)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    def _start_socket(self: DbusAdapterRuntimeContext) -> None:
        with suppress(FileNotFoundError):
            os.unlink(self.paths.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.paths.socket_path)
        server.listen(8)
        server.setblocking(False)
        self._server = server

    def _close_socket(self: DbusAdapterRuntimeContext) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        with suppress(FileNotFoundError):
            os.unlink(self.paths.socket_path)

    def _process_socket_once(self: DbusAdapterRuntimeContext) -> None:
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
            except TimeoutError:
                logging.debug("Gateway socket client connected without sending a request")
                return
            response = self._handle_socket_payload(data)
            conn.sendall((compact_json(response) + "\n").encode("utf-8"))

    def _handle_socket_payload(self: DbusAdapterRuntimeContext, data: str) -> dict[str, Any]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as error:
            return {"ok": False, "error": str(error)}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "request must be an object"}
        request_type = str(payload.get("type") or payload.get("kind") or "")
        handler = self._socket_handlers().get(request_type, self._unsupported_socket_request)
        return handler(payload, request_type)

    def _socket_handlers(self: DbusAdapterRuntimeContext) -> dict[str, Callable[[dict[str, Any], str], dict[str, Any]]]:
        return {
            "snapshot": self._socket_snapshot,
            "health": self._socket_health,
            "refresh_value": self._socket_enqueue,
            "refresh_services": self._socket_enqueue,
            "publish_desired": self._socket_enqueue,
            "publish_value": self._socket_enqueue,
            "set_value": self._socket_enqueue,
        }

    def _socket_snapshot(
        self: DbusAdapterRuntimeContext,
        _payload: dict[str, Any],
        _request_type: str,
    ) -> dict[str, Any]:
        return {"ok": True, "snapshot": self.cache.snapshot()}

    def _socket_health(
        self: DbusAdapterRuntimeContext,
        _payload: dict[str, Any],
        _request_type: str,
    ) -> dict[str, Any]:
        return {"ok": True, "dbus_health": self._health_snapshot()}

    def _socket_enqueue(
        self: DbusAdapterRuntimeContext,
        payload: dict[str, Any],
        request_type: str,
    ) -> dict[str, Any]:
        self.commands.enqueue({**payload, "kind": request_type, "source": payload.get("source", "socket")})
        return {"ok": True}

    @staticmethod
    def _unsupported_socket_request(_payload: dict[str, Any], request_type: str) -> dict[str, Any]:
        return {"ok": False, "error": f"unsupported request type: {request_type}"}

    def _ensure_dbus_service(self: DbusAdapterRuntimeContext) -> None:
        if self._dbusservice is not None:
            return
        self._dbusservice = VeDbusService(self.service_name, register=False)
        self._register_identity_paths()

    def _register_dbus_service_name(self: DbusAdapterRuntimeContext) -> None:
        self._ensure_dbus_service()
        if self._dbusservice_registered:
            return
        self._dbusservice.register()
        self._dbusservice_registered = True
        logging.info("DBus adapter owns service %s", self.service_name)

    def _register_identity_paths(self: DbusAdapterRuntimeContext) -> None:
        defaults = self.config["DEFAULT"]
        for path, value in self._identity_path_values(defaults).items():
            self._add_owned_path(path, value)

    def _identity_path_values(
        self: DbusAdapterRuntimeContext,
        defaults: configparser.SectionProxy,
    ) -> dict[str, Any]:
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

    def _add_owned_path(self: DbusAdapterRuntimeContext, path: str, value: Any) -> None:
        self._dbusservice.add_path(path, value)
        self.write_scheduler.registered_paths.add(path)
        self.write_scheduler.last_values[path] = value
