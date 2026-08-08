# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-tight callback contracts for semantic gateway writes."""

from __future__ import annotations

import configparser
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.async_broker import DbusAsyncOperation
from venus_evcharger.dbus_adapter.contracts import CommandExecution, CommandOutcome
from venus_evcharger.dbus_adapter.write import semantic as semantic_module
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.relay_topology import (
    binary_relay_state,
    manual_function_paths,
    relay_state_matches,
    relay_state_path,
)
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
from venus_evcharger.ipc.generic_shelly_configuration import (
    DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND,
)

_SYSTEM_SERVICE = "com.victronenergy.system"
_SETTINGS_SERVICE = "com.victronenergy.settings"


class _Broker:
    def __init__(self) -> None:
        self.operations: list[DbusAsyncOperation] = []

    def submit(self, operation: DbusAsyncOperation) -> int:
        self.operations.append(operation)
        return len(self.operations)

    def succeed(self, value: object = None) -> None:
        self.operations.pop(0).on_success(value)

    def fail(self, error: BaseException) -> None:
        self.operations.pop(0).on_error(error)


class _Adapter:
    def __init__(self, config_text: str = "[DEFAULT]\n") -> None:
        self.config = configparser.ConfigParser()
        self.config.read_string(config_text)
        self.connection = MagicMock()
        self.cache = MagicMock()
        self.commands = MagicMock()
        self.commands.replace_if_current.return_value = True
        self.operation_broker = _Broker()


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


def _schedule(
    scheduler: SemanticWriteExecutor,
    command: CommandMapping,
    command_file: str = "command.json",
) -> tuple[CommandExecution, list[CommandOutcome]]:
    outcomes: list[CommandOutcome] = []
    execution = scheduler.schedule_semantic_operation(
        command,
        command_file=command_file,
        completion=outcomes.append,
    )
    return execution, outcomes


class SemanticDispatchMutationContracts(unittest.TestCase):
    def test_dispatches_each_kind_with_the_same_completion(self) -> None:
        scheduler, _adapter = _scheduler()
        completion = MagicMock()
        refresh = gx_relay_refresh_command(1)
        relay = _relay_command(phase="output")
        ess = ess_grid_setpoint_command(42.5, intent="tracking")
        expected = (
            CommandExecution.immediate("applied"),
            CommandExecution.pending(),
            CommandExecution.immediate("dropped"),
        )
        with (
            patch.object(scheduler, "_refresh_gx_relay", return_value=expected[0]) as refresh_handler,
            patch.object(scheduler, "_set_gx_relay", return_value=expected[1]) as relay_handler,
            patch.object(scheduler, "_set_ess_grid_setpoint", return_value=expected[2]) as ess_handler,
        ):
            self.assertEqual(
                scheduler.schedule_semantic_operation(refresh, command_file="refresh.json", completion=completion),
                expected[0],
            )
            self.assertEqual(
                scheduler.schedule_semantic_operation(relay, command_file="relay.json", completion=completion),
                expected[1],
            )
            self.assertEqual(
                scheduler.schedule_semantic_operation(ess, command_file="ess.json", completion=completion),
                expected[2],
            )
        refresh_handler.assert_called_once_with(refresh, "refresh.json", completion)
        relay_handler.assert_called_once_with(relay, "relay.json", completion)
        ess_handler.assert_called_once_with(ess, "ess.json", completion)

    def test_dispatches_generic_shelly_and_drops_unknown_kinds(self) -> None:
        scheduler, adapter = _scheduler()
        completion = MagicMock()
        command: CommandMapping = {"kind": DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND}
        executor = MagicMock()
        executor.schedule.return_value = CommandExecution.immediate("applied")
        with patch.object(
            semantic_module,
            "GenericShellyConfigurationExecutor",
            return_value=executor,
        ) as factory:
            self.assertEqual(
                scheduler.schedule_semantic_operation(
                    command,
                    command_file="shelly.json",
                    completion=completion,
                ),
                CommandExecution.immediate("applied"),
            )
        factory.assert_called_once_with(adapter)
        executor.schedule.assert_called_once_with(command, "shelly.json", completion)
        for unknown in ({"kind": "unknown"}, {}):
            self.assertEqual(
                scheduler.schedule_semantic_operation(
                    unknown,
                    command_file="x",
                    completion=completion,
                ),
                CommandExecution.immediate("dropped"),
            )


