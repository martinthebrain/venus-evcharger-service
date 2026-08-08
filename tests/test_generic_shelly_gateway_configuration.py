"""Gateway-owned execution tests for generic Shelly configuration."""

from __future__ import annotations

import configparser
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import cast

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs
from tests.support.async_dbus import ImmediateAsyncBroker, run_semantic_operation

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.introspection_xml import DBUS_INTROSPECTION_XML_MAX_BYTES
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.semantic import SemanticWriteExecutor
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    DbusGatewayCommandInbox,
    gateway_paths,
    read_json_file,
)
from venus_evcharger.dbus_gateway_policy import command_queue_class
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.generic_shelly_configuration import disable_matching_generic_shelly_once_command
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyDeviceSelector,
    GenericShellySelectorKind,
)

_SERVICE = "com.example.generic-shelly"
_DbusCall = tuple[str, str, str, str, str, tuple[object, ...], float]


class _Connection:
    def __init__(self, *, value: object = None, xml: object = "<node/>") -> None:
        self.value = value
        self.xml = xml
        self.calls: list[_DbusCall] = []

    def send_async(
        self,
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object:
        del error_handler
        self.calls.append(
            (
                request.service,
                request.path,
                request.interface,
                request.method_name,
                request.signature,
                request.args,
                request.timeout_seconds,
            )
        )
        if request.method_name == "Introspect":
            reply_handler(self.xml)
        elif request.method_name == "GetValue":
            reply_handler(self.value)
        elif request.method_name == "SetValue":
            reply_handler()
        else:
            raise AssertionError(f"Unexpected DBus method: {request.method_name}")
        return object()


class _Adapter:
    def __init__(self, root: Path, *, service: str = _SERVICE) -> None:
        self.config = configparser.ConfigParser()
        self.config.read_dict({"DEFAULT": {"GenericShellyService": service}})
        self.connection = _Connection()
        paths = gateway_paths(str(root / "run"))
        self.cache = DbusCacheStore(paths)
        self.commands = DbusGatewayCommandInbox(paths.command_dir)
        self.operations: list[str] = []
        self.operation_broker = ImmediateAsyncBroker(self.operations)


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


def _dbus_call(
    *,
    path: str,
    interface: str,
    method: str,
    signature: str,
    args: tuple[object, ...] = (),
) -> _DbusCall:
    return (_SERVICE, path, interface, method, signature, args, 1.0)


class GenericShellyGatewayConfigurationTests(unittest.TestCase):
    def test_discovery_and_ip_matching_advance_one_dbus_operation_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = _request()
            _write(path, command)
            adapter.connection.xml = '<node><node name="first"/><node/><node name="second"/></node>'
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "deferred",
            )
            self.assertEqual(adapter.operations, ["introspection"])
            command = _load(path)
            self.assertEqual(
                (command["phase"], command["devices"], command["cursor"]),
                ("identify", ["first", "second"], 0),
            )

            adapter.operations.clear()
            adapter.connection.value = "192.0.2.8"
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "deferred",
            )
            self.assertEqual(adapter.operations, ["read"])
            command = _load(path)
            self.assertEqual(command["cursor"], 1)

            adapter.operations.clear()
            adapter.connection.value = "192.0.2.7"
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "deferred",
            )
            self.assertEqual(adapter.operations, ["read"])
            command = _load(path)
            self.assertEqual(
                (command["phase"], command["matched_device"]),
                ("enabled", "second"),
            )
            self.assertEqual(
                adapter.connection.calls,
                [
                    _dbus_call(
                        path="/Devices",
                        interface="org.freedesktop.DBus.Introspectable",
                        method="Introspect",
                        signature="",
                    ),
                    _dbus_call(
                        path="/Devices/first/Ip",
                        interface="com.victronenergy.BusItem",
                        method="GetValue",
                        signature="",
                    ),
                    _dbus_call(
                        path="/Devices/second/Ip",
                        interface="com.victronenergy.BusItem",
                        method="GetValue",
                        signature="",
                    ),
                ],
            )

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
            adapter.connection.value = 1
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "deferred",
            )
            self.assertEqual(adapter.operations, ["read"])
            command = _load(path)
            self.assertEqual(command["phase"], "disable")

            adapter.operations.clear()
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "applied",
            )
            self.assertEqual(adapter.operations, ["write"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _dbus_call(
                        path="/Devices/device/2/Enabled",
                        interface="com.victronenergy.BusItem",
                        method="GetValue",
                        signature="",
                    ),
                    _dbus_call(
                        path="/Devices/device/2/Enabled",
                        interface="com.victronenergy.BusItem",
                        method="SetValue",
                        signature="v",
                        args=(0,),
                    ),
                ],
            )

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
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(path)), "deferred")
            self.assertEqual(adapter.operations, [])
            self.assertEqual(adapter.connection.calls, [])
            command = _load(path)
            adapter.connection.value = 0
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "applied",
            )
            self.assertEqual(adapter.operations, ["read"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _dbus_call(
                        path="/Devices/aa:bb:cc:dd:ee:ff/2/Enabled",
                        interface="com.victronenergy.BusItem",
                        method="GetValue",
                        signature="",
                    )
                ],
            )

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
            adapter.connection.value = "aa:bb:cc:dd:ee:ff"
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "deferred",
            )
            self.assertEqual(_load(path)["phase"], "enabled")

            command = {**command, "devices": ["still-not-a-mac"]}
            _write(path, command)
            adapter.connection.value = "invalid"
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "deferred",
            )
            self.assertEqual(_load(path)["cursor"], 1)
            self.assertEqual(adapter.operations, ["read", "read"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _dbus_call(
                        path="/Devices/not-a-mac/Mac",
                        interface="com.victronenergy.BusItem",
                        method="GetValue",
                        signature="",
                    ),
                    _dbus_call(
                        path="/Devices/still-not-a-mac/Mac",
                        interface="com.victronenergy.BusItem",
                        method="GetValue",
                        signature="",
                    ),
                ],
            )

    def test_no_match_malformed_discovery_and_invalid_enabled_are_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            command = {**_request(), "phase": "identify", "devices": ["only"], "cursor": 1}
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(path)), "applied")
            self.assertEqual(adapter.operations, [])

            adapter.connection.xml = "not-xml"
            self.assertEqual(
                run_semantic_operation(scheduler, _request(), command_file=str(path)),
                "applied",
            )
            self.assertEqual(adapter.operations, ["introspection"])
            self.assertEqual(
                adapter.connection.calls[-1],
                _dbus_call(
                    path="/Devices",
                    interface="org.freedesktop.DBus.Introspectable",
                    method="Introspect",
                    signature="",
                ),
            )

            adapter.operations.clear()
            command = {
                **_request(),
                "phase": "enabled",
                "devices": ["device"],
                "cursor": 0,
                "matched_device": "device",
            }
            adapter.connection.value = "unknown"
            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(path)),
                "dropped",
            )
            self.assertEqual(adapter.operations, ["read"])
            self.assertEqual(
                adapter.connection.calls[-1],
                _dbus_call(
                    path="/Devices/device/2/Enabled",
                    interface="com.victronenergy.BusItem",
                    method="GetValue",
                    signature="",
                ),
            )

    def test_oversized_discovery_is_rejected_without_creating_device_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            path = Path(temp_dir) / "command.json"
            adapter.connection.xml = (
                "<node>" + " " * DBUS_INTROSPECTION_XML_MAX_BYTES + "</node>"
            )
            self.assertEqual(
                run_semantic_operation(scheduler, _request(), command_file=str(path)),
                "applied",
            )
            self.assertEqual(adapter.operations, ["introspection"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _dbus_call(
                        path="/Devices",
                        interface="org.freedesktop.DBus.Introspectable",
                        method="Introspect",
                        signature="",
                    )
                ],
            )
            self.assertFalse(path.exists())

    def test_invalid_commands_and_disabled_adapter_target_are_dropped_without_io(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir), service="")
            scheduler = _scheduler(adapter)
            path = str(Path(temp_dir) / "command.json")
            self.assertEqual(run_semantic_operation(scheduler, _request(), command_file=path), "dropped")
            self.assertEqual(run_semantic_operation(scheduler, _request(), command_file=""), "dropped")
            self.assertEqual(
                run_semantic_operation(scheduler, {**_request(), "phase": "invalid"}, command_file=path),
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
                self.assertEqual(run_semantic_operation(scheduler, command, command_file=path), "dropped")
            self.assertEqual(adapter.operations, [])

    def test_enabled_and_disable_phases_require_a_matched_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler = _scheduler(_Adapter(Path(temp_dir)))
            path = str(Path(temp_dir) / "command.json")
            for phase in ("enabled", "disable"):
                command = {**_request(), "phase": phase}
                self.assertEqual(run_semantic_operation(scheduler, command, command_file=path), "dropped")

    def test_wire_command_is_configuration_and_contains_no_dbus_target(self) -> None:
        command = _request()
        self.assertEqual(command_queue_class(command), "configuration")
        self.assertTrue({"service", "path", "value"}.isdisjoint(command))


if __name__ == "__main__":
    unittest.main()
