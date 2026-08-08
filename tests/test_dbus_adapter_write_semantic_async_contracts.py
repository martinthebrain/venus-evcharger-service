# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact asynchronous contracts for semantic gateway operations."""

from __future__ import annotations

import configparser
import unittest
from typing import cast
from unittest.mock import MagicMock, call, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall
from venus_evcharger.dbus_adapter.contracts import CommandExecution, CommandOutcome
from venus_evcharger.dbus_adapter.write import semantic as semantic_module
from venus_evcharger.dbus_adapter.write.busitem_calls import busitem_read_call, busitem_write_call
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.semantic import SemanticWriteExecutor
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import (
    GxRelaySetOperation,
    ess_grid_setpoint_command,
    gx_relay_refresh_command,
    gx_relay_set_command,
    gx_relay_state_key,
    parse_gx_relay_set,
)

_SYSTEM_SERVICE = "com.victronenergy.system"
_SETTINGS_SERVICE = "com.victronenergy.settings"
_BUS_ITEM_INTERFACE = "com.victronenergy.BusItem"


class _Adapter:
    def __init__(self, config_text: str = "[DEFAULT]\n") -> None:
        self.config = configparser.ConfigParser()
        self.config.read_string(config_text)
        self.connection = MagicMock(name="connection")
        self.cache = MagicMock(name="cache")
        self.commands = MagicMock(name="commands")
        self.commands.replace_if_current.return_value = True
        self.operation_broker = MagicMock(name="operation_broker")


def _executor(config_text: str = "[DEFAULT]\n") -> tuple[SemanticWriteExecutor, _Adapter]:
    adapter = _Adapter(config_text)
    return SemanticWriteExecutor(cast(SemanticWriteAdapter, adapter)), adapter


def _relay_command(
    *,
    relay_index: int = 0,
    phase: str = "manual_read",
    manual_target: int = 0,
    retries: int = 0,
    enabled: bool = True,
    settle: float = 2.5,
    retry: float = 3.5,
    priority: str = "safety",
) -> CommandMapping:
    command = gx_relay_set_command(
        relay_index,
        "NO",
        enabled,
        ensure_manual=phase in ("manual_read", "manual_write"),
        verify_settle_seconds=settle,
        verify_retry_seconds=retry,
    )
    return {
        **command,
        "phase": phase,
        "manual_target": manual_target,
        "retries": retries,
        "priority": priority,
    }


def _operation(command: CommandMapping) -> GxRelaySetOperation:
    operation = parse_gx_relay_set(command)
    assert operation is not None
    return operation


def _read_call(
    service: str,
    path: str,
    priority: str,
    owner_path: str = "command.json",
) -> DbusMethodCall:
    return DbusMethodCall(
        service=service,
        path=path,
        interface=_BUS_ITEM_INTERFACE,
        method_name="GetValue",
        signature="",
        rate_kind="read",
        metric_kind="read",
        source=f"{service}{path}",
        priority=priority,
        timeout_seconds=1.0,
        owner_path=owner_path,
    )


def _write_call(
    service: str,
    path: str,
    value: object,
    priority: str,
    owner_path: str = "command.json",
) -> DbusMethodCall:
    return DbusMethodCall(
        service=service,
        path=path,
        interface=_BUS_ITEM_INTERFACE,
        method_name="SetValue",
        signature="v",
        rate_kind="write",
        metric_kind="write",
        source=f"{service}{path}",
        priority=priority,
        timeout_seconds=1.0,
        args=(value,),
        owner_path=owner_path,
    )


class ProxyCallContracts(unittest.TestCase):
    def test_busitem_factories_preserve_every_field(self) -> None:
        self.assertEqual(
            busitem_read_call(
                "com.example.read",
                "/Read/Path",
                "diagnostic",
                "command.json",
            ),
            _read_call("com.example.read", "/Read/Path", "diagnostic"),
        )
        value = object()
        self.assertEqual(
            busitem_write_call(
                "com.example.write",
                "/Write/Path",
                value,
                "urgent",
                "command.json",
            ),
            _write_call("com.example.write", "/Write/Path", value, "urgent"),
        )


