"""Gateway-owned execution tests for generic Shelly configuration."""

from __future__ import annotations

import configparser
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, cast
from unittest.mock import patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.semantic import SemanticWriteExecutor
from venus_evcharger.dbus_gateway import DbusCacheStore, gateway_paths, read_json_file
from venus_evcharger.dbus_gateway_policy import command_queue_class
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.generic_shelly_configuration import disable_matching_generic_shelly_once_command
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyDeviceSelector,
    GenericShellySelectorKind,
)

_T = TypeVar("_T")


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def get_object(self, service: str, path: str, *, introspect: bool) -> object:
        self.calls.append((service, path, introspect))
        return object()


class _Interface:
    def __init__(self, *, value: object = None, xml: object = "<node/>") -> None:
        self.value = value
        self.xml = xml
        self.get_calls: list[float] = []
        self.set_calls: list[tuple[object, float]] = []
        self.introspection_calls: list[float] = []

    def GetValue(self, *, timeout: float) -> object:  # noqa: N802 - external API
        self.get_calls.append(timeout)
        return self.value

    def SetValue(self, value: object, *, timeout: float) -> None:  # noqa: N802 - external API
        self.set_calls.append((value, timeout))

    def Introspect(self, *, timeout: float) -> object:  # noqa: N802 - external API
        self.introspection_calls.append(timeout)
        return self.xml


class _Adapter:
    def __init__(self, root: Path, *, service: str = "com.example.generic-shelly") -> None:
        self.config = configparser.ConfigParser()
        self.config.read_dict({"DEFAULT": {"GenericShellyService": service}})
        self.connection = _Connection()
        self.cache = DbusCacheStore(gateway_paths(str(root / "run")))
        self.json_writer = AtomicJsonWriter()
        self.operations: list[str] = []

    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T:
        self.operations.append(kind)
        return operation()


def _scheduler(adapter: _Adapter) -> SemanticWriteExecutor:
    return SemanticWriteExecutor(cast(SemanticWriteAdapter, adapter))


def _request(*, kind: str = "ip", value: str = "192.0.2.7") -> CommandPayload:
    selector = GenericShellyDeviceSelector(cast(GenericShellySelectorKind, kind), value)
    return disable_matching_generic_shelly_once_command(DisableMatchingGenericShellyOnceRequest(selector, 2))


def _write(path: Path, command: CommandMapping) -> None:
    AtomicJsonWriter().write(str(path), command)


def _load(path: Path) -> CommandPayload:
    payload = read_json_file(str(path))
    assert isinstance(payload, dict)
    return payload


