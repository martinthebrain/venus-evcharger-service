# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact async DBus contracts for generic Shelly configuration writes."""

from __future__ import annotations

import configparser
import unittest
from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, patch

from venus_evcharger.dbus_adapter.async_broker import (
    DbusAsyncOperation,
    DbusMethodCall,
)
from venus_evcharger.dbus_adapter.contracts import (
    CommandExecution,
    CommandOutcome,
)
from venus_evcharger.dbus_adapter.write import generic_shelly as subject
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.generic_shelly_configuration import (
    disable_matching_generic_shelly_once_command,
)
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyDeviceSelector,
    GenericShellySelectorKind,
)

_SERVICE = "com.example.generic-shelly"
_COMMAND_FILE = "/run/gateway-commands/shelly.json"


class _Broker:
    def __init__(self) -> None:
        self.operations: list[DbusAsyncOperation] = []

    def submit(self, operation: DbusAsyncOperation) -> int:
        self.operations.append(operation)
        return 73


class _Adapter:
    def __init__(self, service: str | None = _SERVICE) -> None:
        self.config = configparser.ConfigParser()
        defaults = {} if service is None else {"GenericShellyService": service}
        self.config.read_dict({"DEFAULT": defaults})
        self.connection = object()
        self.operation_broker = _Broker()
        self.commands = MagicMock()
        self.commands.replace_if_current.return_value = True
        self.cache = MagicMock()


class _ProxyFactory:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                object,
                DbusMethodCall,
                Callable[[object], None],
                Callable[[BaseException], None],
            ]
        ] = []

    def __call__(
        self,
        connection: object,
        call: DbusMethodCall,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None],
    ) -> DbusAsyncOperation:
        self.calls.append((connection, call, on_success, on_error))
        return DbusAsyncOperation(
            rate_kind=call.rate_kind,
            metric_kind=call.metric_kind,
            source=call.source,
            priority=call.priority,
            timeout_seconds=call.timeout_seconds,
            starter=lambda _reply, _error: None,
            on_success=on_success,
            on_error=on_error,
            on_callback_failure=on_error,
            optional_failure=call.optional_failure,
            owner_path=call.owner_path,
        )

    @property
    def last(self) -> tuple[object, DbusMethodCall, Callable[[object], None], Callable[[BaseException], None]]:
        return self.calls[-1]


def _command(
    *,
    kind: GenericShellySelectorKind = "ip",
    value: str = "192.0.2.7",
    channel: int = 2,
    **progress: object,
) -> CommandMapping:
    request = DisableMatchingGenericShellyOnceRequest(
        GenericShellyDeviceSelector(kind, value),
        channel,
    )
    return {**disable_matching_generic_shelly_once_command(request), **progress}


def _expected_call(
    *,
    path: str,
    method: str,
    kind: str,
    interface: str = "com.victronenergy.BusItem",
    args: tuple[object, ...] = (),
) -> DbusMethodCall:
    return DbusMethodCall(
        service=_SERVICE,
        path=path,
        interface=interface,
        method_name=method,
        signature="v" if method == "SetValue" else "",
        rate_kind=kind,
        metric_kind=kind,
        source=f"{_SERVICE}{path}",
        priority="user",
        timeout_seconds=1.0,
        args=args,
        owner_path=_COMMAND_FILE,
    )


class GenericShellyAsyncContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = _Adapter()
        self.executor = subject.GenericShellyConfigurationExecutor(
            cast(SemanticWriteAdapter, self.adapter)
        )
        self.proxy_factory = _ProxyFactory()
        proxy_patch = patch.object(
            subject,
            "dbus_call_operation",
            side_effect=self.proxy_factory,
        )
        proxy_patch.start()
        self.addCleanup(proxy_patch.stop)

    def _schedule(
        self,
        command: CommandMapping,
        command_file: str = _COMMAND_FILE,
    ) -> tuple[CommandExecution, list[CommandOutcome]]:
        outcomes: list[CommandOutcome] = []
        execution = self.executor.schedule(command, command_file, outcomes.append)
        return execution, outcomes

    def _assert_pending_call(self, expected: DbusMethodCall) -> tuple[Callable[[object], None], Callable[[BaseException], None]]:
        connection, call, on_success, on_error = self.proxy_factory.last
        self.assertIs(connection, self.adapter.connection)
        self.assertEqual(call, expected)
        self.assertEqual(len(self.adapter.operation_broker.operations), 1)
        operation = self.adapter.operation_broker.operations[0]
        self.assertIs(operation.on_success, on_success)
        self.assertIs(operation.on_error, on_error)
        self.assertEqual(
            (
                operation.rate_kind,
                operation.metric_kind,
                operation.source,
                operation.priority,
                operation.timeout_seconds,
                operation.optional_failure,
            ),
            (expected.rate_kind, expected.metric_kind, expected.source, "user", 1.0, False),
        )
        return on_success, on_error

    def test_rewrite_drops_a_stale_callback_generation_without_changing_payload_identity(self) -> None:
        command = _command(phase="identify", devices=["device"], cursor=0)
        self.adapter.commands.replace_if_current.return_value = False

        self.assertEqual(
            self.executor._rewrite(_COMMAND_FILE, command, phase="enabled"),
            "dropped",
        )
        self.adapter.commands.replace_if_current.assert_called_once_with(
            _COMMAND_FILE,
            command,
            {**command, "phase": "enabled"},
        )

    def test_schedule_rejects_each_invalid_boundary_independently(self) -> None:
        malformed_operation: CommandMapping = {"phase": "discover"}
        malformed_progress = _command(phase="unknown")
        valid = _command()
        for command, command_file in (
            (malformed_operation, _COMMAND_FILE),
            (malformed_progress, _COMMAND_FILE),
            (valid, ""),
        ):
            with self.subTest(command=command, command_file=command_file):
                execution, outcomes = self._schedule(command, command_file)
                self.assertEqual(execution, CommandExecution.immediate("dropped"))
                self.assertEqual(outcomes, [])
        self.assertEqual(self.proxy_factory.calls, [])

    def test_service_contract_defaults_trims_and_disables_explicitly(self) -> None:
        default_executor = subject.GenericShellyConfigurationExecutor(
            cast(SemanticWriteAdapter, _Adapter(None))
        )
        self.assertEqual(default_executor._service(), "com.victronenergy.shelly")
        self.adapter.config["DEFAULT"]["GenericShellyService"] = "  com.example.trimmed  "
        self.assertEqual(self.executor._service(), "com.example.trimmed")
        self.adapter.config["DEFAULT"]["GenericShellyService"] = "   "
        with patch.object(subject.logging, "warning") as warning:
            execution, outcomes = self._schedule(_command())
        self.assertEqual(execution, CommandExecution.immediate("dropped"))
        self.assertEqual(outcomes, [])
        warning.assert_called_once_with(
            "Dropping generic Shelly configuration because its adapter service is disabled"
        )

    def test_discovery_submits_exact_introspection_and_rewrites_on_success(self) -> None:
        command = _command()
        execution, outcomes = self._schedule(command)
        self.assertEqual(execution, CommandExecution.pending())
        self.assertEqual(outcomes, [])
        on_success, _on_error = self._assert_pending_call(
            _expected_call(
                path="/Devices",
                method="Introspect",
                kind="introspection",
                interface="org.freedesktop.DBus.Introspectable",
            )
        )
        on_success('<node><node name=" first "/><node/><node name="second"/></node>')
        self.assertEqual(outcomes, ["deferred"])
        self.adapter.commands.replace_if_current.assert_called_once_with(
            _COMMAND_FILE,
            command,
            {**dict(command), "phase": "identify", "devices": ["first", "second"], "cursor": 0},
        )

    def test_discovery_transport_error_defers_without_rewrite(self) -> None:
        execution, outcomes = self._schedule(_command())
        self.assertEqual(execution, CommandExecution.pending())
        _on_success, on_error = self._assert_pending_call(
            _expected_call(
                path="/Devices",
                method="Introspect",
                kind="introspection",
                interface="org.freedesktop.DBus.Introspectable",
            )
        )
        on_error(TimeoutError("offline"))
        self.assertEqual(outcomes, ["deferred"])
        self.adapter.commands.replace_if_current.assert_not_called()

    def test_discovery_empty_and_rejected_xml_are_terminal_and_diagnostic(self) -> None:
        with patch.object(subject.logging, "info") as info:
            self.assertEqual(self.executor._discover_outcome(self._context(_command()), "<node/>"), "applied")
        info.assert_called_once_with("No generic Shelly devices are currently registered")
        with (
            patch.object(subject.logging, "warning") as warning,
            patch.object(subject.logging, "info") as rejected_info,
        ):
            self.assertEqual(self.executor._discover_outcome(self._context(_command()), "not xml"), "applied")
        warning.assert_called_once_with("Generic Shelly discovery returned rejected introspection XML")
        rejected_info.assert_called_once_with("No generic Shelly devices are currently registered")

    def _context(self, command: CommandMapping) -> subject._ExecutionContext:
        operation = subject._operation(command)
        progress = subject._progress(command)
        self.assertIsNotNone(operation)
        self.assertIsNotNone(progress)
        return subject._ExecutionContext(
            command,
            _COMMAND_FILE,
            _SERVICE,
            cast(subject.DisableMatchingGenericShellyOnceOperation, operation),
            cast(subject._Progress, progress),
            MagicMock(),
        )

    def test_identify_terminal_and_serial_mac_shortcut_do_no_dbus_io(self) -> None:
        terminal = _command(phase="identify", devices=["only"], cursor=1)
        with patch.object(subject.logging, "info") as info:
            execution, outcomes = self._schedule(terminal)
        self.assertEqual(execution, CommandExecution.immediate("applied"))
        self.assertEqual(outcomes, [])
        info.assert_called_once_with("No generic Shelly device matched the configured identity")

        mac = _command(
            kind="mac",
            value="AABBCCDDEEFF",
            phase="identify",
            devices=["aa:bb:cc:dd:ee:ff"],
            cursor=0,
        )
        execution, outcomes = self._schedule(mac)
        self.assertEqual(execution, CommandExecution.immediate("deferred"))
        self.assertEqual(outcomes, [])
        self.adapter.commands.replace_if_current.assert_called_once_with(
            _COMMAND_FILE,
            mac,
            {**dict(mac), "phase": "enabled", "matched_device": "aa:bb:cc:dd:ee:ff"},
        )
        self.assertEqual(self.proxy_factory.calls, [])

    def test_identify_ip_submits_exact_read_and_matches_reply(self) -> None:
        command = _command(phase="identify", devices=["device-7"], cursor=0)
        execution, outcomes = self._schedule(command)
        self.assertEqual(execution, CommandExecution.pending())
        on_success, _on_error = self._assert_pending_call(
            _expected_call(path="/Devices/device-7/Ip", method="GetValue", kind="read")
        )
        on_success(" 192.0.2.7 ")
        self.assertEqual(outcomes, ["deferred"])
        self.adapter.commands.replace_if_current.assert_called_once_with(
            _COMMAND_FILE,
            command,
            {**dict(command), "phase": "enabled", "matched_device": "device-7"},
        )

    def test_identify_mac_fallback_mismatch_and_error_are_exact(self) -> None:
        command = _command(
            kind="mac",
            value="AABBCCDDEEFF",
            phase="identify",
            devices=["not-a-serial"],
            cursor=0,
        )
        execution, outcomes = self._schedule(command)
        self.assertEqual(execution, CommandExecution.pending())
        on_success, on_error = self._assert_pending_call(
            _expected_call(path="/Devices/not-a-serial/Mac", method="GetValue", kind="read")
        )
        on_success("11:22:33:44:55:66")
        self.assertEqual(outcomes, ["deferred"])
        self.adapter.commands.replace_if_current.assert_called_once_with(
            _COMMAND_FILE,
            command,
            {**dict(command), "cursor": 1},
        )
        outcomes.clear()
        on_error(OSError("offline"))
        self.assertEqual(outcomes, ["deferred"])

    def test_enabled_read_has_exact_call_and_callback_outcomes(self) -> None:
        command = _command(
            phase="enabled",
            devices=["device"],
            cursor=0,
            matched_device="device",
        )
        execution, outcomes = self._schedule(command)
        self.assertEqual(execution, CommandExecution.pending())
        on_success, on_error = self._assert_pending_call(
            _expected_call(path="/Devices/device/2/Enabled", method="GetValue", kind="read")
        )
        with patch.object(subject.logging, "info") as info:
            on_success(0)
        self.assertEqual(outcomes, ["applied"])
        info.assert_called_once_with("Matched generic Shelly channel is already disabled")
        outcomes.clear()
        on_error(OSError("offline"))
        self.assertEqual(outcomes, ["deferred"])

        context = self._context(command)
        self.assertEqual(self.executor._enabled_outcome(context, 1), "deferred")
        self.adapter.commands.replace_if_current.assert_called_once_with(
            _COMMAND_FILE,
            command,
            {**dict(command), "phase": "disable"},
        )
        with patch.object(subject.logging, "warning") as warning:
            self.assertEqual(self.executor._enabled_outcome(context, True), "dropped")
        warning.assert_called_once_with(
            "Dropping generic Shelly configuration because Enabled is not binary"
        )

    def test_enabled_and_disable_require_a_canonical_matched_device(self) -> None:
        for phase in ("enabled", "disable"):
            with self.subTest(phase=phase):
                execution, outcomes = self._schedule(_command(phase=phase))
                self.assertEqual(execution, CommandExecution.immediate("dropped"))
                self.assertEqual(outcomes, [])
        self.assertEqual(self.proxy_factory.calls, [])

    def test_disable_submits_exact_write_and_completes_only_from_callback(self) -> None:
        command = _command(
            channel=3,
            phase="disable",
            devices=["device"],
            cursor=0,
            matched_device="device",
        )
        execution, outcomes = self._schedule(command)
        self.assertEqual(execution, CommandExecution.pending())
        self.assertEqual(outcomes, [])
        on_success, on_error = self._assert_pending_call(
            _expected_call(
                path="/Devices/device/3/Enabled",
                method="SetValue",
                kind="write",
                args=(0,),
            )
        )
        with patch.object(subject.logging, "info") as info:
            on_success(None)
        self.assertEqual(outcomes, ["applied"])
        info.assert_called_once_with("Disabled matched generic Shelly channel through the gateway")
        outcomes.clear()
        on_error(OSError("offline"))
        self.assertEqual(outcomes, ["deferred"])

    def test_progress_and_identity_helpers_reject_ambiguous_wire_values(self) -> None:
        self.assertEqual(subject._progress(_command()), subject._Progress("discover", (), 0, ""))
        progress = subject._progress(
            _command(devices=[" first ", "second"], cursor=0, matched_device=" device ")
        )
        self.assertEqual(progress, subject._Progress("discover", ("first", "second"), 0, "device"))
        self.assertIsNone(subject._progress(_command(devices="device", cursor=0)))
        self.assertIsNone(subject._progress(_command(devices=["device"], cursor=-1)))
        for value in (-1, True, 1.0, "0", None):
            with self.subTest(cursor=value):
                self.assertFalse(subject._valid_cursor(value))
        self.assertTrue(subject._valid_cursor(0))
        for value in ("device", {"device"}, [""], ["ok", 7], ("  ",)):
            with self.subTest(devices=value):
                self.assertIsNone(subject._normalized_devices(value))
        self.assertEqual(subject._normalized_devices((" one ", "two")), ("one", "two"))

    def test_phase_and_identity_helpers_preserve_protocol_spelling(self) -> None:
        for phase in ("discover", "identify", "enabled", "disable"):
            self.assertTrue(subject._is_phase(phase))
        for phase in ("Discover", "disabled", "", 1, None):
            self.assertFalse(subject._is_phase(phase))
        self.assertEqual(subject._identity_field("ip"), "Ip")
        self.assertEqual(subject._identity_field("mac"), "Mac")
        self.assertTrue(subject._identity_matches("ip", " 192.0.2.7 ", "192.0.2.7"))
        self.assertFalse(subject._identity_matches("ip", None, "192.0.2.7"))
        self.assertTrue(subject._identity_matches("mac", "aa-bb-cc-dd-ee-ff", "AABBCCDDEEFF"))
        self.assertFalse(subject._identity_matches("mac", "invalid", "AABBCCDDEEFF"))
        self.assertTrue(subject._serial_matches_mac("aa:bb:cc:dd:ee:ff", "AABBCCDDEEFF"))
        self.assertFalse(subject._serial_matches_mac("device", "AABBCCDDEEFF"))


if __name__ == "__main__":
    unittest.main()
