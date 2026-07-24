# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-tight contracts for adapter-owned semantic DBus operations."""

from __future__ import annotations

import configparser
import unittest
from collections.abc import Callable
from typing import TypeVar, cast
from unittest.mock import MagicMock, call, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.dbus_adapter.write import semantic as semantic_module
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.semantic import SemanticWriteExecutor
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import (
    ESS_GRID_SETPOINT_KIND,
    GX_RELAY_REFRESH_KIND,
    GX_RELAY_SET_KIND,
    ess_grid_setpoint_command,
    gx_relay_refresh_command,
    gx_relay_set_command,
    gx_relay_state_key,
    parse_gx_relay_set,
)
from venus_evcharger.ipc.generic_shelly_configuration import DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND

_T = TypeVar("_T")
_SYSTEM_SERVICE = "com.victronenergy.system"
_SETTINGS_SERVICE = "com.victronenergy.settings"
_BUS_ITEM_INTERFACE = "com.victronenergy.BusItem"


class _Connection:
    def __init__(self) -> None:
        self.obj = object()
        self.calls: list[tuple[str, str, bool]] = []

    def get_object(self, service: str, path: str, *, introspect: bool) -> object:
        self.calls.append((service, path, introspect))
        return self.obj


class _Adapter:
    def __init__(self, config_text: str = "[DEFAULT]\n") -> None:
        self.config = configparser.ConfigParser()
        self.config.read_string(config_text)
        self.connection = _Connection()
        self.cache = MagicMock()
        self.json_writer = MagicMock()
        self.operations: list[str] = []

    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T:
        self.operations.append(kind)
        return operation()


def _scheduler(adapter: _Adapter | None = None) -> tuple[SemanticWriteExecutor, _Adapter]:
    selected = adapter or _Adapter()
    return SemanticWriteExecutor(cast(SemanticWriteAdapter, selected)), selected


def _relay_command(
    *,
    relay_index: int = 0,
    phase: str = "manual_read",
    manual_target: int = 0,
    retries: int = 0,
    enabled: bool = True,
    settle: float = 2.5,
    retry: float = 3.5,
) -> CommandMapping:
    command = gx_relay_set_command(
        relay_index,
        "NO",
        enabled,
        ensure_manual=phase in ("manual_read", "manual_write"),
        verify_settle_seconds=settle,
        verify_retry_seconds=retry,
    )
    return {**command, "phase": phase, "manual_target": manual_target, "retries": retries}


def _operation(command: CommandMapping) -> semantic_module.GxRelaySetOperation:
    operation = parse_gx_relay_set(command)
    assert operation is not None
    return operation


class SemanticDispatchMutationContracts(unittest.TestCase):
    def test_dispatches_each_kind_to_exact_handler(self) -> None:
        scheduler, _adapter = _scheduler()
        refresh = gx_relay_refresh_command(1)
        relay = _relay_command(phase="output")
        ess = ess_grid_setpoint_command(42.5, intent="tracking")
        with (
            patch.object(scheduler, "_refresh_gx_relay", return_value="applied") as refresh_handler,
            patch.object(scheduler, "_set_gx_relay", return_value="deferred") as relay_handler,
            patch.object(scheduler, "_set_ess_grid_setpoint", return_value="dropped") as ess_handler,
        ):
            self.assertEqual(scheduler.process_semantic_operation(refresh, command_file="refresh.json"), "applied")
            self.assertEqual(scheduler.process_semantic_operation(relay, command_file="relay.json"), "deferred")
            self.assertEqual(scheduler.process_semantic_operation(ess, command_file="ess.json"), "dropped")
        refresh_handler.assert_called_once_with(refresh)
        relay_handler.assert_called_once_with(relay, "relay.json")
        ess_handler.assert_called_once_with(ess)

    def test_dispatches_generic_shelly_and_drops_unknown_kind(self) -> None:
        scheduler, adapter = _scheduler()
        command: CommandMapping = {"kind": DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND}
        executor = MagicMock()
        executor.process.return_value = "applied"
        with patch.object(semantic_module, "GenericShellyConfigurationExecutor", return_value=executor) as factory:
            self.assertEqual(scheduler.process_semantic_operation(command, command_file="shelly.json"), "applied")
        factory.assert_called_once_with(adapter)
        executor.process.assert_called_once_with(command, "shelly.json")
        self.assertEqual(scheduler.process_semantic_operation({"kind": "unknown"}, command_file="x"), "dropped")
        self.assertEqual(scheduler.process_semantic_operation({}, command_file="x"), "dropped")


