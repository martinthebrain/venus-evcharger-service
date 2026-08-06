# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_topology_config_support import *  # noqa: F401,F403

class _TopologyConfigTestsPart1:
    def test_legacy_runtime_values_handles_configs_without_default_mapping(self) -> None:
        class _NoDefaultConfig:
            def __contains__(self, key: str) -> bool:
                return False

            def has_section(self, name: str) -> bool:
                return False

        runtime = _legacy_runtime_values(cast(Any, _NoDefaultConfig()))

        self.assertEqual(runtime.defaults, {})
        self.assertEqual(runtime.host, "")
        self.assertEqual(runtime.meter_type, "shelly_meter")
        self.assertEqual(runtime.switch_type, "shelly_contactor_switch")

    def test_parse_simple_relay_with_fixed_reference(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300

[Policy]
Mode=manual
Phase=L1
"""
        )

        parsed = parse_topology_config(parser)

        self.assertEqual(parsed.topology.type, "simple_relay")
        self.assertEqual(parsed.actuator.type, "template_switch")
        self.assertEqual(parsed.measurement.type, "fixed_reference")
        self.assertEqual(parsed.measurement.reference_watts, 2300.0)
        self.assertEqual(parsed.policy.mode, "manual")

    def test_parse_auto_without_measurement_fails(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=none

[Policy]
Mode=auto
"""
        )

        with self.assertRaisesRegex(TopologyConfigError, "measurement"):
            parse_topology_config(parser)

    def test_parse_topology_validation_and_literal_errors(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Measurement]
Type=actuator_native
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "simple_relay requires an actuator"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "native_device requires a charger"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=hybrid_topology

[Charger]
Type=goe_charger
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "hybrid_topology requires both charger and actuator"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=external_meter
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "Measurement.ConfigPath"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=fixed_reference
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "Measurement.ReferenceWatts"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Policy]
Mode=invalid
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "invalid Policy.Mode"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=charger_native
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "requires a charger"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger

[Measurement]
Type=actuator_native
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "requires an actuator"):
            parse_topology_config(parser)

    def test_parse_topology_requires_sections_and_keys(self) -> None:
        with self.assertRaisesRegex(TopologyConfigError, r"missing required section \[Topology\]"):
            parse_topology_config(configparser.ConfigParser())

        parser = configparser.ConfigParser()
        parser.read_string("[Topology]\n")
        with self.assertRaisesRegex(TopologyConfigError, "missing required key Topology.Type"):
            parse_topology_config(parser)

    def test_parse_topology_allows_empty_measurement_and_default_policy(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=custom_topology
"""
        )

        parsed = parse_topology_config(parser)

        self.assertIsNone(parsed.measurement)
        self.assertEqual(parsed.policy.mode, "manual")
        self.assertEqual(parsed.policy.phase, "L1")

    def test_legacy_host_only_imports_simple_relay(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.0.2.44
Mode=0
Phase=L1
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "simple_relay")
        self.assertIsNotNone(parsed.actuator)
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")
        self.assertEqual(parsed.measurement.type, "actuator_native")

    def test_legacy_split_goe_imports_native_device(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=1
Phase=3P

[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
ChargerConfigPath=/data/etc/wizard-charger.ini
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "native_device")
        self.assertIsNone(parsed.actuator)
        self.assertEqual(parsed.charger.type, "goe_charger")
        self.assertEqual(parsed.measurement.type, "charger_native")
        self.assertEqual(parsed.policy.mode, "auto")


