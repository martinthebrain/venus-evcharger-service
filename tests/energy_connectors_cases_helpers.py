# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_connectors_cases_common import *

from venus_evcharger.energy.connectors_common import _cache_map, _typed_cache_map


class _EnergyConnectorsHelperCases:
    def test_template_http_settings_and_validation_helpers_cover_cache_and_errors(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
        source = EnergySourceDefinition(source_id="battery", role="battery", connector_type="template_http", config_path="")

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            energy_connectors._template_http_energy_source_settings(runtime, source)

        settings = energy_connectors.TemplateHttpEnergySourceSettings(
            base_url="http://cached.local",
            auth_settings=energy_connectors.TemplateAuthSettings("", "", False, None, None),
            timeout_seconds=2.0,
            request_method="GET",
            request_url="http://cached.local/state",
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
        runtime._energy_template_settings_cache = {"cached.ini": settings}
        cached_source = EnergySourceDefinition(
            source_id="cached",
            role="battery",
            connector_type="template_http",
            config_path="cached.ini",
        )
        self.assertIs(energy_connectors._template_http_energy_source_settings(runtime, cached_source), settings)
        runtime._energy_template_settings_cache = {"cached.ini": settings, 1: "stale", "bad.ini": object()}
        typed_cache = _typed_cache_map(
            runtime,
            "_energy_template_settings_cache",
            energy_connectors.TemplateHttpEnergySourceSettings,
        )
        self.assertEqual(typed_cache, {"cached.ini": settings})
        self.assertEqual(runtime._energy_template_settings_cache, {"cached.ini": settings})
        runtime._energy_misc_cache = {1: "one"}
        self.assertEqual(_cache_map(runtime, "_energy_misc_cache"), {"1": "one"})

        with self.assertRaisesRegex(ValueError, "requires \\[EnergyRequest\\] Url"):
            energy_connectors._validate_template_http_energy_source_settings(
                cached_source,
                settings.__class__(**{**settings.__dict__, "request_url": ""}),
            )
        with self.assertRaisesRegex(ValueError, "requires at least one readable EnergyResponse path"):
            energy_connectors._validate_template_http_energy_source_settings(
                cached_source,
                settings.__class__(**{**settings.__dict__, "request_url": "http://cached.local/state", "soc_path": None}),
            )
        self.assertEqual(
            energy_connectors._template_source_name(
                EnergySourceDefinition(source_id="cached", role="battery", connector_type="template_http", config_path="cfg.ini"),
                settings.__class__(**{**settings.__dict__, "base_url": ""}),
            ),
            "cfg.ini",
        )

    def test_modbus_and_command_connector_helpers_cover_validation_and_fallbacks(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            energy_connectors._modbus_energy_source_settings(
                runtime,
                EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus", config_path=""),
            )

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            energy_connectors._command_json_energy_source_settings(
                runtime,
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json", config_path=""),
            )

        parser = ConfigParser()
        parser.add_section("SocRead")
        parser.set("SocRead", "RegisterType", "holding")
        parser.set("SocRead", "DataType", "uint16")
        parser.set("SocRead", "Scale", "1")
        self.assertIsNone(energy_connectors._modbus_field_settings(parser, "Missing"))
        self.assertIsNone(energy_connectors._modbus_field_settings(parser, "SocRead"))

        transport_settings = energy_connectors.ModbusTransportSettings(
            transport_kind="tcp",
            unit_id=1,
            timeout_seconds=1.0,
            host="",
            port=502,
            device="/dev/ttyUSB0",
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
        self.assertEqual(
            energy_connectors._modbus_source_name(
                EnergySourceDefinition(
                    source_id="modbus",
                    role="battery",
                    connector_type="modbus",
                    config_path="cfg.ini",
                    service_name="modbus-service",
                ),
                transport_settings,
            ),
            "modbus-service",
        )
        self.assertEqual(
            energy_connectors._modbus_source_name(
                EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus", config_path="cfg.ini"),
                transport_settings,
            ),
            "/dev/ttyUSB0",
        )
        self.assertEqual(
            energy_connectors._command_source_name(
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json", config_path="cfg.ini"),
                energy_connectors.CommandJsonEnergySourceSettings(
                    command=(),
                    timeout_seconds=1.0,
                    soc_path=None,
                    usable_capacity_wh_path=None,
                    battery_power_path=None,
                    ac_power_path=None,
                    pv_input_power_path=None,
                    grid_interaction_path=None,
                    operating_mode_path=None,
                    online_path=None,
                    confidence_path=None,
                ),
            ),
            "cfg.ini",
        )

        with self.assertRaisesRegex(ValueError, "requires at least one Modbus read section"):
            energy_connectors._validate_modbus_energy_source_settings(
                EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus"),
                energy_connectors.ModbusEnergySourceSettings(
                    transport_settings=transport_settings,
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
                ),
            )
        with self.assertRaisesRegex(ValueError, "requires \\[Command\\] Args"):
            energy_connectors._validate_command_json_energy_source_settings(
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json"),
                energy_connectors.CommandJsonEnergySourceSettings(
                    command=(),
                    timeout_seconds=1.0,
                    soc_path="soc",
                    usable_capacity_wh_path=None,
                    battery_power_path=None,
                    ac_power_path=None,
                    pv_input_power_path=None,
                    grid_interaction_path=None,
                    operating_mode_path=None,
                    online_path=None,
                    confidence_path=None,
                ),
            )
        with self.assertRaisesRegex(ValueError, "requires at least one Response path or UsableCapacityWh"):
            energy_connectors._validate_command_json_energy_source_settings(
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json"),
                energy_connectors.CommandJsonEnergySourceSettings(
                    command=("helper",),
                    timeout_seconds=1.0,
                    soc_path=None,
                    usable_capacity_wh_path=None,
                    battery_power_path=None,
                    ac_power_path=None,
                    pv_input_power_path=None,
                    grid_interaction_path=None,
                    operating_mode_path=None,
                    online_path=None,
                    confidence_path=None,
                ),
            )

        runtime._energy_modbus_settings_cache = {}
        runtime._energy_modbus_client_cache = {}
        source = EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus", config_path="cfg.ini")
        cached_settings = energy_connectors.ModbusEnergySourceSettings(
            transport_settings=transport_settings,
            soc_field=energy_connectors.ModbusEnergyFieldSettings("holding", 1, "uint16", 1.0, "big"),
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
        runtime._energy_modbus_settings_cache["cfg.ini"] = cached_settings
        self.assertIs(energy_connectors._modbus_energy_source_settings(runtime, source), cached_settings)
        with patch("venus_evcharger.energy.connectors.create_modbus_transport", return_value=_FakeModbusTransport()):
            first_client = energy_connectors._modbus_energy_source_client(runtime, source, cached_settings)
            second_client = energy_connectors._modbus_energy_source_client(runtime, source, cached_settings)
        self.assertIs(first_client, second_client)
        self.assertEqual(energy_connectors._modbus_source_name(source, cached_settings.transport_settings), "/dev/ttyUSB0")
        self.assertEqual(
            energy_connectors._modbus_source_name(
                source,
                cached_settings.transport_settings.__class__(
                    **{**cached_settings.transport_settings.__dict__, "device": "", "host": ""}
                ),
            ),
            "cfg.ini",
        )

        runtime._energy_command_settings_cache = {}
        command_source = EnergySourceDefinition(
            source_id="helper",
            role="battery",
            connector_type="command_json",
            config_path="helper.ini",
            service_name="helper-service",
        )
        cached_command_settings = energy_connectors.CommandJsonEnergySourceSettings(
            command=("helper",),
            timeout_seconds=1.0,
            soc_path="soc",
            usable_capacity_wh_path="capacity",
            battery_power_path=None,
            ac_power_path=None,
            pv_input_power_path=None,
            grid_interaction_path=None,
            operating_mode_path=None,
            online_path=None,
            confidence_path=None,
        )
        runtime._energy_command_settings_cache["helper.ini"] = cached_command_settings
        self.assertIs(
            energy_connectors._command_json_energy_source_settings(runtime, command_source),
            cached_command_settings,
        )
        self.assertEqual(
            energy_connectors._command_source_name(command_source, cached_command_settings),
            "helper-service",
        )

        command_owner = SimpleNamespace(service=runtime)
        completed = SimpleNamespace(stdout='{"soc":150.0,"capacity":-5.0}')
        with patch("venus_evcharger.energy.connectors.subprocess.run", return_value=completed):
            snapshot = energy_connectors._command_json_energy_source_snapshot(command_owner, command_source, 1.0)
        self.assertIsNone(snapshot.soc)
        self.assertIsNone(snapshot.usable_capacity_wh)

        modbus_owner = SimpleNamespace(service=runtime)
        negative_capacity_source = EnergySourceDefinition(
            source_id="modbus-negative",
            role="battery",
            connector_type="modbus",
            config_path="neg.ini",
            usable_capacity_wh=7000.0,
        )
        negative_capacity_settings = energy_connectors.ModbusEnergySourceSettings(
            transport_settings=transport_settings,
            soc_field=energy_connectors.ModbusEnergyFieldSettings("holding", 1, "uint16", 1.0, "big"),
            usable_capacity_field=energy_connectors.ModbusEnergyFieldSettings("holding", 2, "uint16", 1.0, "big"),
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
        modbus_client = SimpleNamespace(read_scalar=MagicMock(side_effect=[150.0, -2.0]))
        with (
            patch("venus_evcharger.energy.connectors._modbus_energy_source_settings", return_value=negative_capacity_settings),
            patch("venus_evcharger.energy.connectors._modbus_energy_source_client", return_value=modbus_client),
        ):
            modbus_snapshot = energy_connectors._modbus_energy_source_snapshot(modbus_owner, negative_capacity_source, 2.0)
        self.assertIsNone(modbus_snapshot.soc)
        self.assertIsNone(modbus_snapshot.usable_capacity_wh)

        fallback_capacity_source = EnergySourceDefinition(
            source_id="modbus-fallback",
            role="battery",
            connector_type="modbus",
            config_path="fallback.ini",
            usable_capacity_wh=6400.0,
        )
        fallback_capacity_settings = energy_connectors.ModbusEnergySourceSettings(
            transport_settings=transport_settings,
            soc_field=None,
            usable_capacity_field=energy_connectors.ModbusEnergyFieldSettings("holding", 2, "uint16", 1.0, "big"),
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
        with (
            patch("venus_evcharger.energy.connectors._modbus_energy_source_settings", return_value=fallback_capacity_settings),
            patch("venus_evcharger.energy.connectors._modbus_energy_source_client", return_value=SimpleNamespace()),
            patch(
                "venus_evcharger.energy.connectors._modbus_field_value",
                side_effect=[None, None, None, None, None, None, None, None, None],
            ),
        ):
            fallback_snapshot = energy_connectors._modbus_energy_source_snapshot(modbus_owner, fallback_capacity_source, 2.5)
        self.assertEqual(fallback_snapshot.usable_capacity_wh, 6400.0)

        positive_capacity_settings = cached_command_settings.__class__(**{**cached_command_settings.__dict__, "usable_capacity_wh_path": "capacity"})
        positive_capacity_source = EnergySourceDefinition(
            source_id="helper-positive",
            role="battery",
            connector_type="command_json",
            config_path="helper-positive.ini",
            usable_capacity_wh=4000.0,
        )
        with (
            patch("venus_evcharger.energy.connectors._command_json_energy_source_settings", return_value=positive_capacity_settings),
            patch(
                "venus_evcharger.energy.connectors.subprocess.run",
                return_value=SimpleNamespace(stdout='{\"soc\":50.0,\"capacity\":6000.0}'),
            ),
        ):
            positive_snapshot = energy_connectors._command_json_energy_source_snapshot(command_owner, positive_capacity_source, 3.0)
        self.assertEqual(positive_snapshot.usable_capacity_wh, 6000.0)

    def test_connector_scalar_helpers_cover_bool_and_path_parsing(self) -> None:
        class _BoolClient:
            @staticmethod
            def read_scalar(*_args: object) -> bool:
                return True

        field = energy_connectors.ModbusEnergyFieldSettings(
            register_type="holding",
            address=1,
            data_type="uint16",
            scale=2.0,
            word_order="big",
        )
        self.assertEqual(energy_connectors._modbus_field_value(_BoolClient(), field), 2.0)
        self.assertEqual(energy_connectors._normalized_connector_type("template_http_energy"), "template_http")
        self.assertEqual(energy_connectors._normalized_connector_type(""), "dbus")
        self.assertEqual(energy_connectors._command_args({"Args": ""}), ())
        self.assertEqual(energy_connectors._command_args({"Args": "python3 helper.py --once"}), ("python3", "helper.py", "--once"))
        self.assertTrue(energy_connectors._optional_bool_path({"value": "enabled"}, "value"))
        self.assertFalse(energy_connectors._optional_bool_path({"value": "disabled"}, "value"))
        self.assertTrue(energy_connectors._optional_bool_path({"value": 1}, "value"))
        self.assertTrue(energy_connectors._optional_bool_path({"value": "1"}, "value"))
        self.assertFalse(energy_connectors._optional_bool_path({"value": None}, "value"))
        self.assertEqual(energy_connectors._optional_confidence_path({"value": 5.0}, "value"), 1.0)
        self.assertEqual(energy_connectors._optional_confidence_path({"value": -1.0}, "value"), 0.0)