class RelayRefreshAndPhaseMutationContracts(unittest.TestCase):
    def test_refresh_reads_exact_target_and_caches_binary_state(self) -> None:
        scheduler, _adapter = _scheduler()
        command = gx_relay_refresh_command(1)
        with (
            patch.object(scheduler, "_read_binary_value", return_value=0) as read_value,
            patch.object(scheduler, "_cache_relay_state") as cache_state,
            patch.object(scheduler, "_mark_relay_error") as mark_error,
        ):
            self.assertEqual(scheduler._refresh_gx_relay(command), "applied")
        read_value.assert_called_once_with(_SYSTEM_SERVICE, "/Relay/1/State")
        cache_state.assert_called_once_with(1, "/Relay/1/State", 0)
        mark_error.assert_not_called()

    def test_refresh_marks_non_binary_value_and_rejects_invalid_command(self) -> None:
        scheduler, _adapter = _scheduler()
        command = gx_relay_refresh_command(0)
        with (
            patch.object(scheduler, "_read_binary_value", return_value=None) as read_value,
            patch.object(scheduler, "_cache_relay_state") as cache_state,
            patch.object(scheduler, "_mark_relay_error") as mark_error,
        ):
            self.assertEqual(scheduler._refresh_gx_relay(command), "dropped")
        read_value.assert_called_once_with(_SYSTEM_SERVICE, "/Relay/0/State")
        mark_error.assert_called_once_with(0, "/Relay/0/State", "relay state is not binary")
        cache_state.assert_not_called()
        self.assertEqual(scheduler._refresh_gx_relay({"kind": GX_RELAY_REFRESH_KIND}), "dropped")

    def test_set_relay_requires_valid_operation_and_command_file(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command()
        self.assertEqual(scheduler._set_gx_relay({"kind": GX_RELAY_SET_KIND}, "relay.json"), "dropped")
        self.assertEqual(scheduler._set_gx_relay(command, ""), "dropped")

    def test_set_relay_dispatches_every_phase_with_exact_arguments(self) -> None:
        scheduler, _adapter = _scheduler()
        phases = {
            "manual_read": "_read_manual_function",
            "manual_write": "_write_manual_function",
            "output": "_write_relay_output",
            "verify": "_verify_relay_output",
            "retry": "_retry_relay_output",
        }
        for phase, method_name in phases.items():
            with self.subTest(phase=phase):
                command = _relay_command(phase=phase)
                operation = _operation(command)
                with patch.object(scheduler, method_name, return_value="applied") as handler:
                    self.assertEqual(scheduler._set_gx_relay(command, "relay.json"), "applied")
                handler.assert_called_once_with(command, "relay.json", operation)


class ManualFunctionMutationContracts(unittest.TestCase):
    def test_manual_read_selects_exact_target_and_next_phase(self) -> None:
        scheduler, _adapter = _scheduler()
        primary = _relay_command(phase="manual_read", manual_target=0)
        legacy = _relay_command(phase="manual_read", manual_target=1)
        scenarios = ((primary, 2, "/Settings/Relay/0/Function", "output"), (legacy, 0, "/Settings/Relay/Function", "manual_write"))
        for command, value, path, phase in scenarios:
            with self.subTest(path=path):
                with (
                    patch.object(scheduler, "_read_value", return_value=value) as read_value,
                    patch.object(scheduler, "_rewrite", return_value="deferred") as rewrite,
                ):
                    self.assertEqual(scheduler._read_manual_function(command, "relay.json", _operation(command)), "deferred")
                read_value.assert_called_once_with(_SETTINGS_SERVICE, path)
                rewrite.assert_called_once_with("relay.json", command, phase=phase)

    def test_manual_read_propagates_deferred_and_routes_dbus_errors_to_fallback(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(phase="manual_read")
        operation = _operation(command)
        with patch.object(scheduler, "_read_value", side_effect=DbusOperationDeferred("busy")):
            with self.assertRaisesRegex(DbusOperationDeferred, "busy"):
                scheduler._read_manual_function(command, "relay.json", operation)
        with (
            patch.object(scheduler, "_read_value", side_effect=OSError("offline")),
            patch.object(scheduler, "_manual_fallback", return_value="deferred") as fallback,
        ):
            self.assertEqual(scheduler._read_manual_function(command, "relay.json", operation), "deferred")
        fallback.assert_called_once_with(command, "relay.json", operation)

    def test_manual_write_uses_exact_target_and_value(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(phase="manual_write", manual_target=1)
        operation = _operation(command)
        with (
            patch.object(scheduler, "_write_value") as write_value,
            patch.object(scheduler, "_rewrite", return_value="deferred") as rewrite,
        ):
            self.assertEqual(scheduler._write_manual_function(command, "relay.json", operation), "deferred")
        write_value.assert_called_once_with(_SETTINGS_SERVICE, "/Settings/Relay/Function", 2)
        rewrite.assert_called_once_with("relay.json", command, phase="output")

    def test_manual_write_propagates_deferred_and_routes_dbus_errors_to_fallback(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(phase="manual_write")
        operation = _operation(command)
        with patch.object(scheduler, "_write_value", side_effect=DbusOperationDeferred("busy")):
            with self.assertRaisesRegex(DbusOperationDeferred, "busy"):
                scheduler._write_manual_function(command, "relay.json", operation)
        with (
            patch.object(scheduler, "_write_value", side_effect=ValueError("unavailable")),
            patch.object(scheduler, "_manual_fallback", return_value="deferred") as fallback,
        ):
            self.assertEqual(scheduler._write_manual_function(command, "relay.json", operation), "deferred")
        fallback.assert_called_once_with(command, "relay.json", operation)

    def test_manual_fallback_advances_to_legacy_target(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(phase="manual_read", manual_target=0)
        with (
            self.assertLogs(level="DEBUG") as logs,
            patch.object(scheduler, "_rewrite", return_value="deferred") as rewrite,
        ):
            self.assertEqual(scheduler._manual_fallback(command, "relay.json", _operation(command)), "deferred")
        rewrite.assert_called_once_with("relay.json", command, phase="manual_read", manual_target=1)
        self.assertEqual(
            logs.output,
            ["DEBUG:root:Trying alternate Venus relay-function target for GX relay 0"],
        )

    def test_manual_fallback_defers_after_last_target_with_minimum_backoff(self) -> None:
        scheduler, _adapter = _scheduler()
        scenarios = ((_relay_command(manual_target=1, retry=0.25), 101.0), (_relay_command(relay_index=1, retry=4.5), 104.5))
        for command, expected in scenarios:
            with self.subTest(command=command):
                with (
                    self.assertLogs(level="WARNING") as logs,
                    patch.object(semantic_module.time, "time", return_value=100.0),
                    patch.object(scheduler, "_rewrite", return_value="deferred") as rewrite,
                ):
                    self.assertEqual(scheduler._manual_fallback(command, "relay.json", _operation(command)), "deferred")
                rewrite.assert_called_once_with("relay.json", command, not_before=expected)
                self.assertEqual(
                    logs.output,
                    [f"WARNING:root:Deferring GX relay {command['relay_index']} because no manual-function target is currently reachable"],
                )


class RelayOutputMutationContracts(unittest.TestCase):
    def test_output_and_retry_write_exact_target_and_progress(self) -> None:
        scheduler, _adapter = _scheduler()
        output = _relay_command(relay_index=1, phase="output", settle=2.5)
        retry = _relay_command(relay_index=1, phase="retry", retries=2, settle=2.5)
        with (
            patch.object(semantic_module.time, "time", return_value=40.0),
            patch.object(scheduler, "_write_value") as write_value,
            patch.object(scheduler, "_rewrite", return_value="deferred") as rewrite,
        ):
            self.assertEqual(scheduler._write_relay_output(output, "output.json", _operation(output)), "deferred")
            self.assertEqual(scheduler._retry_relay_output(retry, "retry.json", _operation(retry)), "deferred")
        self.assertEqual(
            write_value.call_args_list,
            [call(_SYSTEM_SERVICE, "/Relay/1/State", 1), call(_SYSTEM_SERVICE, "/Relay/1/State", 1)],
        )
        self.assertEqual(
            rewrite.call_args_list,
            [
                call("output.json", output, phase="verify", not_before=42.5),
                call("retry.json", retry, phase="verify", retries=3, not_before=42.5),
            ],
        )

    def test_verify_success_caches_target_and_skips_mismatch(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(relay_index=1, phase="verify", enabled=False)
        with (
            patch.object(scheduler, "_read_binary_value", return_value=0) as read_value,
            patch.object(scheduler, "_cache_relay_state") as cache_state,
            patch.object(scheduler, "_relay_mismatch_outcome") as mismatch,
        ):
            self.assertEqual(scheduler._verify_relay_output(command, "relay.json", _operation(command)), "applied")
        read_value.assert_called_once_with(_SYSTEM_SERVICE, "/Relay/1/State")
        cache_state.assert_called_once_with(1, "/Relay/1/State", 0)
        mismatch.assert_not_called()

    def test_verify_mismatch_forwards_complete_observation(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(phase="verify", enabled=True)
        operation = _operation(command)
        with (
            patch.object(scheduler, "_read_binary_value", return_value=0),
            patch.object(scheduler, "_relay_mismatch_outcome", return_value="deferred") as mismatch,
        ):
            self.assertEqual(scheduler._verify_relay_output(command, "relay.json", operation), "deferred")
        mismatch.assert_called_once_with(command, "relay.json", operation, "/Relay/0/State", 0)

    def test_verify_propagates_deferred_and_marks_dbus_error_as_observed(self) -> None:
        scheduler, _adapter = _scheduler()
        command = _relay_command(phase="verify")
        operation = _operation(command)
        with patch.object(scheduler, "_read_binary_value", side_effect=DbusOperationDeferred("busy")):
            with self.assertRaisesRegex(DbusOperationDeferred, "busy"):
                scheduler._verify_relay_output(command, "relay.json", operation)
        error = OSError("offline")
        with (
            patch.object(scheduler, "_read_binary_value", side_effect=error),
            patch.object(scheduler, "_mark_relay_error") as mark_error,
        ):
            self.assertEqual(scheduler._verify_relay_output(command, "relay.json", operation), "applied")
        mark_error.assert_called_once_with(0, "/Relay/0/State", error)

    def test_first_mismatch_retries_once_and_later_mismatch_drops(self) -> None:
        scheduler, _adapter = _scheduler()
        first = _relay_command(phase="verify", retries=0, retry=3.5)
        with (
            patch.object(semantic_module.time, "time", return_value=50.0),
            patch.object(scheduler, "_rewrite", return_value="deferred") as rewrite,
        ):
            self.assertEqual(
                scheduler._relay_mismatch_outcome(first, "relay.json", _operation(first), "/Relay/0/State", None),
                "deferred",
            )
        rewrite.assert_called_once_with("relay.json", first, phase="retry", not_before=53.5)

        later = _relay_command(relay_index=1, phase="verify", retries=2, enabled=False)
        with patch.object(scheduler, "_mark_relay_error") as mark_error:
            self.assertEqual(
                scheduler._relay_mismatch_outcome(later, "relay.json", _operation(later), "/Relay/1/State", 1),
                "dropped",
            )
        mark_error.assert_called_once_with(1, "/Relay/1/State", "relay stayed at 1, expected 0")

    def test_state_match_contract_distinguishes_none_mismatch_and_match(self) -> None:
        self.assertFalse(SemanticWriteExecutor._relay_state_matches(None, 0))
        self.assertFalse(SemanticWriteExecutor._relay_state_matches(0, 1))
        self.assertTrue(SemanticWriteExecutor._relay_state_matches(0, 0))
        self.assertTrue(SemanticWriteExecutor._relay_state_matches(1, 1))


class EssAndDbusBoundaryMutationContracts(unittest.TestCase):
    def test_ess_setpoint_rejects_invalid_and_each_disabled_target_dimension(self) -> None:
        scheduler, adapter = _scheduler()
        self.assertEqual(scheduler._set_ess_grid_setpoint({"kind": ESS_GRID_SETPOINT_KIND}), "dropped")
        adapter.config["DEFAULT"]["AutoBatteryDischargeBalanceVictronBiasService"] = ""
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(
                scheduler._set_ess_grid_setpoint(ess_grid_setpoint_command(1.0, intent="tracking")),
                "dropped",
            )
            adapter.config["DEFAULT"]["AutoBatteryDischargeBalanceVictronBiasService"] = _SETTINGS_SERVICE
            adapter.config["DEFAULT"]["AutoBatteryDischargeBalanceVictronBiasPath"] = ""
            self.assertEqual(
                scheduler._set_ess_grid_setpoint(ess_grid_setpoint_command(1.0, intent="tracking")),
                "dropped",
            )
        expected_warning = "WARNING:root:Dropping ESS setpoint operation because its adapter target is disabled"
        self.assertEqual(logs.output, [expected_warning, expected_warning])
        self.assertEqual(adapter.operations, [])
        adapter.cache.update_value.assert_not_called()

    def test_ess_setpoint_writes_and_caches_complete_adapter_mapping(self) -> None:
        adapter = _Adapter(
            "[DEFAULT]\n"
            "AutoBatteryDischargeBalanceVictronBiasService = com.example.settings\n"
            "AutoBatteryDischargeBalanceVictronBiasPath = /Custom/Setpoint\n"
        )
        scheduler, _adapter = _scheduler(adapter)
        command = ess_grid_setpoint_command(-17.5, intent="restore")
        with patch.object(scheduler, "_write_value") as write_value:
            self.assertEqual(scheduler._set_ess_grid_setpoint(command), "applied")
        write_value.assert_called_once_with("com.example.settings", "/Custom/Setpoint", -17.5)
        adapter.cache.update_value.assert_called_once_with(
            "path:com.example.settings/Custom/Setpoint",
            -17.5,
            source="com.example.settings/Custom/Setpoint",
            confidence=0.9,
            freshness_kind="external_read",
        )

    def test_read_value_is_one_timed_read_and_preserves_result(self) -> None:
        scheduler, adapter = _scheduler()
        token = object()
        with patch.object(scheduler, "_read_value_now", return_value=token) as read_now:
            self.assertIs(scheduler._read_value("service", "/Path"), token)
        self.assertEqual(adapter.operations, ["read"])
        read_now.assert_called_once_with("service", "/Path")

    def test_binary_read_accepts_only_zero_and_one_after_numeric_coercion(self) -> None:
        scheduler, _adapter = _scheduler()
        scenarios = ((0, 0), (1, 1), (0.0, 0), (1.0, 1), (True, None), (2, None), (-1, None), ("invalid", None))
        for value, expected in scenarios:
            with self.subTest(value=value):
                with patch.object(scheduler, "_read_value", return_value=value) as read_value:
                    self.assertEqual(scheduler._read_binary_value("service", "/State"), expected)
                read_value.assert_called_once_with("service", "/State")

    def test_low_level_read_uses_non_introspecting_bus_item_and_timeout(self) -> None:
        scheduler, adapter = _scheduler()
        interface = MagicMock()
        token = object()
        interface.GetValue.return_value = token
        with patch.object(semantic_module.dbus, "Interface", return_value=interface) as interface_factory:
            self.assertIs(scheduler._read_value_now("service", "/Path"), token)
        self.assertEqual(adapter.connection.calls, [("service", "/Path", False)])
        interface_factory.assert_called_once_with(adapter.connection.obj, _BUS_ITEM_INTERFACE)
        interface.GetValue.assert_called_once_with(timeout=1.0)

    def test_write_value_is_one_timed_write_with_exact_payload(self) -> None:
        scheduler, adapter = _scheduler()
        token = object()
        with patch.object(scheduler, "_write_value_now") as write_now:
            self.assertIsNone(scheduler._write_value("service", "/Path", token))
        self.assertEqual(adapter.operations, ["write"])
        write_now.assert_called_once_with("service", "/Path", token)

    def test_low_level_write_uses_non_introspecting_bus_item_and_timeout(self) -> None:
        scheduler, adapter = _scheduler()
        interface = MagicMock()
        token = object()
        with patch.object(semantic_module.dbus, "Interface", return_value=interface) as interface_factory:
            self.assertIsNone(scheduler._write_value_now("service", "/Path", token))
        self.assertEqual(adapter.connection.calls, [("service", "/Path", False)])
        interface_factory.assert_called_once_with(adapter.connection.obj, _BUS_ITEM_INTERFACE)
        interface.SetValue.assert_called_once_with(token, timeout=1.0)


class CacheAndPureHelperMutationContracts(unittest.TestCase):
    def test_relay_cache_update_has_complete_semantic_metadata(self) -> None:
        scheduler, adapter = _scheduler()
        scheduler._cache_relay_state(1, "/Relay/1/State", 0)
        adapter.cache.update_external_read.assert_called_once_with(
            gx_relay_state_key(1),
            0,
            source="com.victronenergy.system/Relay/1/State",
            confidence=1.0,
        )

    def test_relay_error_has_complete_semantic_metadata(self) -> None:
        scheduler, adapter = _scheduler()
        error = ValueError("invalid")
        scheduler._mark_relay_error(0, "/Relay/0/State", error)
        adapter.cache.mark_error.assert_called_once_with(
            gx_relay_state_key(0),
            source="com.victronenergy.system/Relay/0/State",
            error=error,
            freshness_kind="external_read",
        )

    def test_ess_target_defaults_and_strips_configured_values(self) -> None:
        scheduler, _adapter = _scheduler()
        self.assertEqual(
            scheduler._ess_grid_setpoint_target(),
            (_SETTINGS_SERVICE, "/Settings/CGwacs/AcPowerSetPoint"),
        )
        configured = _Adapter(
            "[DEFAULT]\n"
            "AutoBatteryDischargeBalanceVictronBiasService =   com.example.settings   \n"
            "AutoBatteryDischargeBalanceVictronBiasPath =   /Custom/Setpoint   \n"
        )
        scheduler, _adapter = _scheduler(configured)
        self.assertEqual(scheduler._ess_grid_setpoint_target(), ("com.example.settings", "/Custom/Setpoint"))

    def test_rewrite_preserves_command_and_applies_only_requested_changes(self) -> None:
        scheduler, adapter = _scheduler()
        command: CommandMapping = {"kind": "original", "phase": "old", "retries": 1}
        self.assertEqual(scheduler._rewrite("command.json", command, phase="new", not_before=12.5), "deferred")
        adapter.json_writer.write.assert_called_once_with(
            "command.json",
            {"kind": "original", "phase": "new", "retries": 1, "not_before": 12.5},
        )

    def test_relay_and_manual_paths_are_exact_for_both_supported_indices(self) -> None:
        self.assertEqual(SemanticWriteExecutor._relay_state_path(0), "/Relay/0/State")
        self.assertEqual(SemanticWriteExecutor._relay_state_path(1), "/Relay/1/State")
        self.assertEqual(
            SemanticWriteExecutor._manual_function_paths(0),
            ("/Settings/Relay/0/Function", "/Settings/Relay/Function"),
        )
        self.assertEqual(SemanticWriteExecutor._manual_function_paths(1), ("/Settings/Relay/1/Function",))


if __name__ == "__main__":
    unittest.main()
