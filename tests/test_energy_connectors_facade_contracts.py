# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for the energy connector registry facade."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.energy import connectors
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot


class EnergyConnectorsFacadeContractTests(unittest.TestCase):
    def test_registry_dispatch_forwards_owner_source_and_timestamp_exactly(self) -> None:
        owner = object()
        now = 123.5
        result = EnergySourceSnapshot(source_id="result", role="battery", service_name="service")
        connector_cases = (
            ("template_http", "_template_http_energy_source_snapshot"),
            ("opendtu_http", "_opendtu_energy_source_snapshot"),
            ("modbus", "_modbus_energy_source_snapshot"),
            ("command_json", "_command_json_energy_source_snapshot"),
        )
        for connector_type, function_name in connector_cases:
            source = EnergySourceDefinition(source_id=connector_type, connector_type=connector_type)
            with self.subTest(connector_type=connector_type), patch.object(
                connectors,
                function_name,
                return_value=result,
            ) as reader:
                self.assertIs(connectors.read_energy_source_snapshot(owner, source, now), result)
                reader.assert_called_once_with(owner, source, now)

        for source, expected in (
            (EnergySourceDefinition(source_id="missing"), "<empty>"),
            (EnergySourceDefinition(source_id="retired", connector_type="dbus"), "dbus"),
        ):
            with self.subTest(connector=expected), self.assertRaisesRegex(
                ValueError,
                f"Unsupported energy-source connector: {expected}",
            ):
                connectors.read_energy_source_snapshot(owner, source, now)

    def test_modbus_client_cache_is_keyed_by_config_path_and_uses_transport_settings(self) -> None:
        runtime = SimpleNamespace()
        transport_settings = SimpleNamespace(unit_id=7, timeout_seconds=1.25)
        settings = SimpleNamespace(transport_settings=transport_settings)
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
        self.assertEqual(set(runtime._energy_modbus_client_cache), {"a.ini", "b.ini"})
        self.assertIs(client_a.transport, transport_a)
        self.assertEqual(client_a.unit_id, 7)
        self.assertEqual(client_a.timeout_seconds, 1.25)

    def test_modbus_field_text_preserves_numeric_text_and_optional_mapping(self) -> None:
        client = object()
        field = object()
        with patch.object(connectors, "_modbus_field_value", return_value=None) as read_none:
            self.assertEqual(connectors._modbus_field_text(client, field), "")
        read_none.assert_called_once_with(client, field)
        with patch.object(connectors, "_modbus_field_value", return_value=12.5) as read_decimal:
            self.assertEqual(connectors._modbus_field_text(client, field), "12.5")
            self.assertEqual(connectors._modbus_field_text(client, field, {"other": "value"}), "12.5")
        self.assertEqual(read_decimal.call_args_list, [call(client, field), call(client, field)])
        with patch.object(connectors, "_modbus_field_value", return_value=12.0) as read_integer:
            self.assertEqual(connectors._modbus_field_text(client, field), "12")
            self.assertEqual(connectors._modbus_field_text(client, field, {"12": "running"}), "running")
        self.assertEqual(read_integer.call_args_list, [call(client, field), call(client, field)])

    def test_modbus_snapshot_wrapper_uses_service_runtime_and_exact_builder_arguments(self) -> None:
        runtime = object()
        owner = SimpleNamespace(service=runtime)
        source = EnergySourceDefinition(source_id="modbus", connector_type="modbus")
        settings = object()
        client = object()
        result = object()
        with (
            patch.object(connectors, "_modbus_energy_source_settings", return_value=settings) as load_settings,
            patch.object(connectors, "_modbus_energy_source_client", return_value=client) as load_client,
            patch.object(connectors, "_build_modbus_energy_source_snapshot", return_value=result) as build,
        ):
            self.assertIs(connectors._modbus_energy_source_snapshot(owner, source, 9.5), result)

        load_settings.assert_called_once_with(runtime, source)
        load_client.assert_called_once_with(runtime, source, settings)
        build.assert_called_once_with(
            source,
            9.5,
            settings,
            client,
            connectors._modbus_field_value,
            connectors._modbus_field_text,
        )

    def test_command_snapshot_wrapper_enforces_process_and_json_contracts(self) -> None:
        runtime = object()
        owner = SimpleNamespace(service=runtime)
        source = EnergySourceDefinition(source_id="command", connector_type="command_json")
        settings = SimpleNamespace(command=("helper", "--once"), timeout_seconds=2.5)
        result = object()
        with (
            patch.object(connectors, "_command_json_energy_source_settings", return_value=settings) as load_settings,
            patch.object(
                connectors.subprocess,
                "run",
                return_value=SimpleNamespace(stdout=' {"soc": 50} '),
            ) as run,
            patch.object(connectors, "_build_command_json_energy_source_snapshot", return_value=result) as build,
        ):
            self.assertIs(connectors._command_json_energy_source_snapshot(owner, source, 7.5), result)

        load_settings.assert_called_once_with(runtime, source)
        run.assert_called_once_with(
            ("helper", "--once"),
            check=True,
            capture_output=True,
            text=True,
            timeout=2.5,
        )
        build.assert_called_once_with(source, 7.5, settings, {"soc": 50})

        with (
            patch.object(connectors, "_command_json_energy_source_settings", return_value=settings),
            patch.object(connectors.subprocess, "run", return_value=SimpleNamespace(stdout="  ")),
            patch.object(connectors, "_build_command_json_energy_source_snapshot", return_value=result) as empty_build,
        ):
            connectors._command_json_energy_source_snapshot(owner, source, 8.5)
        empty_build.assert_called_once_with(source, 8.5, settings, {})

        with (
            patch.object(connectors, "_command_json_energy_source_settings", return_value=settings),
            patch.object(connectors.subprocess, "run", return_value=SimpleNamespace(stdout="[]")),
        ):
            with self.assertRaisesRegex(ValueError, "did not return a JSON object"):
                connectors._command_json_energy_source_snapshot(owner, source, 9.5)


if __name__ == "__main__":
    unittest.main()
