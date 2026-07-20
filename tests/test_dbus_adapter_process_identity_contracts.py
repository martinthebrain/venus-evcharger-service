#!/usr/bin/env python3
"""Behavioral contracts for the adapter-owned DBus service identity."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs


class FakeVeDbusService(dict[str, object]):
    def __init__(self, name: str, *, register: bool) -> None:
        super().__init__()
        self.name = name
        self.register_argument = register
        self.register_calls = 0
        self.added: list[tuple[str, object]] = []

    def register(self) -> None:
        self.register_calls += 1

    def add_path(self, path: str, value: object) -> None:
        self.added.append((path, value))
        self[path] = value


install_venus_adapter_stubs()

import venus_evcharger.dbus_adapter.process.identity as identity
from venus_evcharger.dbus_adapter.process.config import CasePreservingConfigParser
from venus_evcharger.dbus_gateway import DbusCacheStore, dbus_path_key


def adapter(values: dict[str, str] | None = None) -> identity.DbusAdapterIdentity:
    instance = object.__new__(identity.DbusAdapterIdentity)
    instance.config = CasePreservingConfigParser()
    instance.config["DEFAULT"] = values or {}
    instance.service_name = "com.victronenergy.evcharger.http_60"
    instance._dbusservice = None
    instance._dbusservice_registered = False
    instance.cache = DbusCacheStore()
    instance.write_scheduler = SimpleNamespace(registered_paths=set(), last_values={})
    return instance


class DbusAdapterProcessIdentityContractTests(unittest.TestCase):
    def test_set_service_and_properties_preserve_registration_state(self) -> None:
        instance = adapter()
        service = FakeVeDbusService("test", register=False)
        instance.set_dbus_service(service, registered=True)
        self.assertIs(instance.dbus_service, service)
        self.assertIs(instance.dbus_service_registered, True)
        self.assertIs(instance.cache.local_service_registered, True)
        self.assertEqual(instance.cache.local_service_name, instance.service_name)
        instance.set_dbus_service(service, registered=False)
        self.assertIs(instance.dbus_service_registered, False)
        self.assertIs(instance.cache.local_service_registered, False)

    def test_ensure_service_creates_once_and_registers_identity_paths(self) -> None:
        instance = adapter()
        with (
            patch.object(identity, "VeDbusService", FakeVeDbusService),
            patch.object(instance, "register_identity_paths") as register_paths,
        ):
            instance.ensure_dbus_service()
            service = instance._dbusservice
            instance.ensure_dbus_service()
        self.assertIsInstance(service, FakeVeDbusService)
        assert isinstance(service, FakeVeDbusService)
        self.assertEqual(service.name, instance.service_name)
        self.assertIs(service.register_argument, False)
        register_paths.assert_called_once_with()

    def test_register_service_name_is_idempotent_and_marks_owned(self) -> None:
        instance = adapter()
        service = FakeVeDbusService("test", register=False)
        instance.set_dbus_service(service)
        with patch.object(identity.logging, "info") as info:
            instance.register_dbus_service_name()
            instance.register_dbus_service_name()
        self.assertEqual(service.register_calls, 1)
        self.assertIs(instance.dbus_service_registered, True)
        self.assertIs(instance.cache.local_service_registered, True)
        info.assert_called_once_with("DBus adapter owns service %s", instance.service_name)

    def test_default_identity_payload_is_exact(self) -> None:
        instance = adapter()
        with patch.object(identity.platform, "python_version", return_value="3.test"):
            payload = instance.identity_path_values(instance.config["DEFAULT"])
        self.assertEqual(
            payload,
            {
                "/Mgmt/ProcessName": os.path.join(os.path.dirname(identity.__file__), "venus_evcharger_service.py"),
                "/Mgmt/ProcessVersion": "Unknown version, and running on Python 3.test",
                "/Mgmt/Connection": "Venus EV Charger Gateway",
                "/DeviceInstance": 60,
                "/ProductId": 0xFFFF,
                "/ProductName": "Venus EV Charger Service",
                "/CustomName": "Wallbox",
                "/FirmwareVersion": "",
                "/HardwareVersion": "",
                "/Serial": "gateway-60",
                "/Connected": 0,
                "/Position": 1,
                "/UpdateIndex": 0,
            },
        )

    def test_custom_identity_payload_strips_values_and_reports_connection(self) -> None:
        instance = adapter(
            {
                "DeviceInstance": "61",
                "Connection": " custom connection ",
                "ProductName": " custom product ",
                "CustomName": " custom wallbox ",
                "FirmwareVersion": " 1.2.3 ",
                "HardwareVersion": " hw-2 ",
                "Serial": " serial-1 ",
                "Position": " 2 ",
                "MeterConfigPath": " /data/meter.ini ",
            }
        )
        with patch.object(identity.platform, "python_version", return_value="3.test"):
            payload = instance.identity_path_values(instance.config["DEFAULT"])
        self.assertEqual(payload["/Mgmt/Connection"], "custom connection")
        self.assertEqual(payload["/DeviceInstance"], 61)
        self.assertEqual(payload["/ProductName"], "custom product")
        self.assertEqual(payload["/CustomName"], "custom wallbox")
        self.assertEqual(payload["/FirmwareVersion"], "1.2.3")
        self.assertEqual(payload["/HardwareVersion"], "hw-2")
        self.assertEqual(payload["/Serial"], "serial-1")
        self.assertEqual(payload["/Connected"], 1)
        self.assertEqual(payload["/Position"], 2)
        blank_values = adapter({"CustomName": " ", "Position": " "})
        blank_payload = blank_values.identity_path_values(blank_values.config["DEFAULT"])
        self.assertEqual(blank_payload["/CustomName"], "Wallbox")
        self.assertEqual(blank_payload["/Position"], 1)

    def test_configured_identity_accepts_host_or_each_backend_path(self) -> None:
        self.assertFalse(identity.DbusAdapterIdentity.configured_for_identity(adapter().config["DEFAULT"]))
        for key in ("Host", "MeterConfigPath", "SwitchConfigPath", "ChargerConfigPath"):
            with self.subTest(key=key):
                self.assertTrue(
                    identity.DbusAdapterIdentity.configured_for_identity(adapter({key: " configured "}).config["DEFAULT"])
                )
        self.assertFalse(identity.DbusAdapterIdentity.configured_for_identity(adapter({"Host": " "}).config["DEFAULT"]))

    def test_register_paths_and_add_owned_path_update_all_owners(self) -> None:
        instance = adapter()
        service = FakeVeDbusService("test", register=False)
        instance.set_dbus_service(service)
        with patch.object(instance, "identity_path_values", return_value={"/A": 1, "/B": "two"}) as path_values:
            instance.register_identity_paths()
        path_values.assert_called_once_with(instance.config["DEFAULT"])
        self.assertEqual(service.added, [("/A", 1), ("/B", "two")])
        self.assertEqual(instance.write_scheduler.registered_paths, {"/A", "/B"})
        self.assertEqual(instance.write_scheduler.last_values, {"/A": 1, "/B": "two"})
        cached_a = instance.cache.values[dbus_path_key(instance.service_name, "/A")]
        cached_b = instance.cache.values[dbus_path_key(instance.service_name, "/B")]
        self.assertEqual(cached_a["value"], 1)
        self.assertEqual(cached_b["value"], "two")
        self.assertEqual(cached_a["freshness_kind"], "local_owned")


if __name__ == "__main__":
    unittest.main()
