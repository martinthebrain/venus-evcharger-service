# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end contracts for semantic GX relay and ESS gateway operations."""

from __future__ import annotations

import configparser
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, cast
from unittest.mock import MagicMock, call, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs
from tests.support.async_dbus import ImmediateAsyncBroker, run_semantic_operation

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.dbus_adapter.write import semantic as semantic_module
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.semantic import SemanticWriteExecutor
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox, gateway_paths, read_json_file
from venus_evcharger.dbus_gateway_client import GatewayClient, GatewayOperationsClient
from venus_evcharger.dbus_gateway_policy import command_queue_class
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.gateway_operations import (
    ESS_GRID_SETPOINT_KIND,
    GX_RELAY_REFRESH_KIND,
    GX_RELAY_SET_KIND,
    ess_grid_setpoint_command,
    gx_relay_refresh_command,
    gx_relay_set_command,
    gx_relay_state_key,
    parse_ess_grid_setpoint,
    parse_gx_relay_refresh,
    parse_gx_relay_set,
)
from venus_evcharger.ipc.enqueue_result import GatewayEnqueueResult
from venus_evcharger.ports.gateway_operations import GxRelayContactMode, GxRelaySetRequest


class _DbusCall(NamedTuple):
    service: str
    path: str
    interface: str
    method: str
    signature: str
    args: tuple[object, ...]
    timeout: float