class RelayRefreshAndPhaseMutationContracts(unittest.TestCase):
    def test_refresh_completes_only_after_a_binary_reply(self) -> None:
        scheduler, adapter = _scheduler()
        execution, outcomes = _schedule(scheduler, gx_relay_refresh_command(1))
        self.assertEqual(execution, CommandExecution.pending())
        self.assertEqual(outcomes, [])
        operation = adapter.operation_broker.operations[0]
        self.assertEqual((operation.rate_kind, operation.metric_kind), ("read", "read"))
        self.assertEqual(operation.source, f"{_SYSTEM_SERVICE}/Relay/1/State")

        adapter.operation_broker.succeed(0)

        self.assertEqual(outcomes, ["applied"])
        adapter.cache.update_external_read.assert_called_once_with(
            gx_relay_state_key(1),
            0,
            source=f"{_SYSTEM_SERVICE}/Relay/1/State",
            confidence=1.0,
        )

    def test_refresh_rejects_bad_commands_values_and_transport_errors(self) -> None:
        scheduler, adapter = _scheduler()
        execution, outcomes = _schedule(scheduler, {"kind": GX_RELAY_REFRESH_KIND})
        self.assertEqual(execution, CommandExecution.immediate("dropped"))
        self.assertEqual(outcomes, [])

        _schedule(scheduler, gx_relay_refresh_command(0))
        adapter.operation_broker.succeed(True)
        self.assertEqual(adapter.cache.mark_error.call_args.args[0], gx_relay_state_key(0))

        adapter.cache.reset_mock()
        _execution, outcomes = _schedule(scheduler, gx_relay_refresh_command(0))
        adapter.operation_broker.fail(OSError("offline"))
        self.assertEqual(outcomes, ["deferred"])
        adapter.cache.assert_not_called()

    def test_set_relay_rejects_invalid_input_and_routes_every_phase(self) -> None:
        scheduler, _adapter = _scheduler()
        completion = MagicMock()
        self.assertEqual(
            scheduler._set_gx_relay({"kind": GX_RELAY_SET_KIND}, "relay.json", completion),
            CommandExecution.immediate("dropped"),
        )
        command = _relay_command()
        self.assertEqual(
            scheduler._set_gx_relay(command, "", completion),
            CommandExecution.immediate("dropped"),
        )
        handlers = {
            "manual_read": "_read_manual_function",
            "manual_write": "_write_manual_function",
            "output": "_write_relay_output",
            "verify": "_verify_relay_output",
            "retry": "_retry_relay_output",
        }
        for phase, name in handlers.items():
            with self.subTest(phase=phase):
                command = _relay_command(phase=phase)
                expected = CommandExecution.immediate("applied")
                with patch.object(scheduler, name, return_value=expected) as handler:
                    self.assertEqual(
                        scheduler._set_gx_relay(command, "relay.json", completion),
                        expected,
                    )
                handler.assert_called_once_with(command, "relay.json", _operation(command), completion)


