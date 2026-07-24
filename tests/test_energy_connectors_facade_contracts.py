# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for the energy connector registry facade."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.energy import connectors
from venus_evcharger.energy.bounded_subprocess import BoundedCommandResult
from venus_evcharger.energy.connectors_command import CommandJsonEnergySourceSettings
from venus_evcharger.energy.connectors_modbus import (
    ModbusEnergyFieldSettings,
    ModbusEnergySourceSettings,
)
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.energy.read_steps import completed_read


class _DeadlineRuntime:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.requests: list[float] = []

    def bounded_request_timeout_seconds(self, configured: float) -> float:
        self.requests.append(configured)
        return self.timeout_seconds


def _transport_settings(timeout_seconds: float = 1.25) -> ModbusTransportSettings:
    return ModbusTransportSettings(
        transport_kind="tcp",
        unit_id=7,
        timeout_seconds=timeout_seconds,
        host="192.0.2.7",
        port=502,
        device=None,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        serial_port_owner="none",
        serial_port_owner_stop_command=None,
        serial_port_owner_start_command=None,
        serial_retry_count=0,
        serial_retry_delay_seconds=0.0,
    )


def _modbus_settings(timeout_seconds: float = 1.25) -> ModbusEnergySourceSettings:
    return ModbusEnergySourceSettings(
        transport_settings=_transport_settings(timeout_seconds),
        soc_field=None,
        usable_capacity_field=None,
        battery_power_field=None,
        charge_limit_power_field=None,
        discharge_limit_power_field=None,
        ac_power_field=None,
        pv_input_power_field=None,
        grid_interaction_field=None,
        operating_mode_field=None,
        operating_mode_map={},
        ac_power_scope_key="",
        pv_input_power_scope_key="",
        grid_interaction_scope_key="",
    )


def _command_settings() -> CommandJsonEnergySourceSettings:
    return CommandJsonEnergySourceSettings(
        command=("helper", "--once"),
        timeout_seconds=2.5,
        soc_path="soc",
        usable_capacity_wh_path=None,
        battery_power_path=None,
        ac_power_path=None,
        pv_input_power_path=None,
        grid_interaction_path=None,
        operating_mode_path=None,
        online_path=None,
        confidence_path=None,
    )