class _Connection:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.get_error: BaseException | None = None
        self.set_error: BaseException | None = None
        self.calls: list[_DbusCall] = []

    def send_async(
        self,
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object:
        self.calls.append(
            _DbusCall(
                request.service,
                request.path,
                request.interface,
                request.method_name,
                request.signature,
                request.args,
                request.timeout_seconds,
            )
        )
        if request.method_name == "GetValue":
            if self.get_error is not None:
                error_handler(self.get_error)
            else:
                reply_handler(self.value)
        elif request.method_name == "SetValue":
            if self.set_error is not None:
                error_handler(self.set_error)
            else:
                reply_handler()
        else:
            raise AssertionError(f"unexpected DBus method: {request.method_name}")
        return object()


def _read_call(service: str, path: str) -> _DbusCall:
    return _DbusCall(
        service,
        path,
        "com.victronenergy.BusItem",
        "GetValue",
        "",
        (),
        1.0,
    )


def _write_call(service: str, path: str, value: object) -> _DbusCall:
    return _DbusCall(
        service,
        path,
        "com.victronenergy.BusItem",
        "SetValue",
        "v",
        (value,),
        1.0,
    )


class _Circuit:
    @staticmethod
    def allows_priority(_priority: str) -> bool:
        return True


class _Adapter:
    def __init__(self, root: Path, config: str = "[DEFAULT]\n") -> None:
        parser = configparser.ConfigParser()
        parser.read_string(config)
        self.config = parser
        paths = gateway_paths(str(root / "run"))
        self.cache = DbusCacheStore(paths)
        self.commands = DbusGatewayCommandInbox(paths.command_dir)
        self.connection = _Connection()
        self.circuit = _Circuit()
        self.operations: list[str] = []
        self.operation_broker = ImmediateAsyncBroker(self.operations)


def _scheduler(adapter: _Adapter) -> SemanticWriteExecutor:
    return SemanticWriteExecutor(cast(SemanticWriteAdapter, adapter))


def _write_command(path: Path, command: CommandMapping) -> None:
    AtomicJsonWriter().write(str(path), command)


def _command(path: Path) -> CommandPayload:
    payload = read_json_file(str(path))
    assert isinstance(payload, dict)
    return payload


class GatewaySemanticWireContractTests(unittest.TestCase):
    def test_relay_builders_are_semantic_coalesced_and_safety_aware(self) -> None:
        refresh = gx_relay_refresh_command(1)
        self.assertEqual(refresh["kind"], GX_RELAY_REFRESH_KIND)
        self.assertEqual(command_queue_class(refresh), "read-fast")
        enabled = gx_relay_set_command(
            1,
            "NC",
            True,
            ensure_manual=True,
            verify_settle_seconds=4.0,
            verify_retry_seconds=3.0,
        )
        disabled = gx_relay_set_command(
            1,
            "NC",
            False,
            ensure_manual=False,
            verify_settle_seconds=0.0,
            verify_retry_seconds=0.0,
        )
        self.assertEqual(enabled["kind"], GX_RELAY_SET_KIND)
        self.assertEqual(enabled["phase"], "manual_read")
        self.assertEqual(enabled["deadline_s"], 16.0)
        self.assertEqual(enabled["priority"], "user")
        self.assertEqual(disabled["phase"], "output")
        self.assertEqual(disabled["priority"], "safety")
        self.assertNotIn("deadline_s", disabled)
        self.assertEqual(enabled["coalesce_key"], disabled["coalesce_key"])
        self.assertTrue({"service", "path", "value"}.isdisjoint(enabled))
        operation = parse_gx_relay_set(enabled)
        assert operation is not None
        self.assertEqual(operation.target_state, 0)

    def test_ess_builder_preserves_intent_priority_and_finite_value(self) -> None:
        tracking = ess_grid_setpoint_command(42, intent="tracking")
        restore = ess_grid_setpoint_command(-3.5, intent="restore")
        self.assertEqual(tracking["kind"], ESS_GRID_SETPOINT_KIND)
        self.assertEqual(tracking["priority"], "user")
        self.assertEqual(restore["priority"], "safety")
        self.assertEqual(tracking["coalesce_key"], restore["coalesce_key"])
        self.assertEqual(command_queue_class(tracking), "remote-write")
        self.assertTrue({"service", "path", "value"}.isdisjoint(tracking))
        operation = parse_ess_grid_setpoint(restore)
        assert operation is not None
        self.assertEqual(operation.watts, -3.5)

    def test_builders_and_parsers_reject_invalid_wire_values(self) -> None:
        with self.assertRaises(ValueError):
            gx_relay_refresh_command(2)
        with self.assertRaises(ValueError):
            gx_relay_set_command(
                0,
                cast(GxRelayContactMode, "invalid"),
                True,
                ensure_manual=True,
                verify_settle_seconds=0,
                verify_retry_seconds=0,
            )
        with self.assertRaises(TypeError):
            gx_relay_set_command(
                0,
                "NO",
                cast(bool, 1),
                ensure_manual=True,
                verify_settle_seconds=0,
                verify_retry_seconds=0,
            )
        with self.assertRaises(ValueError):
            gx_relay_set_command(0, "NO", True, ensure_manual=True, verify_settle_seconds=-1, verify_retry_seconds=0)
        with self.assertRaises(ValueError):
            ess_grid_setpoint_command(float("nan"), intent="tracking")
        self.assertIsNone(parse_gx_relay_refresh({"kind": GX_RELAY_REFRESH_KIND, "relay_index": True}))
        self.assertIsNone(parse_gx_relay_set({"kind": GX_RELAY_SET_KIND}))
        self.assertIsNone(
            parse_ess_grid_setpoint({"kind": ESS_GRID_SETPOINT_KIND, "watts": True, "intent": "tracking"})
        )

    def test_gateway_client_reads_semantic_cache_and_requests_refresh_on_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            cache = DbusCacheStore(paths)
            cache.update_external_read(gx_relay_state_key(0), 1, source="test")
            cache.write_cache_snapshot()
            client = GatewayClient(paths)
            operations = GatewayOperationsClient(client)
            self.assertEqual(operations.read_gx_relay_state(0, max_age_seconds=5.0), 1)
            with patch.object(client, "backpressure_state", return_value="ok"):
                self.assertIsNone(operations.read_gx_relay_state(1, max_age_seconds=5.0))
            pending = GatewayClient(paths).commands.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["kind"], GX_RELAY_REFRESH_KIND)

    def test_gateway_client_receipts_reflect_transport_acceptance(self) -> None:
        client = MagicMock(spec=GatewayClient)
        client.enqueue_command.side_effect = [
            GatewayEnqueueResult(True, "relay-command", "mailbox"),
            GatewayEnqueueResult(True, "ess-command", "mailbox"),
            GatewayEnqueueResult(False, reason="backpressure"),
        ]
        operations = GatewayOperationsClient(client)
        request = GxRelaySetRequest(
            relay_index=0,
            contact_mode="NO",
            enabled=True,
            ensure_manual=True,
            verify_settle_seconds=0.1,
            verify_retry_seconds=0.2,
        )
        relay = operations.set_gx_relay_enabled(request)
        ess = operations.set_ess_grid_setpoint(10.0, intent="tracking")
        rejected = operations.set_ess_grid_setpoint(20.0, intent="restore")

        self.assertTrue(relay.accepted)
        self.assertEqual(relay.command_id, "relay-command")
        self.assertTrue(ess.accepted)
        self.assertEqual(ess.command_id, "ess-command")
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.command_id, "")
        self.assertEqual(rejected.reason, "backpressure")
        client.enqueue_command.assert_has_calls(
            [
                call(
                    gx_relay_set_command(
                        request.relay_index,
                        request.contact_mode,
                        request.enabled,
                        ensure_manual=request.ensure_manual,
                        verify_settle_seconds=request.verify_settle_seconds,
                        verify_retry_seconds=request.verify_retry_seconds,
                    )
                ),
                call(ess_grid_setpoint_command(10.0, intent="tracking")),
                call(ess_grid_setpoint_command(20.0, intent="restore")),
            ]
        )