class RelayLifecycleMutationContracts(unittest.TestCase):
    def test_rewrite_drops_a_stale_callback_generation_without_changing_payload_identity(self) -> None:
        scheduler, adapter = _scheduler()
        command: CommandMapping = {"kind": GX_RELAY_SET_KIND, "phase": "verify"}
        adapter.commands.replace_if_current.return_value = False

        self.assertEqual(
            scheduler._rewrite("relay.json", command, phase="retry"),
            "dropped",
        )
        adapter.commands.replace_if_current.assert_called_once_with(
            "relay.json",
            command,
            {**command, "phase": "retry"},
        )

    def test_manual_read_selects_next_phase_and_fallback_target(self) -> None:
        scheduler, adapter = _scheduler()
        command = _relay_command(phase="manual_read")
        _execution, outcomes = _schedule(scheduler, command, "relay.json")
        adapter.operation_broker.succeed(2)
        self.assertEqual(outcomes, ["deferred"])
        adapter.commands.replace_if_current.assert_called_once_with(
            "relay.json", command, {**command, "phase": "output"}
        )

        adapter.commands.replace_if_current.reset_mock()
        command = _relay_command(phase="manual_read", manual_target=1)
        _schedule(scheduler, command, "relay.json")
        self.assertEqual(
            adapter.operation_broker.operations[0].source,
            f"{_SETTINGS_SERVICE}/Settings/Relay/Function",
        )
        adapter.operation_broker.succeed(0)
        self.assertEqual(adapter.commands.replace_if_current.call_args.args[2]["phase"], "manual_write")

        adapter.commands.replace_if_current.reset_mock()
        command = _relay_command(phase="manual_read", manual_target=0)
        _schedule(scheduler, command, "relay.json")
        adapter.operation_broker.fail(OSError("offline"))
        rewritten = adapter.commands.replace_if_current.call_args.args[2]
        self.assertEqual((rewritten["phase"], rewritten["manual_target"]), ("manual_read", 1))

    def test_manual_fallback_after_last_target_uses_minimum_or_configured_backoff(self) -> None:
        scheduler, adapter = _scheduler()
        for command, expected in (
            (_relay_command(manual_target=1, retry=0.25), 101.0),
            (_relay_command(relay_index=1, retry=4.5), 104.5),
        ):
            adapter.commands.replace_if_current.reset_mock()
            with patch.object(semantic_module.time, "time", return_value=100.0):
                self.assertEqual(
                    scheduler._manual_fallback(command, "relay.json", _operation(command)),
                    "deferred",
                )
            self.assertEqual(
                adapter.commands.replace_if_current.call_args.args[2]["not_before"],
                expected,
            )

    def test_manual_write_advances_only_after_reply_and_retries_on_error(self) -> None:
        scheduler, adapter = _scheduler()
        command = _relay_command(phase="manual_write", manual_target=1)
        _execution, outcomes = _schedule(scheduler, command, "relay.json")
        operation = adapter.operation_broker.operations[0]
        self.assertEqual(operation.rate_kind, "write")
        self.assertEqual(operation.source, f"{_SETTINGS_SERVICE}/Settings/Relay/Function")
        adapter.operation_broker.succeed()
        self.assertEqual(outcomes, ["deferred"])
        self.assertEqual(adapter.commands.replace_if_current.call_args.args[2]["phase"], "output")

        adapter.commands.replace_if_current.reset_mock()
        command = _relay_command(phase="manual_write", manual_target=0)
        _schedule(scheduler, command, "relay.json")
        adapter.operation_broker.fail(OSError("offline"))
        self.assertEqual(
            adapter.commands.replace_if_current.call_args.args[2]["manual_target"],
            1,
        )

    def test_output_and_retry_rewrite_exact_verification_state(self) -> None:
        scheduler, adapter = _scheduler()
        with patch.object(semantic_module.time, "time", return_value=40.0):
            output = _relay_command(relay_index=1, phase="output", settle=2.5)
            _schedule(scheduler, output, "output.json")
            adapter.operation_broker.succeed()
        self.assertEqual(
            adapter.commands.replace_if_current.call_args.args,
            (
                "output.json",
                output,
                {**output, "phase": "verify", "not_before": 42.5},
            ),
        )

        adapter.commands.replace_if_current.reset_mock()
        with patch.object(semantic_module.time, "time", return_value=50.0):
            retry = _relay_command(phase="retry", retries=2, settle=1.5)
            _schedule(scheduler, retry, "retry.json")
            adapter.operation_broker.succeed()
        payload = adapter.commands.replace_if_current.call_args.args[2]
        self.assertEqual((payload["phase"], payload["retries"], payload["not_before"]), ("verify", 3, 51.5))

        _execution, outcomes = _schedule(scheduler, output, "error.json")
        adapter.operation_broker.fail(OSError("offline"))
        self.assertEqual(outcomes, ["deferred"])

    def test_verify_handles_match_retry_terminal_mismatch_and_read_error(self) -> None:
        scheduler, adapter = _scheduler()
        matching = _relay_command(relay_index=1, phase="verify", enabled=False)
        _execution, outcomes = _schedule(scheduler, matching, "relay.json")
        adapter.operation_broker.succeed(0)
        self.assertEqual(outcomes, ["applied"])
        adapter.cache.update_external_read.assert_called_once()

        adapter.cache.reset_mock()
        with patch.object(semantic_module.time, "time", return_value=50.0):
            first = _relay_command(phase="verify", retries=0, retry=3.5)
            _execution, outcomes = _schedule(scheduler, first, "first.json")
            adapter.operation_broker.succeed(0)
        self.assertEqual(outcomes, ["deferred"])
        self.assertEqual(
            adapter.commands.replace_if_current.call_args.args[2]["not_before"],
            53.5,
        )

        later = _relay_command(phase="verify", retries=1, enabled=False)
        _execution, outcomes = _schedule(scheduler, later, "later.json")
        adapter.operation_broker.succeed(1)
        self.assertEqual(outcomes, ["dropped"])
        self.assertIn("relay stayed at 1, expected 0", str(adapter.cache.mark_error.call_args))

        adapter.cache.reset_mock()
        _execution, outcomes = _schedule(scheduler, matching, "error.json")
        error = OSError("offline")
        adapter.operation_broker.fail(error)
        self.assertEqual(outcomes, ["applied"])
        self.assertIs(adapter.cache.mark_error.call_args.kwargs["error"], error)