class GenericShellyGatewayConfigurationTests(unittest.TestCase):
    def test_discovery_and_ip_matching_advance_one_dbus_operation_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = _request()
            _write(path, command)
            interface = _Interface(xml='<node><node name="first"/><node/><node name="second"/></node>')
            with patch("venus_evcharger.dbus_adapter.write.generic_shelly.dbus.Interface", return_value=interface):
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
                self.assertEqual(adapter.operations, ["introspection"])
                command = _load(path)
                self.assertEqual(
                    (command["phase"], command["devices"], command["cursor"]),
                    ("identify", ["first", "second"], 0),
                )

                adapter.operations.clear()
                interface.value = "192.0.2.8"
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
                self.assertEqual(adapter.operations, ["read"])
                command = _load(path)
                self.assertEqual(command["cursor"], 1)

                adapter.operations.clear()
                interface.value = "192.0.2.7"
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
                self.assertEqual(adapter.operations, ["read"])
                command = _load(path)
                self.assertEqual((command["phase"], command["matched_device"]), ("enabled", "second"))

    def test_enabled_read_and_disable_write_are_separate_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = {
                **_request(),
                "phase": "enabled",
                "devices": ["device"],
                "cursor": 0,
                "matched_device": "device",
            }
            _write(path, command)
            interface = _Interface(value=1)
            with patch("venus_evcharger.dbus_adapter.write.generic_shelly.dbus.Interface", return_value=interface):
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
                self.assertEqual(adapter.operations, ["read"])
                command = _load(path)
                self.assertEqual(command["phase"], "disable")

                adapter.operations.clear()
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "applied")
                self.assertEqual(adapter.operations, ["write"])
            self.assertEqual(interface.set_calls, [(0, 1.0)])
            self.assertEqual(adapter.connection.calls[-1], ("com.example.generic-shelly", "/Devices/device/2/Enabled", False))

    def test_mac_serial_match_needs_no_read_and_already_disabled_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = {
                **_request(kind="mac", value="AABBCCDDEEFF"),
                "phase": "identify",
                "devices": ["aa:bb:cc:dd:ee:ff"],
                "cursor": 0,
            }
            _write(path, command)
            self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
            self.assertEqual(adapter.operations, [])
            command = _load(path)
            interface = _Interface(value=0)
            with patch("venus_evcharger.dbus_adapter.write.generic_shelly.dbus.Interface", return_value=interface):
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "applied")
            self.assertEqual(adapter.operations, ["read"])

    def test_mac_fallback_reads_device_metadata_and_rejects_invalid_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = {
                **_request(kind="mac", value="AABBCCDDEEFF"),
                "phase": "identify",
                "devices": ["not-a-mac"],
                "cursor": 0,
            }
            _write(path, command)
            interface = _Interface(value="aa:bb:cc:dd:ee:ff")
            with patch("venus_evcharger.dbus_adapter.write.generic_shelly.dbus.Interface", return_value=interface):
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
                self.assertEqual(_load(path)["phase"], "enabled")

                command = {**command, "devices": ["still-not-a-mac"]}
                interface.value = "invalid"
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "deferred")
            self.assertEqual(_load(path)["cursor"], 1)
            self.assertEqual(adapter.operations, ["read", "read"])

    def test_no_match_malformed_discovery_and_invalid_enabled_are_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = {**_request(), "phase": "identify", "devices": ["only"], "cursor": 1}
            self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "applied")
            self.assertEqual(adapter.operations, [])

            interface = _Interface(xml="not-xml")
            with patch("venus_evcharger.dbus_adapter.write.generic_shelly.dbus.Interface", return_value=interface):
                self.assertEqual(scheduler.process_semantic_operation(_request(), command_file=str(path)), "applied")
            self.assertEqual(adapter.operations, ["introspection"])

            adapter.operations.clear()
            command = {
                **_request(),
                "phase": "enabled",
                "devices": ["device"],
                "cursor": 0,
                "matched_device": "device",
            }
            interface.value = "unknown"
            with patch("venus_evcharger.dbus_adapter.write.generic_shelly.dbus.Interface", return_value=interface):
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=str(path)), "dropped")
            self.assertEqual(adapter.operations, ["read"])

    def test_invalid_commands_and_disabled_adapter_target_are_dropped_without_io(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir), service="")
            scheduler = _scheduler(adapter)
            path = str(Path(temp_dir) / "command.json")
            self.assertEqual(scheduler.process_semantic_operation(_request(), command_file=path), "dropped")
            self.assertEqual(scheduler.process_semantic_operation(_request(), command_file=""), "dropped")
            self.assertEqual(
                scheduler.process_semantic_operation({**_request(), "phase": "invalid"}, command_file=path),
                "dropped",
            )
            invalid_progress = (
                {**_request(), "devices": "device"},
                {**_request(), "cursor": -1},
                {**_request(), "cursor": True},
                {**_request(), "matched_device": 7},
                {**_request(), "devices": [""]},
            )
            for command in invalid_progress:
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=path), "dropped")
            self.assertEqual(adapter.operations, [])

    def test_enabled_and_disable_phases_require_a_matched_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler = _scheduler(_Adapter(Path(temp_dir)))
            path = str(Path(temp_dir) / "command.json")
            for phase in ("enabled", "disable"):
                command = {**_request(), "phase": phase}
                self.assertEqual(scheduler.process_semantic_operation(command, command_file=path), "dropped")

    def test_wire_command_is_configuration_and_contains_no_dbus_target(self) -> None:
        command = _request()
        self.assertEqual(command_queue_class(command), "configuration")
        self.assertTrue({"service", "path", "value"}.isdisjoint(command))


if __name__ == "__main__":
    unittest.main()
