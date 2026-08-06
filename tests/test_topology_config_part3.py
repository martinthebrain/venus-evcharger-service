# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_topology_config_support import *  # noqa: F401,F403

class _TopologyConfigTestsPart3:
    def test_backend_helpers_use_resolved_runtime_selection(self) -> None:
        runtime = runtime_summary_fixture()
        service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=runtime))

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "goe_charger")

    def test_backend_helpers_use_direct_topology_for_fixed_reference_simple_relay(self) -> None:
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
        service = SimpleNamespace(config=parser)

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "fixed_reference")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "")

    def test_backend_helpers_use_direct_topology_for_external_meter_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n",
                encoding="utf-8",
            )
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=external_meter
ConfigPath={meter_path}
"""
            )
            service = SimpleNamespace(config=parser)

            self.assertEqual(backend_mode_for_service(service, "combined"), "split")
            self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
            self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
            runtime = load_runtime_backend_summary(parser)
            self.assertEqual(runtime.meter_type, "template_meter")
            self.assertEqual(str(runtime.meter_config_path), str(meter_path))
            self.assertEqual(runtime.switch_type, "template_switch")
            self.assertEqual(str(runtime.switch_config_path), "/data/etc/wallbox-actuator.ini")

    def test_backend_helpers_use_runtime_topology(self) -> None:
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
        service = SimpleNamespace(_topology_config=topology)

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "fixed_reference")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "")

    def test_backend_helpers_still_prefer_backend_bundle_selection_over_runtime_topology(self) -> None:
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
        runtime = runtime_summary_fixture()
        service = SimpleNamespace(
            _topology_config=topology,
            _backend_bundle=SimpleNamespace(runtime=runtime),
        )

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "goe_charger")

    def test_backend_helpers_prefer_runtime_bundle_over_conflicting_topology(self) -> None:
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
        runtime = runtime_summary_fixture()
        service = SimpleNamespace(
            _topology_config=topology,
            _backend_bundle=SimpleNamespace(runtime=runtime),
        )

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "goe_charger")

    def test_backend_config_helper_edges_cover_fallbacks_and_compat_views(self) -> None:
        self.assertEqual(_adapter_type_from_config_path(None), None)
        adapter_parser = configparser.ConfigParser()
        adapter_parser.optionxform = str
        adapter_parser["Adapter"] = {"Type": "template_meter"}
        self.assertEqual(_adapter_type_from_parser(adapter_parser), "template_meter")

        default_parser = configparser.ConfigParser()
        default_parser.optionxform = str
        default_parser["DEFAULT"] = {"Type": "template_meter"}
        self.assertEqual(_adapter_type_from_parser(default_parser), "template_meter")

        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.ini"
            self.assertIsNone(_adapter_type_from_config_path(str(missing)))

            default_only = Path(temp_dir) / "default-only.ini"
            default_only.write_text("[DEFAULT]\nType=template_meter\n", encoding="utf-8")
            self.assertEqual(_adapter_type_from_config_path(str(default_only)), "template_meter")

        self.assertIsNone(_native_meter_type_for_actuator("template_switch"))

        native_parser = configparser.ConfigParser()
        native_parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/charger.ini

[Measurement]
Type=charger_native
"""
        )
        native_topology = parse_topology_config(native_parser)
        self.assertEqual(_topology_backend_label(native_topology, "meter"), "goe_charger")
        self.assertEqual(_topology_backend_label(native_topology, "charger"), "goe_charger")
        self.assertIsNone(_topology_backend_label(native_topology, "switch"))
        self.assertIsNone(_topology_backend_label(native_topology, "unknown"))

        fixed_reference_parser = configparser.ConfigParser()
        fixed_reference_parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/switch.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        fixed_reference_topology = parse_topology_config(fixed_reference_parser)
        self.assertEqual(_topology_backend_label(fixed_reference_topology, "meter"), "fixed_reference")

        learned_reference_parser = configparser.ConfigParser()
        learned_reference_parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=shelly_switch
ConfigPath=/data/etc/switch.ini