class EssAndPureHelperMutationContracts(unittest.TestCase):
    def test_ess_rejects_invalid_or_disabled_targets(self) -> None:
        scheduler, adapter = _scheduler()
        completion = MagicMock()
        self.assertEqual(
            scheduler._set_ess_grid_setpoint(
                {"kind": ESS_GRID_SETPOINT_KIND},
                "ess.json",
                completion,
            ),
            CommandExecution.immediate("dropped"),
        )
        for key in (
            "AutoBatteryDischargeBalanceVictronBiasService",
            "AutoBatteryDischargeBalanceVictronBiasPath",
        ):
            adapter.config["DEFAULT"].clear()
            adapter.config["DEFAULT"][key] = ""
            self.assertEqual(
                scheduler._set_ess_grid_setpoint(
                    ess_grid_setpoint_command(1.0, intent="tracking"),
                    "ess.json",
                    completion,
                ),
                CommandExecution.immediate("dropped"),
            )

    def test_ess_caches_only_a_confirmed_write(self) -> None:
        adapter = _Adapter(
            "[DEFAULT]\n"
            "AutoBatteryDischargeBalanceVictronBiasService=com.example.settings\n"
            "AutoBatteryDischargeBalanceVictronBiasPath=/Custom/Setpoint\n"
        )
        scheduler, _adapter = _scheduler(adapter)
        command = ess_grid_setpoint_command(-17.5, intent="restore")
        _execution, outcomes = _schedule(scheduler, command)
        operation = adapter.operation_broker.operations[0]
        self.assertEqual((operation.rate_kind, operation.priority), ("write", "safety"))
        self.assertEqual(operation.source, "com.example.settings/Custom/Setpoint")
        adapter.cache.update_value.assert_not_called()
        adapter.operation_broker.succeed()
        self.assertEqual(outcomes, ["applied"])
        adapter.cache.update_value.assert_called_once_with(
            "path:com.example.settings/Custom/Setpoint",
            -17.5,
            source="com.example.settings/Custom/Setpoint",
            confidence=0.9,
            freshness_kind="external_read",
        )

        _execution, outcomes = _schedule(scheduler, command)
        adapter.operation_broker.fail(OSError("offline"))
        self.assertEqual(outcomes, ["deferred"])

    def test_binary_priority_target_rewrite_and_path_contracts(self) -> None:
        scheduler, adapter = _scheduler()
        scenarios = ((0, 0), (1, 1), (0.0, 0), (1.0, 1), (True, None), (2, None), ("bad", None))
        for value, expected in scenarios:
            self.assertEqual(binary_relay_state(value), expected)
        self.assertEqual(scheduler._priority({}), "user")
        self.assertEqual(scheduler._priority({"priority": "safety"}), "safety")
        self.assertEqual(
            scheduler._ess_grid_setpoint_target(),
            (_SETTINGS_SERVICE, "/Settings/CGwacs/AcPowerSetPoint"),
        )
        command: CommandMapping = {"kind": "original", "phase": "old", "retries": 1}
        self.assertEqual(
            scheduler._rewrite("command.json", command, phase="new", not_before=12.5),
            "deferred",
        )
        adapter.commands.replace_if_current.assert_called_once_with(
            "command.json",
            command,
            {"kind": "original", "phase": "new", "retries": 1, "not_before": 12.5},
        )
        self.assertEqual(relay_state_path(1), "/Relay/1/State")
        self.assertEqual(
            manual_function_paths(0),
            ("/Settings/Relay/0/Function", "/Settings/Relay/Function"),
        )
        self.assertEqual(
            manual_function_paths(1),
            ("/Settings/Relay/1/Function",),
        )
        self.assertFalse(relay_state_matches(None, 0))
        self.assertFalse(relay_state_matches(0, 1))
        self.assertTrue(relay_state_matches(1, 1))


if __name__ == "__main__":
    unittest.main()
