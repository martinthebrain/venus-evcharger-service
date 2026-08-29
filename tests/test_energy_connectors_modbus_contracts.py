# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the Modbus energy connector."""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.energy import connectors_modbus as modbus
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot


def _transport(**overrides: object) -> ModbusTransportSettings:
    baseline = ModbusTransportSettings(
        transport_kind="tcp",
        unit_id=7,
        timeout_seconds=1.5,
        host="192.0.2.10",
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
    return replace(baseline, **overrides)


def _field(address: int) -> modbus.ModbusEnergyFieldSettings:
    return modbus.ModbusEnergyFieldSettings("holding", address, "uint16", 1.0, "big")


def _settings(**overrides: object) -> modbus.ModbusEnergySourceSettings:
    baseline = modbus.ModbusEnergySourceSettings(
        transport_settings=_transport(),
        soc_field=_field(1),
        usable_capacity_field=_field(2),
        battery_power_field=_field(3),
        charge_limit_power_field=_field(4),
        discharge_limit_power_field=_field(5),
        ac_power_field=_field(6),
        pv_input_power_field=_field(7),
        grid_interaction_field=_field(8),
        operating_mode_field=_field(9),
        operating_mode_map={"2": "support"},
        ac_power_scope_key="ac-{source_id}",
        pv_input_power_scope_key="pv-{host}",
        grid_interaction_scope_key="grid-{unit_id}",
    )
    return replace(baseline, **overrides)


class EnergyConnectorsModbusContractTests(unittest.TestCase):
    def test_field_parser_and_auxiliary_sections_are_exact(self) -> None:
        parser = ConfigParser()
        parser.read_dict(
            {
                "CustomRead": {
                    "RegisterType": " INPUT ",
                    "Address": " 42 ",
                    "DataType": " INT32 ",
                    "Scale": " -0.5 ",
                    "WordOrder": " LITTLE ",
                },
                "DefaultsRead": {"Address": "7", "Scale": "  "},
                "EmptyRead": {"Address": "  "},
                "OperatingModeMap": {" 1 ": " idle ", "2": "support", "empty": "  "},
                "Aggregation": {
                    "AcPowerScopeKey": " ac-{source_id} ",
                    "PvInputPowerScopeKey": "pv-{host}",
                },
            }
        )
        self.assertEqual(
            modbus._modbus_field_settings(parser, "CustomRead"),
            modbus.ModbusEnergyFieldSettings("input", 42, "int32", -0.5, "little"),
        )
        self.assertEqual(
            modbus._modbus_field_settings(parser, "DefaultsRead"),
            modbus.ModbusEnergyFieldSettings("holding", 7, "uint16", 1.0, "big"),
        )
        self.assertIsNone(modbus._modbus_field_settings(parser, "EmptyRead"))
        self.assertIsNone(modbus._modbus_field_settings(parser, "MissingRead"))
        self.assertEqual(modbus._modbus_text_map(parser, "OperatingModeMap"), {"1": "idle", "2": "support"})
        self.assertEqual(modbus._modbus_text_map(parser, "MissingMap"), {})
        self.assertEqual(modbus._modbus_aggregation_setting(parser, "AcPowerScopeKey"), "ac-{source_id}")
        self.assertEqual(modbus._modbus_aggregation_setting(parser, "PvInputPowerScopeKey"), "pv-{host}")
        self.assertEqual(modbus._modbus_aggregation_setting(parser, "Missing"), "")
        self.assertEqual(modbus._modbus_aggregation_setting(ConfigParser(), "Missing"), "")

    def test_non_finite_scale_and_values_fail_closed(self) -> None:
        for scale in ("nan", "inf", "-inf"):
            parser = ConfigParser()
            parser.read_dict({"Read": {"Address": "1", "Scale": scale}})
            with self.subTest(scale=scale), self.assertRaises(ValueError) as raised:
                modbus._modbus_field_settings(parser, "Read")
            self.assertEqual(
                str(raised.exception),
                "Modbus field scale must be finite",
            )

        field = _field(1)
        for raw_value in (float("nan"), float("inf"), float("-inf")):
            client = MagicMock()
            client.read_scalar.return_value = raw_value
            with self.subTest(raw_value=raw_value), self.assertRaises(
                ValueError
            ) as raised:
                modbus._modbus_field_value(client, field)
            self.assertEqual(
                str(raised.exception),
                "Modbus energy-source field returned a non-finite value",
            )

        parser = ConfigParser()
        parser.read_dict({"Read": {"Address": "1", "Scale": "0"}})
        self.assertEqual(modbus._modbus_field_scale(parser["Read"]), 0.0)

    def test_settings_loader_assembles_every_role_and_caches(self) -> None:
        parser = ConfigParser()
        fields = tuple(_field(index) for index in range(1, 10))
        transport = _transport()
        source = EnergySourceDefinition(source_id="source", config_path=" config.ini ")
        runtime = SimpleNamespace()
        with (
            patch.object(modbus, "load_template_config", return_value=parser) as load_config,
            patch.object(modbus, "load_modbus_transport_settings", return_value=transport) as load_transport,
            patch.object(modbus, "_modbus_field_settings", side_effect=fields) as load_field,
            patch.object(modbus, "_modbus_text_map", return_value={"2": "support"}) as load_map,
            patch.object(modbus, "_modbus_aggregation_setting", side_effect=("ac", "pv", "grid")) as load_scope,
            patch.object(modbus, "_validate_modbus_energy_source_settings") as validate,
        ):
            loaded = modbus._modbus_energy_source_settings(runtime, source)
            cached = modbus._modbus_energy_source_settings(runtime, source)

        self.assertIs(cached, loaded)
        load_config.assert_called_once_with("config.ini")
        load_transport.assert_called_once_with(parser, runtime)
        self.assertEqual(
            load_field.call_args_list,
            [
                call(parser, "SocRead"),
                call(parser, "UsableCapacityRead"),
                call(parser, "BatteryPowerRead"),
                call(parser, "ChargeLimitPowerRead"),
                call(parser, "DischargeLimitPowerRead"),
                call(parser, "AcPowerRead"),
                call(parser, "PvInputPowerRead"),
                call(parser, "GridInteractionRead"),
                call(parser, "OperatingModeRead"),
            ],
        )
        load_map.assert_called_once_with(parser, "OperatingModeMap")
        self.assertEqual(
            load_scope.call_args_list,
            [
                call(parser, "AcPowerScopeKey"),
                call(parser, "PvInputPowerScopeKey"),
                call(parser, "GridInteractionScopeKey"),
            ],
        )
        self.assertEqual(
            loaded,
            modbus.ModbusEnergySourceSettings(
                transport,
                *fields,
                {"2": "support"},
                "ac",
                "pv",
                "grid",
            ),
        )
        validate.assert_called_once_with(source, loaded)
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches,
            {"modbus.settings": {"config.ini": loaded}},
        )

        missing = EnergySourceDefinition(source_id="missing", config_path=" ")
        with self.assertRaises(ValueError) as raised:
            modbus._modbus_energy_source_settings(SimpleNamespace(), missing)
        self.assertEqual(str(raised.exception), "Energy source 'missing' requires ConfigPath for modbus connector")

    def test_client_cache_source_name_and_field_values_are_exact(self) -> None:
        runtime = SimpleNamespace()
        cached_client = MagicMock(spec=ModbusClient)
        modbus._store_modbus_client(runtime, "valid", cached_client)
        self.assertIs(modbus._cached_modbus_client(runtime, "valid"), cached_client)
        runtime._energy_connector_runtime_state.caches["modbus.clients"]["invalid"] = "value"
        self.assertIsNone(modbus._cached_modbus_client(runtime, "invalid"))
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches,
            {"modbus.clients": {"valid": cached_client}},
        )

        transport = _transport()
        self.assertEqual(
            modbus._modbus_source_name(EnergySourceDefinition(source_id="id", service_name="service"), transport),
            "service",
        )
        self.assertEqual(modbus._modbus_source_name(EnergySourceDefinition(source_id="id"), transport), "192.0.2.10")
        serial = _transport(host=None, device="/dev/ttyUSB0")
        self.assertEqual(modbus._modbus_source_name(EnergySourceDefinition(source_id="id"), serial), "/dev/ttyUSB0")
        empty = _transport(host=None, device=None)
        self.assertEqual(
            modbus._modbus_source_name(EnergySourceDefinition(source_id="id", config_path="cfg"), empty), "cfg"
        )
        self.assertEqual(modbus._modbus_source_name(EnergySourceDefinition(source_id="id"), empty), "id")

        client = MagicMock(spec=ModbusClient)
        client.read_scalar.side_effect = (True, False, 2.5)
        field = modbus.ModbusEnergyFieldSettings("input", 42, "float32", -2.0, "little")
        self.assertEqual(modbus._modbus_field_value(client, field), -2.0)
        self.assertEqual(modbus._modbus_field_value(client, field), -0.0)
        self.assertEqual(modbus._modbus_field_value(client, field), -5.0)
        self.assertIsNone(modbus._modbus_field_value(client, None))
        self.assertEqual(
            client.read_scalar.call_args_list,
            [
                call("input", 42, "float32", "little"),
                call("input", 42, "float32", "little"),
                call("input", 42, "float32", "little"),
            ],
        )

    def test_snapshot_builder_maps_every_field_and_boundary(self) -> None:
        source = EnergySourceDefinition(
            source_id="source",
            role="hybrid-inverter",
            physical_id="battery-rack",
            physical_priority=17,
            usable_capacity_wh=5000.0,
        )
        settings = _settings()
        values: dict[str, float | str | None] = {
            "soc": 61.0,
            "usable_capacity": 8400.0,
            "battery_power": -1200.0,
            "charge_limit_power": 3000.0,
            "discharge_limit_power": 2500.0,
            "ac_power": 2300.0,
            "pv_input_power": 1700.0,
            "grid_interaction": -300.0,
            "operating_mode": "support",
        }
        self.assertEqual(
            modbus._build_modbus_energy_source_snapshot(
                source,
                123.5,
                settings,
                values,
            ),
            EnergySourceSnapshot(
                source_id="source",
                role="hybrid-inverter",
                service_name="192.0.2.10",
                physical_id="battery-rack",
                physical_priority=17,
                battery_chemistry="lfp",
                soc=61.0,
                usable_capacity_wh=8400.0,
                net_battery_power_w=-1200.0,
                charge_limit_power_w=3000.0,
                discharge_limit_power_w=2500.0,
                ac_power_w=2300.0,
                pv_input_power_w=1700.0,
                grid_interaction_w=-300.0,
                ac_power_scope_key="ac-source",
                pv_input_power_scope_key="pv-192.0.2.10",
                grid_interaction_scope_key="grid-7",
                operating_mode="support",
                online=True,
                confidence=1.0,
                captured_at=123.5,
            ),
        )

        for soc, capacity, expected_soc, expected_capacity in (
            (-0.1, None, None, 5000.0),
            (0.0, 0.0, 0.0, None),
            (100.0, -1.0, 100.0, None),
            (100.1, 0.5, None, 0.5),
        ):
            with self.subTest(soc=soc, capacity=capacity):
                snapshot = modbus._build_modbus_energy_source_snapshot(
                    source,
                    1.0,
                    settings,
                    {"soc": soc, "usable_capacity": capacity},
                )
                self.assertEqual(snapshot.soc, expected_soc)
                self.assertEqual(snapshot.usable_capacity_wh, expected_capacity)

    def test_read_field_order_and_operating_mode_conversion_are_exact(self) -> None:
        settings = _settings()
        self.assertEqual(
            modbus._modbus_read_fields(settings),
            (
                ("soc", settings.soc_field),
                ("usable_capacity", settings.usable_capacity_field),
                ("battery_power", settings.battery_power_field),
                ("charge_limit_power", settings.charge_limit_power_field),
                ("discharge_limit_power", settings.discharge_limit_power_field),
                ("ac_power", settings.ac_power_field),
                ("pv_input_power", settings.pv_input_power_field),
                ("grid_interaction", settings.grid_interaction_field),
                ("operating_mode", settings.operating_mode_field),
            ),
        )
        self.assertEqual(modbus._modbus_progress_value("soc", 2.0, settings), 2.0)
        self.assertEqual(
            modbus._modbus_progress_value("operating_mode", 2.0, settings),
            "support",
        )
        self.assertEqual(
            modbus._modbus_progress_value("operating_mode", -0.0, settings),
            "0",
        )
        self.assertEqual(
            modbus._modbus_progress_value("operating_mode", 2.25, settings),
            "2.25",
        )

    def test_progress_value_extractors_reject_cross_typed_values(self) -> None:
        values: dict[str, float | str | None] = {
            "numeric": 12.5,
            "boolean": True,
            "text": "support",
            "missing": None,
        }
        self.assertEqual(modbus._numeric_progress_value(values, "numeric"), 12.5)
        self.assertEqual(modbus._numeric_progress_value(values, "boolean"), 1.0)
        self.assertIsNone(modbus._numeric_progress_value(values, "text"))
        self.assertIsNone(modbus._numeric_progress_value(values, "absent"))
        self.assertEqual(modbus._text_progress_value(values, "text"), "support")
        self.assertEqual(modbus._text_progress_value(values, "numeric"), "")
        self.assertEqual(modbus._text_progress_value(values, "missing"), "")
        self.assertEqual(modbus._text_progress_value(values, "absent"), "")

    def test_validation_accepts_each_read_role_and_capacity_fallback(self) -> None:
        field_names = (
            "soc_field",
            "usable_capacity_field",
            "battery_power_field",
            "charge_limit_power_field",
            "discharge_limit_power_field",
            "ac_power_field",
            "pv_input_power_field",
            "grid_interaction_field",
            "operating_mode_field",
        )
        empty_values = {name: None for name in field_names}
        for name in field_names:
            with self.subTest(field=name):
                settings = _settings(**{**empty_values, name: _field(1)})
                self.assertIs(modbus._modbus_has_any_read_field(settings), True)
                modbus._validate_modbus_energy_source_settings(EnergySourceDefinition(source_id="source"), settings)

        empty = _settings(**empty_values)
        self.assertIs(modbus._modbus_has_any_read_field(empty), False)
        with self.assertRaises(ValueError) as raised:
            modbus._validate_modbus_energy_source_settings(EnergySourceDefinition(source_id="source"), empty)
        self.assertEqual(
            str(raised.exception),
            "Energy source 'source' requires at least one Modbus read section or UsableCapacityWh",
        )
        modbus._validate_modbus_energy_source_settings(
            EnergySourceDefinition(source_id="source", usable_capacity_wh=1.0),
            empty,
        )

    def test_scope_rendering_preserves_unknown_and_malformed_templates(self) -> None:
        source = EnergySourceDefinition(source_id="source")
        transport = _transport(device="/dev/ttyUSB0")
        self.assertEqual(modbus._render_scope_key(source, transport, ""), "")
        self.assertEqual(modbus._render_scope_key(source, transport, "  "), "")
        self.assertEqual(
            modbus._render_scope_key(source, transport, " {source_id}|{host}|{port}|{unit_id}|{device} "),
            "source|192.0.2.10|502|7|/dev/ttyUSB0",
        )
        self.assertEqual(modbus._render_scope_key(source, transport, "{source_id}-{missing}"), "source-{missing}")
        self.assertEqual(modbus._render_scope_key(source, transport, "{"), "{")
        self.assertEqual(modbus._ScopeKeyFormatter({})["unknown"], "{unknown}")


if __name__ == "__main__":
    unittest.main()
