#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""EV-charger DBus service identity for the dedicated adapter process."""

from __future__ import annotations

import configparser
import logging
import os
import platform

from vedbus import VeDbusService

from venus_evcharger.dbus_adapter_process_config import configured_device_instance
from venus_evcharger.dbus_adapter_process_protocol_runtime import DbusAdapterIdentityContext
from venus_evcharger.dbus_adapter_service_protocol import DbusServiceLike
from venus_evcharger.dbus_gateway_command_types import CommandPayload


class DbusAdapterIdentity:
    _dbusservice: DbusServiceLike | None

    @property
    def dbus_service(self: DbusAdapterIdentityContext) -> DbusServiceLike:
        self.ensure_dbus_service()
        service = self._dbusservice
        assert service is not None
        return service

    @property
    def dbus_service_registered(self: DbusAdapterIdentityContext) -> bool:
        return bool(self._dbusservice_registered)

    def set_dbus_service(
        self: DbusAdapterIdentityContext,
        service: DbusServiceLike,
        *,
        registered: bool = False,
    ) -> None:
        self._dbusservice = service
        self._dbusservice_registered = bool(registered)

    def ensure_dbus_service(self: DbusAdapterIdentityContext) -> None:
        if self._dbusservice is not None:
            return
        self.set_dbus_service(VeDbusService(self.service_name, register=False))
        self.register_identity_paths()

    def register_dbus_service_name(self: DbusAdapterIdentityContext) -> None:
        service = self.dbus_service
        if self.dbus_service_registered:
            return
        service.register()
        self._dbusservice_registered = True
        logging.info("DBus adapter owns service %s", self.service_name)

    def register_identity_paths(self: DbusAdapterIdentityContext) -> None:
        defaults = self.config["DEFAULT"]
        for path, value in self.identity_path_values(defaults).items():
            self.add_owned_path(path, value)

    def identity_path_values(
        self: DbusAdapterIdentityContext,
        defaults: configparser.SectionProxy,
    ) -> CommandPayload:
        device_instance = configured_device_instance(defaults)
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
            "/Connected": 1 if self.configured_for_identity(defaults) else 0,
            "/Position": int(float(str(defaults.get("Position", "1")).strip() or "1")),
            "/UpdateIndex": 0,
        }

    @staticmethod
    def configured_for_identity(defaults: configparser.SectionProxy) -> bool:
        if str(defaults.get("Host", "")).strip():
            return True
        return any(
            str(defaults.get(key, "")).strip()
            for key in ("MeterConfigPath", "SwitchConfigPath", "ChargerConfigPath")
        )

    def add_owned_path(self: DbusAdapterIdentityContext, path: str, value: object) -> None:
        self.dbus_service.add_path(path, value)
        self.write_scheduler.registered_paths.add(path)
        self.write_scheduler.last_values[path] = value