class EnergyConnectorsFacadeContractTests(unittest.TestCase):
    def test_registry_dispatch_forwards_owner_source_and_timestamp_exactly(self) -> None:
        owner = object()
        now = 123.5
        snapshot = EnergySourceSnapshot(source_id="result", role="battery", service_name="service")
        result = completed_read(snapshot)
        connector_types = (
            "template_http",
            "opendtu_http",
            "modbus",
            "command_json",
        )
        self.assertEqual(set(connectors._ENERGY_SOURCE_STEP_READERS), set(connector_types))
        for connector_type in connector_types:
            source = EnergySourceDefinition(source_id=connector_type, connector_type=connector_type)
            reader = MagicMock(return_value=result)
            with (
                self.subTest(connector_type=connector_type),
                patch.dict(
                    connectors._ENERGY_SOURCE_STEP_READERS,
                    {connector_type: reader},
                ),
            ):
                self.assertIs(connectors.read_energy_source_step(owner, source, now), result)
                reader.assert_called_once_with(owner, source, now)

        for source, expected in (
            (EnergySourceDefinition(source_id="missing"), "<empty>"),
            (EnergySourceDefinition(source_id="retired", connector_type="dbus"), "dbus"),
        ):
            with (
                self.subTest(connector=expected),
                self.assertRaisesRegex(
                    ValueError,
                    f"Unsupported energy-source connector: {expected}",
                ),
            ):
                connectors.read_energy_source_step(owner, source, now)

    def test_modbus_client_cache_is_keyed_by_config_path_and_uses_transport_settings(self) -> None:
        runtime = SimpleNamespace()
        settings = _modbus_settings()
        transport_settings = settings.transport_settings
        transport_a = object()
        transport_b = object()
        source_a = EnergySourceDefinition(source_id="a", config_path=" a.ini ")
        source_b = EnergySourceDefinition(source_id="b", config_path="b.ini")

        with patch.object(
            connectors,
            "create_modbus_transport",
            side_effect=(transport_a, transport_b),
        ) as create_transport:
            client_a = connectors._modbus_energy_source_client(runtime, source_a, settings)
            self.assertIs(connectors._modbus_energy_source_client(runtime, source_a, settings), client_a)
            client_b = connectors._modbus_energy_source_client(runtime, source_b, settings)

        self.assertIsNot(client_a, client_b)
        self.assertEqual(create_transport.call_args_list, [call(transport_settings), call(transport_settings)])
        self.assertEqual(
            set(runtime._energy_connector_runtime_state.caches["modbus.clients"]),
            {"a.ini", "b.ini"},
        )
        self.assertIs(client_a.transport, transport_a)
        self.assertEqual(client_a.unit_id, 7)
        self.assertEqual(client_a.timeout_seconds, 1.25)

    def test_modbus_operating_mode_progress_value_is_normalized_and_mapped(self) -> None:
        settings = replace(
            _modbus_settings(),
            operating_mode_map={"12": "running"},
        )
        self.assertEqual(
            connectors._modbus_progress_value("operating_mode", 12.0, settings),
            "running",
        )
        self.assertEqual(
            connectors._modbus_progress_value("operating_mode", 12.5, settings),
            "12.5",
        )
        self.assertEqual(
            connectors._modbus_progress_value("soc", 12.0, settings),
            12.0,
        )

    def test_modbus_snapshot_wrapper_uses_service_runtime_and_exact_builder_arguments(self) -> None:
        runtime = _DeadlineRuntime(0.25)
        owner = SimpleNamespace(service=runtime)
        source = EnergySourceDefinition(source_id="modbus", connector_type="modbus")
        value_field = ModbusEnergyFieldSettings(
            register_type="holding",
            address=10,
            data_type="uint16",
            scale=1.0,
            word_order="big",
        )
        settings = replace(_modbus_settings(), soc_field=value_field)
        client = MagicMock(spec=ModbusClient)
        client.timeout_seconds = 9.0
        result = EnergySourceSnapshot("modbus", "battery", "service")
        with (
            patch.object(connectors, "_modbus_energy_source_settings", return_value=settings) as load_settings,
            patch.object(connectors, "_modbus_energy_source_client", return_value=client) as load_client,
            patch.object(connectors, "_modbus_field_value", return_value=42.5) as read_value,
            patch.object(connectors, "_build_modbus_energy_source_snapshot", return_value=result) as build,
        ):
            step = connectors._modbus_energy_source_step(owner, source, 9.5)

        self.assertIs(step.snapshot, result)
        load_settings.assert_called_once_with(runtime, source)
        load_client.assert_called_once_with(runtime, source, settings)
        read_value.assert_called_once_with(client, value_field)
        build.assert_called_once_with(source, 9.5, settings, {"soc": 42.5})
        self.assertEqual(runtime.requests, [1.25])
        self.assertEqual(client.timeout_seconds, 0.25)

    def test_modbus_empty_fields_complete_without_io_and_read_failure_clears_progress(
        self,
    ) -> None:
        source = EnergySourceDefinition(
            source_id="modbus",
            connector_type="modbus",
            config_path="source.ini",
        )
        runtime = _DeadlineRuntime(0.25)
        client = MagicMock(spec=ModbusClient)
        client.timeout_seconds = 1.0
        empty_result = EnergySourceSnapshot("modbus", "battery", "empty")
        with (
            patch.object(
                connectors,
                "_modbus_energy_source_settings",
                return_value=_modbus_settings(),
            ),
            patch.object(
                connectors,
                "_modbus_energy_source_client",
                return_value=client,
            ),
            patch.object(
                connectors,
                "_build_modbus_energy_source_snapshot",
                return_value=empty_result,
            ) as build,
            patch.object(connectors, "_modbus_field_value") as read,
        ):
            step = connectors._modbus_energy_source_step(runtime, source, 4.0)
        self.assertIs(step.snapshot, empty_result)
        read.assert_not_called()
        build.assert_called_once_with(source, 4.0, _modbus_settings(), {})

        value_field = ModbusEnergyFieldSettings(
            register_type="holding",
            address=10,
            data_type="uint16",
            scale=1.0,
            word_order="big",
        )
        settings = replace(_modbus_settings(), soc_field=value_field)
        connectors._runtime_cache_put(
            runtime,
            connectors._MODBUS_PROGRESS_CACHE,
            connectors._connector_progress_key(source),
            connectors.ModbusReadProgress(0, {}),
        )
        with (
            patch.object(
                connectors,
                "_modbus_energy_source_settings",
                return_value=settings,
            ),
            patch.object(
                connectors,
                "_modbus_energy_source_client",
                return_value=client,
            ),
            patch.object(
                connectors,
                "_modbus_field_value",
                side_effect=TimeoutError("offline"),
            ),
            self.assertRaisesRegex(TimeoutError, "offline"),
        ):
            connectors._modbus_energy_source_step(runtime, source, 5.0)
        self.assertNotIn(
            connectors._connector_progress_key(source),
            runtime._energy_connector_runtime_state.caches[
                connectors._MODBUS_PROGRESS_CACHE
            ],
        )

    def test_command_snapshot_wrapper_enforces_process_and_json_contracts(self) -> None:
        runtime = _DeadlineRuntime(0.4)
        owner = SimpleNamespace(service=runtime)
        source = EnergySourceDefinition(source_id="command", connector_type="command_json")
        settings = _command_settings()
        result = EnergySourceSnapshot("command", "battery", "service")
        with (
            patch.object(connectors, "_command_json_energy_source_settings", return_value=settings) as load_settings,
            patch(
                "venus_evcharger.energy.connectors.run_bounded_command",
                return_value=BoundedCommandResult(' {"soc": 50} ', ""),
            ) as run,
            patch.object(connectors, "_build_command_json_energy_source_snapshot", return_value=result) as build,
        ):
            step = connectors._command_json_energy_source_step(owner, source, 7.5)

        self.assertIs(step.snapshot, result)
        load_settings.assert_called_once_with(runtime, source)
        run.assert_called_once_with(
            ("helper", "--once"),
            timeout_seconds=0.4,
            stdout_limit=262144,
            stderr_limit=16384,
        )
        self.assertEqual(runtime.requests, [2.5])
        build.assert_called_once_with(source, 7.5, settings, {"soc": 50})

        runtime.requests.clear()
        with (
            patch.object(connectors, "_command_json_energy_source_settings", return_value=settings),
            patch(
                "venus_evcharger.energy.connectors.run_bounded_command",
                return_value=BoundedCommandResult("  ", ""),
            ),
            patch.object(connectors, "_build_command_json_energy_source_snapshot", return_value=result) as empty_build,
        ):
            connectors._command_json_energy_source_step(owner, source, 8.5)
        self.assertEqual(runtime.requests, [2.5])
        empty_build.assert_called_once_with(source, 8.5, settings, {})

        runtime.requests.clear()
        with (
            patch.object(connectors, "_command_json_energy_source_settings", return_value=settings),
            patch(
                "venus_evcharger.energy.connectors.run_bounded_command",
                return_value=BoundedCommandResult("[]", ""),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "did not return a JSON object"):
                connectors._command_json_energy_source_step(owner, source, 9.5)
        self.assertEqual(runtime.requests, [2.5])


if __name__ == "__main__":
    unittest.main()
