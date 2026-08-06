# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Callable

from tests.test_topology_config_support import *  # noqa: F401,F403


class _TopologyConfigTestsPart4:
    class _FakeSection:
        def __init__(self, name: str, values: dict[str, object]) -> None:
            self.name = name
            self._values = values

        def get(self, key: str, fallback: object | None = None) -> object | None:
            return self._values.get(key, fallback)

    class _FakeConfig:
        def __init__(self, sections: dict[str, "_TopologyConfigTestsPart4._FakeSection"]) -> None:
            self._sections = sections

        def has_section(self, name: str) -> bool:
            return name in self._sections

        def __getitem__(self, key: str) -> "_TopologyConfigTestsPart4._FakeSection":
            return self._sections[key]

    def _assert_topology_error(self, expected: str, callback: Callable[..., object], *args: object) -> None:
        with self.assertRaises(TopologyConfigError) as caught:
            callback(*args)
        self.assertEqual(str(caught.exception), expected)

    def test_literal_choice_contracts_accept_normalized_values_and_reject_invalid_values(self) -> None:
        self.assertEqual(_topology_type(" SIMPLE_RELAY "), "simple_relay")
        self.assertEqual(_topology_type("NATIVE_DEVICE"), "native_device")
        self.assertEqual(_topology_type("hybrid_topology"), "hybrid_topology")
        self.assertEqual(_topology_type("custom_topology"), "custom_topology")
        self.assertEqual(_policy_mode("AUTO"), "auto")
        self.assertEqual(_policy_mode("scheduled"), "scheduled")
        self.assertEqual(_actuator_type("shelly_switch"), "shelly_switch")
        self.assertEqual(_actuator_type("custom"), "custom")
        self.assertEqual(_measurement_type("learned_reference"), "learned_reference")
        self.assertEqual(_charger_type("template_charger"), "template_charger")

        invalid_cases = (
            (
                _topology_type,
                "Topology.Type",
                "bad-topology",
                "invalid Topology.Type: 'bad-topology' "
                "(expected one of: custom_topology, hybrid_topology, native_device, simple_relay)",
            ),
            (
                _policy_mode,
                "Policy.Mode",
                "bad-policy",
                "invalid Policy.Mode: 'bad-policy' (expected one of: auto, manual, scheduled)",
            ),
            (
                _actuator_type,
                "Actuator.Type",
                "bad-actuator",
                "invalid Actuator.Type: 'bad-actuator' "
                "(expected one of: cerbo_gx_relay_switch, custom, shelly_contactor_switch, "
                "shelly_switch, switch_group, tasmota_contactor_switch, tasmota_switch, "
                "template_switch, tuya_contactor_switch, tuya_switch)",
            ),
            (
                _measurement_type,
                "Measurement.Type",
                "bad-meter",
                "invalid Measurement.Type: 'bad-meter' "
                "(expected one of: actuator_native, charger_native, external_meter, "
                "fixed_reference, learned_reference, none)",
            ),
            (
                _charger_type,
                "Charger.Type",
                "bad-charger",
                "invalid Charger.Type: 'bad-charger' "
                "(expected one of: custom, goe_charger, modbus_charger, simpleevse_charger, "
                "smartevse_charger, template_charger)",
            ),
        )
        for parser, label, value, expected in invalid_cases:
            with self.subTest(label=label):
                self._assert_topology_error(expected, parser, value)

    def test_bool_and_legacy_policy_contracts_cover_truthy_and_fallback_values(self) -> None:
        for value in ("1", " true ", "YES", "on"):
            with self.subTest(value=value):
                self.assertTrue(_as_bool(value))
        for value in ("", "0", "false", "no", "off", None, "enabled"):
            with self.subTest(value=value):
                self.assertFalse(_as_bool(value))

        self.assertEqual(_legacy_policy_mode("1"), "auto")
        self.assertEqual(_legacy_policy_mode("2"), "scheduled")
        for value in ("0", "", "manual", None):
            with self.subTest(value=value):
                self.assertEqual(_legacy_policy_mode(value), "manual")

    def test_optional_role_sections_parse_paths_flags_and_missing_type_errors(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=custom_topology

[Actuator]
Type=shelly_switch
ConfigPath= /data/etc/switch.ini

[Measurement]
Type=learned_reference
ReferenceWatts= 1840.5
AllowAutoEstimate= yes

[Charger]
Type=template_charger
ConfigPath= /data/etc/charger.ini

[Policy]
Phase= L2
"""
        )

        parsed = parse_topology_config(parser)

        self.assertEqual(parsed.actuator.type, "shelly_switch")
        self.assertEqual(parsed.actuator.config_path, "/data/etc/switch.ini")
        self.assertEqual(parsed.measurement.type, "learned_reference")
        self.assertEqual(parsed.measurement.reference_watts, 1840.5)
        self.assertTrue(parsed.measurement.allow_auto_estimate)
        self.assertEqual(parsed.charger.type, "template_charger")
        self.assertEqual(parsed.charger.config_path, "/data/etc/charger.ini")
        self.assertEqual(parsed.policy.mode, "manual")
        self.assertEqual(parsed.policy.phase, "L2")

        for section in ("Actuator", "Measurement", "Charger"):
            with self.subTest(section=section):
                missing_type = configparser.ConfigParser()
                missing_type.read_string(f"[Topology]\nType=custom_topology\n\n[{section}]\n")
                with self.assertRaisesRegex(TopologyConfigError, rf"missing required key {section}\.Type"):
                    parse_topology_config(missing_type)

    def test_role_section_helpers_use_explicit_schema_field_names(self) -> None:
        fake = self._FakeConfig(
            {
                "Actuator": self._FakeSection(
                    "Actuator",
                    {"Type": "template_switch", "ConfigPath": "/data/etc/switch.ini"},
                ),
                "Measurement": self._FakeSection(
                    "Measurement",
                    {
                        "Type": "fixed_reference",
                        "ConfigPath": "/data/etc/meter.ini",
                        "ReferenceWatts": "1234.5",
                        "AllowAutoEstimate": "1",
                    },
                ),
                "Charger": self._FakeSection(
                    "Charger",
                    {"Type": "goe_charger", "ConfigPath": "/data/etc/charger.ini"},
                ),
                "Policy": self._FakeSection("Policy", {"Mode": "scheduled", "Phase": "L3"}),
            },
        )

        actuator = _optional_actuator(fake)
        self.assertEqual(actuator.type, "template_switch")
        self.assertEqual(actuator.config_path, "/data/etc/switch.ini")
        measurement = _optional_measurement(fake)
        self.assertEqual(measurement.type, "fixed_reference")
        self.assertEqual(measurement.config_path, "/data/etc/meter.ini")
        self.assertEqual(measurement.reference_watts, 1234.5)
        self.assertTrue(measurement.allow_auto_estimate)
        charger = _optional_charger(fake)
        self.assertEqual(charger.type, "goe_charger")
        self.assertEqual(charger.config_path, "/data/etc/charger.ini")
        policy = _policy(fake)
        self.assertEqual(policy.mode, "scheduled")
        self.assertEqual(policy.phase, "L3")

    def test_role_section_helpers_reject_lowercase_required_schema_keys(self) -> None:
        self._assert_topology_error(
            "missing required key Actuator.Type",
            _optional_actuator,
            self._FakeConfig({"Actuator": self._FakeSection("Actuator", {"type": "template_switch"})}),
        )
        self._assert_topology_error(
            "missing required key Measurement.Type",
            _optional_measurement,
            self._FakeConfig({"Measurement": self._FakeSection("Measurement", {"type": "fixed_reference"})}),
        )
        self._assert_topology_error(
            "missing required key Charger.Type",
            _optional_charger,
            self._FakeConfig({"Charger": self._FakeSection("Charger", {"type": "goe_charger"})}),
        )
        self.assertEqual(
            _policy(self._FakeConfig({"Policy": self._FakeSection("Policy", {"mode": "scheduled", "phase": "L3"})})),
            PolicyConfig(),
        )
        self._assert_topology_error(
            "missing required key Section.Required",
            _required_value,
            self._FakeSection("Section", {"required": "value"}),
            "Required",
        )

    def test_topology_validation_contracts_accept_required_role_combinations(self) -> None:
        cases = {
            "simple_relay": """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=actuator_native
""",
            "native_device": """
[Topology]
Type=native_device

[Charger]
Type=goe_charger

[Measurement]
Type=charger_native
""",
            "hybrid_topology": """
[Topology]
Type=hybrid_topology

[Actuator]
Type=template_switch

[Measurement]
Type=charger_native

[Charger]
Type=goe_charger
""",
            "custom_topology": """
[Topology]
Type=custom_topology
""",
        }
        for topology_type, text in cases.items():
            with self.subTest(topology_type=topology_type):
                parser = configparser.ConfigParser()
                parser.read_string(text)
                self.assertEqual(parse_topology_config(parser).topology.type, topology_type)

    def test_topology_validation_contracts_reject_each_invalid_role_combination_exactly(self) -> None:
        invalid_cases = (
            (
                EvChargerTopologyConfig(topology=TopologyConfig(type="simple_relay")),
                "simple_relay requires an actuator",
            ),
            (
                EvChargerTopologyConfig(topology=TopologyConfig(type="native_device")),
                "native_device requires a charger",
            ),
            (
                EvChargerTopologyConfig(topology=TopologyConfig(type="hybrid_topology")),
                "hybrid_topology requires both charger and actuator",
            ),
            (
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="hybrid_topology"),
                    actuator=ActuatorConfig(type="template_switch"),
                ),
                "hybrid_topology requires both charger and actuator",
            ),
            (
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="external_meter"),
                ),
                "external_meter requires Measurement.ConfigPath",
            ),
            (
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="fixed_reference"),
                ),
                "fixed_reference requires Measurement.ReferenceWatts",
            ),
            (
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="charger_native"),
                ),
                "charger_native measurement requires a charger",
            ),
            (
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="actuator_native"),
                ),
                "actuator_native measurement requires an actuator",
            ),
            (
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="none"),
                    policy=PolicyConfig(mode="auto"),
                ),
                "auto policy requires a non-empty measurement mode",
            ),
        )
        for topology, message in invalid_cases:
            with self.subTest(message=message):
                self._assert_topology_error(message, validate_topology_config, topology)

    def test_legacy_runtime_values_normalize_all_backend_fields(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host= 192.0.2.76
Mode= 2
Phase= L3

[Backends]
MeterType= TEMPLATE_METER
SwitchType= TEMPLATE_SWITCH
ChargerType= GOE_CHARGER
MeterConfigPath= /data/etc/meter.ini
SwitchConfigPath= /data/etc/switch.ini
ChargerConfigPath= /data/etc/charger.ini
"""
        )

        runtime = _legacy_runtime_values(parser)

        self.assertEqual(runtime.host, "192.0.2.76")
        self.assertEqual(runtime.meter_type, "template_meter")
        self.assertEqual(runtime.switch_type, "template_switch")
        self.assertEqual(runtime.charger_type_raw, "goe_charger")
        self.assertEqual(runtime.meter_path, "/data/etc/meter.ini")
        self.assertEqual(runtime.switch_path, "/data/etc/switch.ini")
        self.assertEqual(runtime.charger_path, "/data/etc/charger.ini")

        default_only = configparser.ConfigParser()
        default_only.read_string(
            """
[DEFAULT]
Host= direct.local
MeterType= TEMPLATE_METER
SwitchType= SHELLY_COMBINED
ChargerType= TEMPLATE_CHARGER
MeterConfigPath= /default/meter.ini
SwitchConfigPath= /default/switch.ini
ChargerConfigPath= /default/charger.ini
"""
        )
        default_runtime = _legacy_runtime_values(default_only)
        self.assertEqual(default_runtime.host, "direct.local")
        self.assertEqual(default_runtime.meter_type, "template_meter")
        self.assertEqual(default_runtime.switch_type, "shelly_combined")
        self.assertEqual(default_runtime.charger_type_raw, "template_charger")
        self.assertEqual(default_runtime.meter_path, "/default/meter.ini")
        self.assertEqual(default_runtime.switch_path, "/default/switch.ini")
        self.assertEqual(default_runtime.charger_path, "/default/charger.ini")

    def test_legacy_role_helpers_keep_alias_and_none_semantics_explicit(self) -> None:
        self.assertEqual(_legacy_switch_type("", "192.0.2.76"), "shelly_contactor_switch")
        self.assertEqual(_legacy_switch_type("", ""), "")
        self.assertEqual(_legacy_switch_alias("shelly_combined", "192.0.2.76"), "shelly_contactor_switch")
        self.assertIsNone(_legacy_switch_alias("shelly_combined", ""))
        self.assertTrue(_known_legacy_switch_type("template_switch"))
        self.assertFalse(_known_legacy_switch_type("custom"))
        self.assertEqual(_legacy_switch_actuator_type("unknown", ""), "custom")
        self.assertEqual(_legacy_switch_actuator_type("", "192.0.2.76"), "shelly_contactor_switch")
        self.assertIsNone(_legacy_actuator_config("none", None, ""))
        self.assertEqual(_legacy_actuator_config("none", None, "192.0.2.76").type, "custom")
        self.assertEqual(
            _legacy_actuator_config("template_switch", "/data/etc/switch.ini", "").config_path,
            "/data/etc/switch.ini",
        )
        self.assertEqual(_legacy_charger("goe_charger", "/data/etc/charger.ini").config_path, "/data/etc/charger.ini")
        self.assertIsNone(_legacy_charger("", "/data/etc/charger.ini"))
        self.assertEqual(_legacy_native_measurement_config(None).type, "charger_native")
        self.assertEqual(_legacy_native_measurement_config("/data/etc/meter.ini").config_path, "/data/etc/meter.ini")
        self.assertEqual(_legacy_hybrid_measurement_config("none", None, "goe_charger").type, "charger_native")
        self.assertEqual(_legacy_hybrid_measurement_config("none", None, "").type, "none")

    def test_legacy_topology_contracts_keep_policy_phase_paths_and_measurement_roles(self) -> None:
        cases = (
            (
                "simple relay with meter path",
                """
[DEFAULT]
Host=192.0.2.76
Mode=2
Phase=L3

[Backends]
MeterType=template_meter
MeterConfigPath=/data/etc/meter.ini
SwitchType=template_switch
SwitchConfigPath=/data/etc/switch.ini
""",
                (
                    "simple_relay",
                    "template_switch",
                    "/data/etc/switch.ini",
                    "external_meter",
                    "/data/etc/meter.ini",
                    None,
                    None,
                    "scheduled",
                    "L3",
                ),
            ),
            (
                "native charger with external meter",
                """
[DEFAULT]
Mode=1
Phase=L2

[Backends]
MeterType=template_meter
MeterConfigPath=/data/etc/meter.ini
SwitchType=none
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
""",
                (
                    "native_device",
                    None,
                    None,
                    "external_meter",
                    "/data/etc/meter.ini",
                    "goe_charger",
                    "/data/etc/charger.ini",
                    "auto",
                    "L2",
                ),
            ),
            (
                "hybrid charger with actuator native meter",
                """
[DEFAULT]
Mode=2

[Backends]
MeterType=template_meter
SwitchType=template_switch
SwitchConfigPath=/data/etc/switch.ini
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
""",
                (
                    "hybrid_topology",
                    "template_switch",
                    "/data/etc/switch.ini",
                    "actuator_native",
                    None,
                    "goe_charger",
                    "/data/etc/charger.ini",
                    "scheduled",
                    "L1",
                ),
            ),
        )
        for label, text, expected in cases:
            with self.subTest(label=label):
                parser = configparser.ConfigParser()
                parser.read_string(text)
                parsed = legacy_topology_from_config(parser)
                topology, actuator, actuator_path, measurement, measurement_path, charger, charger_path, mode, phase = expected
                self.assertEqual(parsed.topology.type, topology)
                self.assertEqual(None if parsed.actuator is None else parsed.actuator.type, actuator)
                self.assertEqual(None if parsed.actuator is None else parsed.actuator.config_path, actuator_path)
                self.assertEqual(parsed.measurement.type, measurement)
                self.assertEqual(parsed.measurement.config_path, measurement_path)
                self.assertEqual(None if parsed.charger is None else parsed.charger.type, charger)
                self.assertEqual(None if parsed.charger is None else parsed.charger.config_path, charger_path)
                self.assertEqual(parsed.policy.mode, mode)
                self.assertEqual(parsed.policy.phase, phase)
