# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_topology_config_support import *  # noqa: F401,F403

class _TopologyConfigTestsPart2:
    def test_legacy_topology_import_covers_remaining_modes(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=2
Phase=L2

[Backends]
MeterType=none
SwitchType=none
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "simple_relay requires an actuator"):
            legacy_topology_from_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=2
Phase=L2
Host=192.168.1.44
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.policy.mode, "scheduled")
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")
        self.assertEqual(parsed.measurement.type, "actuator_native")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.1.44

[Backends]
SwitchType=shelly_combined
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=template_switch
MeterType=template_meter
MeterConfigPath=/data/etc/meter.ini
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.topology.type, "hybrid_topology")
        self.assertEqual(parsed.measurement.type, "external_meter")
        self.assertEqual(str(parsed.measurement.config_path), "/data/etc/meter.ini")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=template_switch
MeterType=none
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.measurement.type, "charger_native")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=0

[Backends]
SwitchType=unknown_switch
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.actuator.type, "custom")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.1.44
Mode=0

[Backends]
MeterType=none
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.measurement.type, "none")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
MeterType=template_meter
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.measurement.type, "none")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
MeterType=none
SwitchType=template_switch
ChargerType=
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.actuator.type, "template_switch")
        self.assertEqual(parsed.measurement.type, "none")

    def test_runtime_summary_is_configured_requires_host_for_legacy_combined(self) -> None:
        summary = BackendRuntimeSummary(
            backend_mode="combined",
            meter_type="shelly_meter",
            meter_config_path=None,
            switch_type="shelly_contactor_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            topology_configured=False,
            primary_rpc_configured=False,
        )

        self.assertFalse(runtime_summary_is_configured(summary, legacy_host=""))
        self.assertTrue(runtime_summary_is_configured(summary, legacy_host="192.168.1.44"))

    def test_runtime_summary_is_configured_accepts_split_topology_without_legacy_host(self) -> None:
        summary = BackendRuntimeSummary(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=None,
            switch_type="template_switch",
            switch_config_path=Path("/data/etc/wizard-switch.ini"),
            charger_type=None,
            charger_config_path=None,
            topology_configured=True,
            primary_rpc_configured=False,
        )

        self.assertTrue(runtime_summary_is_configured(summary, legacy_host=""))

    def test_runtime_summary_from_service_uses_config_as_canonical_source(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/wizard-charger.ini

[Measurement]
Type=charger_native
"""
        )
        service = SimpleNamespace(config=parser)

        selection = runtime_summary_from_service(service)

        self.assertEqual(selection.backend_mode, "split")
        self.assertIsNone(selection.meter_type)
        self.assertIsNone(selection.switch_type)
        self.assertEqual(selection.charger_type, "goe_charger")

    def test_runtime_summary_from_service_uses_resolved_backend_bundle(self) -> None:
        runtime = runtime_summary_fixture()
        service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=runtime))

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertEqual(resolved.meter_type, "template_meter")
        self.assertEqual(resolved.switch_type, "template_switch")
        self.assertEqual(resolved.charger_type, "goe_charger")

    def test_runtime_summary_from_service_prefers_backend_bundle_selection_over_runtime_topology(self) -> None:
        runtime = runtime_summary_fixture()
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
"""
        )
        topology = parse_topology_config(parser)
        service = SimpleNamespace(
            _topology_config=topology,
            _backend_bundle=SimpleNamespace(runtime=runtime),
        )

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertEqual(resolved.meter_type, "template_meter")
        self.assertEqual(resolved.switch_type, "template_switch")
        self.assertEqual(resolved.charger_type, "goe_charger")

    def test_runtime_summary_from_service_uses_runtime_topology(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/wizard-charger.ini

[Measurement]
Type=charger_native
"""
        )
        topology = parse_topology_config(parser)
        service = SimpleNamespace(_topology_config=topology)

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertIsNone(resolved.meter_type)
        self.assertIsNone(resolved.switch_type)
        self.assertEqual(resolved.charger_type, "goe_charger")

    def test_runtime_summary_from_service_supports_bootstrap_normalized_summary(self) -> None:
        service = SimpleNamespace(
            _backend_runtime_summary=runtime_summary_fixture(
                meter_config_path=Path("/data/etc/wizard-meter.ini"),
                switch_config_path=Path("/data/etc/wizard-switch.ini"),
                charger_config_path=Path("/data/etc/wizard-charger.ini"),
            )
        )

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertEqual(resolved.meter_type, "template_meter")
        self.assertEqual(resolved.switch_type, "template_switch")
        self.assertEqual(resolved.charger_type, "goe_charger")
        self.assertEqual(str(resolved.meter_config_path), "/data/etc/wizard-meter.ini")
        self.assertEqual(str(resolved.switch_config_path), "/data/etc/wizard-switch.ini")
        self.assertEqual(str(resolved.charger_config_path), "/data/etc/wizard-charger.ini")

    def test_runtime_summary_from_service_rejects_missing_canonical_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical backend configuration"):
            runtime_summary_from_service(SimpleNamespace())

    def test_load_runtime_backend_summary_supports_fixed_reference_topology(self) -> None:
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
"""
        )

        summary = load_runtime_backend_summary(parser)

        self.assertEqual(summary.backend_mode, "split")
        self.assertIsNone(summary.meter_type)
        self.assertEqual(summary.switch_type, "template_switch")
        self.assertIsNone(summary.charger_type)