class RelayReadContracts(unittest.TestCase):
    def test_refresh_submits_exact_read_and_forwards_both_callbacks(self) -> None:
        executor, _adapter = _executor()
        completion = MagicMock()
        command = {**gx_relay_refresh_command(1), "priority": "diagnostic"}
        value = object()
        with (
            patch.object(executor, "_schedule_read", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_refresh_relay_outcome", return_value="applied") as outcome,
        ):
            self.assertEqual(
                executor._refresh_gx_relay(command, "command.json", completion),
                CommandExecution.pending(),
            )
            operation = schedule.call_args
            operation.kwargs["on_success"](value)
            error = OSError("offline")
            operation.kwargs["on_error"](error)
        schedule.assert_called_once()
        self.assertEqual(
            schedule.call_args.args,
            (_read_call(_SYSTEM_SERVICE, "/Relay/1/State", "diagnostic"),),
        )
        outcome.assert_called_once_with(1, "/Relay/1/State", value)
        self.assertEqual(completion.call_args_list, [call("applied"), call("deferred")])

    def test_refresh_outcome_records_exact_state_or_exact_error(self) -> None:
        executor, _adapter = _executor()
        with (
            patch.object(semantic_module, "binary_relay_state", return_value=1) as binary,
            patch.object(executor, "_cache_relay_state") as cache,
            patch.object(executor, "_mark_relay_error") as mark_error,
        ):
            value = object()
            self.assertEqual(executor._refresh_relay_outcome(3, "/Relay/3/State", value), "applied")
        binary.assert_called_once_with(value)
        cache.assert_called_once_with(3, "/Relay/3/State", 1)
        mark_error.assert_not_called()

        with (
            patch.object(semantic_module, "binary_relay_state", return_value=None),
            patch.object(executor, "_cache_relay_state") as cache,
            patch.object(executor, "_mark_relay_error") as mark_error,
        ):
            self.assertEqual(executor._refresh_relay_outcome(4, "/Relay/4/State", object()), "dropped")
        cache.assert_not_called()
        mark_error.assert_called_once_with(4, "/Relay/4/State", "relay state is not binary")

    def test_verify_read_forwards_exact_context_and_callbacks(self) -> None:
        executor, _adapter = _executor()
        completion = MagicMock()
        command = _relay_command(relay_index=1, phase="verify", priority="urgent")
        operation = _operation(command)
        value = object()
        error = OSError("offline")
        with (
            patch.object(executor, "_schedule_read", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_verify_relay_outcome", return_value="dropped") as outcome,
            patch.object(executor, "_verify_relay_error", return_value="applied") as read_error,
        ):
            result = executor._verify_relay_output(command, "relay.json", operation, completion)
            schedule.call_args.kwargs["on_success"](value)
            schedule.call_args.kwargs["on_error"](error)
        self.assertEqual(result, CommandExecution.pending())
        self.assertEqual(
            schedule.call_args.args,
            (_read_call(_SYSTEM_SERVICE, "/Relay/1/State", "urgent", "relay.json"),),
        )
        outcome.assert_called_once_with(command, "relay.json", operation, "/Relay/1/State", value)
        read_error.assert_called_once_with(1, "/Relay/1/State", error)
        self.assertEqual(completion.call_args_list, [call("dropped"), call("applied")])


class ManualFunctionContracts(unittest.TestCase):
    def test_manual_read_uses_exact_target_and_callback_context(self) -> None:
        executor, _adapter = _executor()
        completion = MagicMock()
        command = _relay_command(phase="manual_read", manual_target=1, priority="control")
        operation = _operation(command)
        value = object()
        error = OSError("offline")
        with (
            patch.object(executor, "_schedule_read", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_manual_read_outcome", return_value="applied") as outcome,
            patch.object(executor, "_manual_fallback", return_value="dropped") as fallback,
        ):
            result = executor._read_manual_function(command, "relay.json", operation, completion)
            schedule.call_args.kwargs["on_success"](value)
            schedule.call_args.kwargs["on_error"](error)
        self.assertEqual(result, CommandExecution.pending())
        self.assertEqual(
            schedule.call_args.args,
            (_read_call(_SETTINGS_SERVICE, "/Settings/Relay/Function", "control", "relay.json"),),
        )
        outcome.assert_called_once_with(command, "relay.json", value)
        fallback.assert_called_once_with(command, "relay.json", operation)
        self.assertEqual(completion.call_args_list, [call("applied"), call("dropped")])

    def test_manual_write_uses_exact_value_and_callback_context(self) -> None:
        executor, _adapter = _executor()
        completion = MagicMock()
        command = _relay_command(phase="manual_write", manual_target=1, priority="control")
        operation = _operation(command)
        error = OSError("offline")
        with (
            patch.object(executor, "_schedule_write", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_rewrite", return_value="applied") as rewrite,
            patch.object(executor, "_manual_fallback", return_value="dropped") as fallback,
        ):
            result = executor._write_manual_function(command, "relay.json", operation, completion)
            schedule.call_args.kwargs["on_success"]()
            schedule.call_args.kwargs["on_error"](error)
        self.assertEqual(result, CommandExecution.pending())
        self.assertEqual(
            schedule.call_args.args,
            (_write_call(_SETTINGS_SERVICE, "/Settings/Relay/Function", 2, "control", "relay.json"),),
        )
        rewrite.assert_called_once_with("relay.json", command, phase="output")
        fallback.assert_called_once_with(command, "relay.json", operation)
        self.assertEqual(completion.call_args_list, [call("applied"), call("dropped")])

    def test_manual_fallback_has_exact_boundary_backoff_and_diagnostics(self) -> None:
        executor, _adapter = _executor()
        alternate = _relay_command(manual_target=0)
        with (
            self.assertLogs(level="DEBUG") as logs,
            patch.object(executor, "_rewrite", return_value="deferred") as rewrite,
        ):
            self.assertEqual(executor._manual_fallback(alternate, "relay.json", _operation(alternate)), "deferred")
        self.assertEqual(
            logs.output,
            ["DEBUG:root:Trying alternate Venus relay-function target for GX relay 0"],
        )
        rewrite.assert_called_once_with(
            "relay.json",
            alternate,
            phase="manual_read",
            manual_target=1,
        )

        exhausted = _relay_command(relay_index=1, manual_target=0, retry=0.25)
        with (
            self.assertLogs(level="WARNING") as logs,
            patch("venus_evcharger.dbus_adapter.write.semantic.time.time", return_value=100.0),
            patch.object(executor, "_rewrite", return_value="deferred") as rewrite,
        ):
            self.assertEqual(executor._manual_fallback(exhausted, "relay.json", _operation(exhausted)), "deferred")
        self.assertEqual(
            logs.output,
            ["WARNING:root:Deferring GX relay 1 because no manual-function target is currently reachable"],
        )
        rewrite.assert_called_once_with("relay.json", exhausted, not_before=101.0)


class RelayWriteContracts(unittest.TestCase):
    def test_output_write_preserves_target_timing_and_error_policy(self) -> None:
        executor, _adapter = _executor()
        completion = MagicMock()
        command = _relay_command(relay_index=1, phase="output", enabled=False, settle=2.5, priority="urgent")
        operation = _operation(command)
        with (
            patch.object(executor, "_schedule_write", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_rewrite", return_value="applied") as rewrite,
            patch("venus_evcharger.dbus_adapter.write.semantic.time.time", return_value=40.0),
        ):
            result = executor._write_relay_output(command, "relay.json", operation, completion)
            schedule.call_args.kwargs["on_success"]()
            schedule.call_args.kwargs["on_error"](OSError("offline"))
        self.assertEqual(result, CommandExecution.pending())
        self.assertEqual(
            schedule.call_args.args,
            (_write_call(_SYSTEM_SERVICE, "/Relay/1/State", 0, "urgent", "relay.json"),),
        )
        rewrite.assert_called_once_with("relay.json", command, phase="verify", not_before=42.5)
        self.assertEqual(completion.call_args_list, [call("applied"), call("deferred")])

    def test_retry_write_increments_only_retry_and_preserves_timing(self) -> None:
        executor, _adapter = _executor()
        completion = MagicMock()
        command = _relay_command(phase="retry", retries=2, settle=1.5, priority="urgent")
        operation = _operation(command)
        with (
            patch.object(executor, "_schedule_write", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_rewrite", return_value="dropped") as rewrite,
            patch("venus_evcharger.dbus_adapter.write.semantic.time.time", return_value=50.0),
        ):
            result = executor._retry_relay_output(command, "retry.json", operation, completion)
            schedule.call_args.kwargs["on_success"]()
            schedule.call_args.kwargs["on_error"](OSError("offline"))
        self.assertEqual(result, CommandExecution.pending())
        self.assertEqual(
            schedule.call_args.args,
            (_write_call(_SYSTEM_SERVICE, "/Relay/0/State", 1, "urgent", "retry.json"),),
        )
        rewrite.assert_called_once_with(
            "retry.json",
            command,
            phase="verify",
            retries=3,
            not_before=51.5,
        )
        self.assertEqual(completion.call_args_list, [call("dropped"), call("deferred")])

    def test_verify_outcome_and_error_preserve_exact_relay_context(self) -> None:
        executor, _adapter = _executor()
        command = _relay_command(relay_index=1, phase="verify", enabled=False)
        operation = _operation(command)
        value = object()
        with (
            patch.object(semantic_module, "binary_relay_state", return_value=0) as binary,
            patch.object(semantic_module, "relay_state_matches", return_value=True) as matches,
            patch.object(executor, "_cache_relay_state") as cache,
            patch.object(executor, "_relay_mismatch_outcome") as mismatch,
        ):
            self.assertEqual(
                executor._verify_relay_outcome(command, "relay.json", operation, "/Relay/1/State", value),
                "applied",
            )
        binary.assert_called_once_with(value)
        matches.assert_called_once_with(0, 0)
        cache.assert_called_once_with(1, "/Relay/1/State", 0)
        mismatch.assert_not_called()

        with (
            patch.object(semantic_module, "binary_relay_state", return_value=1),
            patch.object(semantic_module, "relay_state_matches", return_value=False),
            patch.object(executor, "_cache_relay_state") as cache,
            patch.object(executor, "_relay_mismatch_outcome", return_value="deferred") as mismatch,
        ):
            self.assertEqual(
                executor._verify_relay_outcome(command, "relay.json", operation, "/Relay/1/State", value),
                "deferred",
            )
        cache.assert_not_called()
        mismatch.assert_called_once_with(command, "relay.json", operation, "/Relay/1/State", 1)

        error = OSError("offline")
        with patch.object(executor, "_mark_relay_error") as mark_error:
            self.assertEqual(executor._verify_relay_error(2, "/Relay/2/State", error), "applied")
        mark_error.assert_called_once_with(2, "/Relay/2/State", error)

    def test_mismatch_retries_once_then_records_exact_terminal_error(self) -> None:
        executor, _adapter = _executor()
        first = _relay_command(phase="verify", retries=0, retry=3.5)
        with (
            patch("venus_evcharger.dbus_adapter.write.semantic.time.time", return_value=10.0),
            patch.object(executor, "_rewrite", return_value="deferred") as rewrite,
            patch.object(executor, "_mark_relay_error") as mark_error,
        ):
            self.assertEqual(
                executor._relay_mismatch_outcome(first, "relay.json", _operation(first), "/Relay/0/State", 0),
                "deferred",
            )
        rewrite.assert_called_once_with("relay.json", first, phase="retry", not_before=13.5)
        mark_error.assert_not_called()

        terminal = _relay_command(relay_index=1, phase="verify", retries=1, enabled=False)
        with (
            patch.object(executor, "_rewrite") as rewrite,
            patch.object(executor, "_mark_relay_error") as mark_error,
        ):
            self.assertEqual(
                executor._relay_mismatch_outcome(
                    terminal,
                    "relay.json",
                    _operation(terminal),
                    "/Relay/1/State",
                    1,
                ),
                "dropped",
            )
        rewrite.assert_not_called()
        mark_error.assert_called_once_with(1, "/Relay/1/State", "relay stayed at 1, expected 0")


class EssAndDiagnosticsContracts(unittest.TestCase):
    def test_ess_write_uses_configured_target_and_forwards_callbacks(self) -> None:
        executor, _adapter = _executor(
            "[DEFAULT]\n"
            "AutoBatteryDischargeBalanceVictronBiasService = com.example.settings\n"
            "AutoBatteryDischargeBalanceVictronBiasPath = /Custom/Setpoint\n"
        )
        completion = MagicMock()
        command = ess_grid_setpoint_command(-17.5, intent="restore")
        with (
            patch.object(executor, "_schedule_write", return_value=CommandExecution.pending()) as schedule,
            patch.object(executor, "_complete_ess_grid_setpoint", return_value="applied") as complete,
        ):
            result = executor._set_ess_grid_setpoint(
                command,
                "command.json",
                completion,
            )
            schedule.call_args.kwargs["on_success"]()
            schedule.call_args.kwargs["on_error"](OSError("offline"))
        self.assertEqual(result, CommandExecution.pending())
        self.assertEqual(
            schedule.call_args.args,
            (_write_call("com.example.settings", "/Custom/Setpoint", -17.5, "safety"),),
        )
        complete.assert_called_once_with("com.example.settings", "/Custom/Setpoint", -17.5)
        self.assertEqual(completion.call_args_list, [call("applied"), call("deferred")])

    def test_disabled_ess_targets_have_exact_diagnostic_and_no_write(self) -> None:
        for key in (
            "AutoBatteryDischargeBalanceVictronBiasService",
            "AutoBatteryDischargeBalanceVictronBiasPath",
        ):
            executor, adapter = _executor(f"[DEFAULT]\n{key}=\n")
            with self.subTest(key=key), self.assertLogs(level="WARNING") as logs:
                result = executor._set_ess_grid_setpoint(
                    ess_grid_setpoint_command(1.0, intent="tracking"),
                    "command.json",
                    MagicMock(),
                )
            self.assertEqual(result, CommandExecution.immediate("dropped"))
            self.assertEqual(
                logs.output,
                ["WARNING:root:Dropping ESS setpoint operation because its adapter target is disabled"],
            )
            adapter.operation_broker.submit.assert_not_called()

    def test_mark_relay_error_preserves_key_source_error_and_freshness(self) -> None:
        executor, adapter = _executor()
        error = OSError("offline")
        executor._mark_relay_error(1, "/Relay/1/State", error)
        adapter.cache.mark_error.assert_called_once_with(
            gx_relay_state_key(1),
            source=f"{_SYSTEM_SERVICE}/Relay/1/State",
            error=error,
            freshness_kind="external_read",
        )


if __name__ == "__main__":
    unittest.main()