[Measurement]
Type=learned_reference
ReferenceWatts=1840
"""
        )
        learned_reference_topology = parse_topology_config(learned_reference_parser)
        self.assertEqual(_topology_backend_label(learned_reference_topology, "meter"), "learned_reference")
        self.assertEqual(_topology_backend_label(learned_reference_topology, "switch"), "shelly_switch")
        self.assertEqual(_native_meter_type_for_actuator("shelly_switch"), "shelly_meter")
        learned_runtime = _runtime_summary_from_topology(learned_reference_topology)
        self.assertEqual(learned_runtime.switch_type, "shelly_switch")
        self.assertEqual(str(learned_runtime.switch_config_path), "/data/etc/switch.ini")
        self.assertIsNone(learned_runtime.meter_type)

        no_measurement_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=None,
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertIsNone(_topology_backend_label(no_measurement_topology, "meter"))
        none_measurement_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=MeasurementConfig(type="none"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_topology_backend_label(none_measurement_topology, "meter"), "none")
        actuator_native_without_actuator = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertIsNone(_topology_backend_label(actuator_native_without_actuator, "meter"))

        actuator_native_with_shelly_switch = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="shelly_switch", config_path="/data/etc/switch.ini"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_topology_backend_label(actuator_native_with_shelly_switch, "meter"), "shelly_meter")
        actuator_native_runtime = _runtime_summary_from_topology(actuator_native_with_shelly_switch)
        self.assertEqual(actuator_native_runtime.meter_type, "shelly_meter")
        self.assertIsNone(actuator_native_runtime.meter_config_path)

        runtime = _build_runtime_summary(
            backend_mode="split",
            meter_type=None,
            meter_config_path=None,
            switch_type=None,
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
        )
        service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=runtime))
        self.assertEqual(backend_type_for_service(service, "meter", "fallback"), "fallback")

        service_with_none_attr = SimpleNamespace(meter_backend_type=None)
        self.assertEqual(backend_type_for_service(service_with_none_attr, "meter", "fallback"), "fallback")

        legacy_parser = configparser.ConfigParser()
        legacy_parser.read_string(
            """
[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
"""
        )
        self.assertEqual(backend_type_for_service(SimpleNamespace(config=legacy_parser), "meter", "fallback"), "fallback")

        combined_summary = _build_runtime_summary(
            backend_mode="combined",
            meter_type="shelly_meter",
            meter_config_path=None,
            switch_type="shelly_contactor_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            primary_rpc_configured=False,
        )
        self.assertTrue(runtime_summary_uses_legacy_primary_rpc(combined_summary, legacy_host="192.0.2.20"))
        with self.assertRaisesRegex(TypeError, "BackendRuntimeSummary"):
            backend_selection_view(cast(Any, None))
        self.assertEqual(backend_selection_view_from_config(legacy_parser)["mode"], "split")

    def test_topology_private_helpers_cover_remaining_measurement_and_legacy_edges(self) -> None:
        self.assertIsNone(_optional_text(None))
        self.assertEqual(_optional_text("  demo  "), "demo")

        no_measurement = EvChargerTopologyConfig(
            topology=TopologyConfig(type="custom_topology"),
            measurement=None,
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        _validate_measurement(no_measurement)
        no_measurement_runtime = _runtime_summary_from_topology(no_measurement)
        self.assertIsNone(no_measurement_runtime.meter_type)

        self.assertEqual(_legacy_measurement_config("template_meter", "/data/etc/meter.ini", "").type, "external_meter")
        self.assertEqual(_legacy_native_measurement_config(None).type, "charger_native")
        self.assertEqual(_legacy_native_measurement_config("/data/etc/meter.ini").type, "external_meter")
        self.assertEqual(_legacy_hybrid_measurement_config("template_meter", None, "").type, "actuator_native")
        self.assertEqual(_legacy_hybrid_measurement_config("template_meter", "/data/etc/meter.ini", "").type, "external_meter")

        unknown_measurement_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="custom_topology"),
            measurement=cast(MeasurementConfig, SimpleNamespace(type="mystery")),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        runtime = _runtime_summary_from_topology(unknown_measurement_topology)
        self.assertIsNone(runtime.meter_type)
        self.assertIsNone(_topology_backend_label(unknown_measurement_topology, "meter"))

        charger_with_path_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            charger=ChargerConfig(type="goe_charger", config_path="/data/etc/charger.ini"),
            measurement=MeasurementConfig(type="charger_native"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        charger_runtime = _runtime_summary_from_topology(charger_with_path_topology)
        self.assertEqual(charger_runtime.charger_type, "goe_charger")
        self.assertEqual(str(charger_runtime.charger_config_path), "/data/etc/charger.ini")
        self.assertFalse(charger_runtime.primary_rpc_configured)


if __name__ == "__main__":
    unittest.main()