class GatewaySemanticAdapterTests(unittest.TestCase):
    def test_relay_refresh_accepts_binary_state_and_rejects_malformed_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            cache_state = MagicMock()
            adapter.connection.value = 1
            with patch.object(scheduler, "_cache_relay_state", cache_state):
                self.assertEqual(
                    run_semantic_operation(scheduler, gx_relay_refresh_command(0), command_file=""),
                    "applied",
                )
            cache_state.assert_called_once_with(0, "/Relay/0/State", 1)
            self.assertEqual(
                adapter.connection.calls,
                [_read_call("com.victronenergy.system", "/Relay/0/State")],
            )
            self.assertEqual(
                run_semantic_operation(
                    scheduler,
                    {"kind": GX_RELAY_REFRESH_KIND, "relay_index": True},
                    command_file="",
                ),
                "dropped",
            )

    def test_deferred_manual_and_verify_operations_propagate_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            command_path = Path(temp_dir) / "relay.json"
            base = gx_relay_set_command(
                0,
                "NO",
                True,
                ensure_manual=True,
                verify_settle_seconds=0,
                verify_retry_seconds=0,
            )
            scenarios = (
                {**base, "phase": "manual_read"},
                {**base, "phase": "manual_write"},
                {**base, "phase": "verify"},
            )
            for command in scenarios:
                with self.subTest(phase=command["phase"]):
                    _write_command(command_path, command)
                    with patch.object(
                        adapter.operation_broker,
                        "submit",
                        side_effect=DbusOperationDeferred("busy"),
                    ):
                        with self.assertRaises(DbusOperationDeferred):
                            run_semantic_operation(scheduler, command, command_file=str(command_path))
                    self.assertEqual(_command(command_path), command)

    def test_manual_write_error_uses_next_supported_settings_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            command_path = Path(temp_dir) / "relay.json"
            command = {
                **gx_relay_set_command(
                    0,
                    "NO",
                    True,
                    ensure_manual=True,
                    verify_settle_seconds=0,
                    verify_retry_seconds=0,
                ),
                "phase": "manual_write",
            }
            _write_command(command_path, command)
            adapter.connection.set_error = OSError("missing primary")

            self.assertEqual(
                run_semantic_operation(scheduler, command, command_file=str(command_path)),
                "deferred",
            )

            rewritten = _command(command_path)
            self.assertEqual((rewritten["phase"], rewritten["manual_target"]), ("manual_read", 1))
            self.assertEqual(
                adapter.connection.calls,
                [
                    _write_call(
                        "com.victronenergy.settings",
                        "/Settings/Relay/0/Function",
                        2,
                    )
                ],
            )

    def test_malformed_ess_operation_is_dropped_without_dbus_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)

            self.assertEqual(
                run_semantic_operation(
                    scheduler,
                    {"kind": ESS_GRID_SETPOINT_KIND, "watts": True, "intent": "tracking"},
                    command_file="",
                ),
                "dropped",
            )
            self.assertEqual(adapter.operations, [])

    def test_manual_check_write_output_and_verify_each_use_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            command_path = Path(temp_dir) / "relay.json"
            command = gx_relay_set_command(
                0,
                "NO",
                True,
                ensure_manual=True,
                verify_settle_seconds=0.0,
                verify_retry_seconds=0.0,
            )
            _write_command(command_path, command)
            adapter.connection.value = 0
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            self.assertEqual(adapter.operations, ["read"])
            command = _command(command_path)
            self.assertEqual(command["phase"], "manual_write")

            adapter.operations.clear()
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            self.assertEqual(adapter.operations, ["write"])
            command = _command(command_path)
            self.assertEqual(command["phase"], "output")

            adapter.operations.clear()
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            self.assertEqual(adapter.operations, ["write"])
            command = _command(command_path)
            self.assertEqual(command["phase"], "verify")

            adapter.operations.clear()
            adapter.connection.value = 1
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "applied")
            self.assertEqual(adapter.operations, ["read"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _read_call(
                        "com.victronenergy.settings",
                        "/Settings/Relay/0/Function",
                    ),
                    _write_call(
                        "com.victronenergy.settings",
                        "/Settings/Relay/0/Function",
                        2,
                    ),
                    _write_call("com.victronenergy.system", "/Relay/0/State", 1),
                    _read_call("com.victronenergy.system", "/Relay/0/State"),
                ],
            )
            self.assertEqual(adapter.cache.values[gx_relay_state_key(0)]["value"], 1)

    def test_manual_check_skips_write_when_already_manual_and_uses_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            command_path = Path(temp_dir) / "relay.json"
            command = gx_relay_set_command(
                0, "NO", False, ensure_manual=True, verify_settle_seconds=0, verify_retry_seconds=0
            )
            _write_command(command_path, command)
            adapter.connection.value = 2
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            self.assertEqual(_command(command_path)["phase"], "output")

            adapter.connection.get_error = OSError("missing primary")
            _write_command(command_path, command)
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            fallback = _command(command_path)
            self.assertEqual((fallback["phase"], fallback["manual_target"]), ("manual_read", 1))
            self.assertEqual(
                adapter.connection.calls,
                [
                    _read_call(
                        "com.victronenergy.settings",
                        "/Settings/Relay/0/Function",
                    ),
                    _read_call(
                        "com.victronenergy.settings",
                        "/Settings/Relay/0/Function",
                    ),
                ],
            )

    def test_unreachable_manual_targets_are_deferred_with_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            command_path = Path(temp_dir) / "relay.json"
            command = {
                **gx_relay_set_command(
                    0,
                    "NO",
                    False,
                    ensure_manual=True,
                    verify_settle_seconds=0,
                    verify_retry_seconds=0.2,
                ),
                "manual_target": 1,
            }
            _write_command(command_path, command)
            adapter.connection.get_error = OSError("offline")
            with patch.object(semantic_module.time, "time", return_value=100.0):
                self.assertEqual(
                    run_semantic_operation(scheduler, command, command_file=str(command_path)),
                    "deferred",
                )
            deferred = _command(command_path)
            self.assertEqual(deferred["not_before"], 101.0)
            self.assertEqual(deferred["manual_target"], 1)
            self.assertEqual(adapter.operations, ["read"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _read_call(
                        "com.victronenergy.settings",
                        "/Settings/Relay/Function",
                    )
                ],
            )

    def test_relay_mismatch_retries_once_then_drops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            command_path = Path(temp_dir) / "relay.json"
            command = gx_relay_set_command(
                1, "NO", True, ensure_manual=False, verify_settle_seconds=0, verify_retry_seconds=0
            )
            command = {**command, "phase": "verify"}
            _write_command(command_path, command)
            adapter.connection.value = 0
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            command = _command(command_path)
            self.assertEqual(command["phase"], "retry")
            adapter.operations.clear()
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "deferred")
            self.assertEqual(adapter.operations, ["write"])
            command = _command(command_path)
            self.assertEqual((command["phase"], command["retries"]), ("verify", 1))
            adapter.operations.clear()
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "dropped")
            self.assertEqual(adapter.operations, ["read"])
            self.assertEqual(
                adapter.connection.calls,
                [
                    _read_call("com.victronenergy.system", "/Relay/1/State"),
                    _write_call("com.victronenergy.system", "/Relay/1/State", 1),
                    _read_call("com.victronenergy.system", "/Relay/1/State"),
                ],
            )
            self.assertEqual(adapter.cache.values[gx_relay_state_key(1)]["status"], "error")

    def test_refresh_and_verification_errors_update_semantic_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(Path(temp_dir))
            scheduler = _scheduler(adapter)
            adapter.connection.value = 2
            self.assertEqual(
                run_semantic_operation(scheduler, gx_relay_refresh_command(0), command_file=""), "dropped"
            )
            self.assertEqual(adapter.cache.values[gx_relay_state_key(0)]["status"], "error")

            command_path = Path(temp_dir) / "relay.json"
            command = {
                **gx_relay_set_command(
                    0, "NO", True, ensure_manual=False, verify_settle_seconds=0, verify_retry_seconds=0
                ),
                "phase": "verify",
            }
            _write_command(command_path, command)
            adapter.connection.get_error = OSError("offline")
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=str(command_path)), "applied")
            self.assertEqual(adapter.cache.values[gx_relay_state_key(0)]["last_error"], "offline")
            self.assertEqual(
                adapter.connection.calls,
                [
                    _read_call("com.victronenergy.system", "/Relay/0/State"),
                    _read_call("com.victronenergy.system", "/Relay/0/State"),
                ],
            )

    def test_ess_mapping_is_adapter_owned_configurable_and_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(
                Path(temp_dir),
                "[DEFAULT]\n"
                "AutoBatteryDischargeBalanceVictronBiasService=com.example.settings\n"
                "AutoBatteryDischargeBalanceVictronBiasPath=/Custom/Setpoint\n",
            )
            scheduler = _scheduler(adapter)
            command = ess_grid_setpoint_command(17.5, intent="tracking")
            self.assertEqual(run_semantic_operation(scheduler, command, command_file=""), "applied")
            self.assertEqual(adapter.operations, ["write"])
            self.assertEqual(
                adapter.connection.calls,
                [_write_call("com.example.settings", "/Custom/Setpoint", 17.5)],
            )

    def test_malformed_disabled_and_not_yet_due_commands_do_no_dbus_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _Adapter(
                Path(temp_dir),
                "[DEFAULT]\nAutoBatteryDischargeBalanceVictronBiasService=\nAutoBatteryDischargeBalanceVictronBiasPath=\n",
            )
            scheduler = _scheduler(adapter)
            self.assertEqual(run_semantic_operation(scheduler, {"kind": "unknown"}, command_file=""), "dropped")
            self.assertEqual(
                run_semantic_operation(scheduler, {"kind": GX_RELAY_SET_KIND}, command_file="command.json"),
                "dropped",
            )
            self.assertEqual(
                run_semantic_operation(scheduler, ess_grid_setpoint_command(1.0, intent="tracking"), command_file=""),
                "dropped",
            )
            self.assertEqual(adapter.operations, [])


if __name__ == "__main__":
    unittest.main()
