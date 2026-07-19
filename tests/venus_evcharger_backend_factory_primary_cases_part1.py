# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_backend_factory_primary_cases_support import *  # noqa: F401,F403

class _TestShellyWallboxBackendFactoryPrimaryPart1:
    def test_factory_helper_paths_cover_normalization_and_adapter_type_reads(self) -> None:
        self.assertIsNone(_normalized_path(None))
        self.assertIsNone(_normalized_path("   "))
        self.assertEqual(_normalized_path(" /tmp/test.ini "), Path("/tmp/test.ini"))

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            _adapter_type_from_config_path(None)

        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.ini"
            with self.assertRaises(FileNotFoundError):
                _adapter_type_from_config_path(str(missing))

            parser_path = Path(temp_dir) / "backend.ini"
            parser_path.write_text("[DEFAULT]\nType=template_meter\n", encoding="utf-8")
            self.assertEqual(_adapter_type_from_config_path(str(parser_path)), "template_meter")

    def test_load_runtime_backend_summary_uses_topology_sections_for_simple_relay_bridge(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=shelly_contactor_switch

[Measurement]
Type=actuator_native
"""
        )

        selection = load_runtime_backend_summary(parser)

        self.assertEqual(selection.backend_mode, "split")
        self.assertEqual(selection.meter_type, "shelly_meter")
        self.assertEqual(selection.switch_type, "shelly_contactor_switch")

    def test_private_resolvers_reject_none_backends_outside_split_mode(self) -> None:
        selection = BackendRuntimeSummary(
            backend_mode="combined",
            meter_type=None,
            meter_config_path=Path(""),
            switch_type=None,
            switch_config_path=Path(""),
            charger_type=None,
            charger_config_path=Path(""),
            topology_configured=False,
            primary_rpc_configured=False,
        )
        self.assertIsNone(_resolved_meter_backend(selection, SimpleNamespace()))
        self.assertIsNone(_resolved_switch_backend(selection, SimpleNamespace()))

    def test_registry_rejects_unsupported_meter_backend_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported meter backend"):
            create_meter_backend("unknown", SimpleNamespace(), "")

    def test_build_service_backends_uses_default_combined_selection(self) -> None:
        service = _service_from_backends_config()
        resolved = build_service_backends(service)
        self.assertEqual(resolved.runtime.backend_mode, "combined")
        self.assertTrue(resolved.runtime.topology_configured)
        self.assertTrue(resolved.runtime.primary_rpc_configured)
        self.assertEqual(resolved.runtime.meter_type, "shelly_meter")
        self.assertEqual(resolved.runtime.switch_type, "shelly_contactor_switch")
        self.assertIsInstance(resolved.meter, ShellyMeterBackend)
        self.assertIsInstance(resolved.switch, ShellyContactorSwitchBackend)

    def test_build_service_backends_supports_split_meter_and_switch_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            meter_path.write_text("[Adapter]\nType=shelly_meter\nHost=192.168.1.20\n", encoding="utf-8")
            switch_path.write_text("[Adapter]\nType=shelly_switch\nHost=192.168.1.21\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n[PhaseMap]\nP1=0\nP1_P2=0,1\nP1_P2_P3=0,1,2\n", encoding="utf-8")
            service = _service_from_backends_config(
                mode="split",
                meter_type="shelly_meter",
                switch_type="shelly_switch",
                meter_config_path=str(meter_path),
                switch_config_path=str(switch_path),
            )
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.meter, ShellyMeterBackend)
            self.assertIsInstance(resolved.switch, ShellySwitchBackend)

    def test_build_service_backends_supports_contactor_and_template_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = Path(temp_dir) / "switch.ini"
            switch_path.write_text("[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.21\n", encoding="utf-8")
            service = _service_from_backends_config(
                mode="split",
                meter_type="shelly_combined",
                switch_type="shelly_contactor_switch",
                switch_config_path=str(switch_path),
            )
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.switch, ShellyContactorSwitchBackend)
            switch_path.write_text("[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[StateRequest]\nUrl=/switch/state\n[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n[CommandRequest]\nUrl=/switch/control\n[PhaseRequest]\nUrl=/switch/phase\n", encoding="utf-8")
            service.config["Backends"]["SwitchType"] = "template_switch"
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)

    def test_build_service_backends_supports_switch_group_and_template_meter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            p1_path = Path(temp_dir) / "phase1-switch.ini"
            p2_path = Path(temp_dir) / "phase2-switch.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            p1_path.write_text("[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n[StateRequest]\nUrl=/state\n[StateResponse]\nEnabledPath=enabled\n[CommandRequest]\nMethod=POST\nUrl=/control\n", encoding="utf-8")
            p2_path.write_text("[Adapter]\nType=shelly_switch\nHost=192.168.1.21\n", encoding="utf-8")
            switch_path.write_text("[Adapter]\nType=switch_group\n[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\n", encoding="utf-8")
            meter_path = Path(temp_dir) / "meter.ini"
            meter_path.write_text("[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n[MeterRequest]\nUrl=/meter/state\n[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n", encoding="utf-8")
            service = _service_from_backends_config(
                mode="split",
                meter_type="template_meter",
                switch_type="switch_group",
                meter_config_path=str(meter_path),
                switch_config_path=str(switch_path),
            )
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.meter, TemplateMeterBackend)
            self.assertIsInstance(resolved.switch, SwitchGroupBackend)

    def test_build_service_backends_supports_goe_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n", encoding="utf-8")
            service = _service_from_backends_config(
                mode="split",
                meter_type="none",
                switch_type="none",
                charger_type="goe_charger",
                charger_config_path=str(charger_path),
            )
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, GoEChargerBackend)

    def test_build_service_backends_supports_topology_native_device_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n", encoding="utf-8")
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath={charger_path}

[Measurement]
Type=charger_native
"""
            )
            service = SimpleNamespace(
                config=parser,
                phase="L1",
                host="",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertTrue(resolved.runtime.topology_configured)
            self.assertFalse(resolved.runtime.primary_rpc_configured)
            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertEqual(resolved.runtime.meter_type, None)
            self.assertEqual(resolved.runtime.switch_type, None)
            self.assertEqual(resolved.runtime.charger_type, "goe_charger")
            self.assertIsInstance(resolved.charger, GoEChargerBackend)

    def test_build_service_backends_supports_topology_simple_relay_external_meter_and_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n[MeterRequest]\nUrl=/meter/state\n[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n",
                encoding="utf-8",
            )
            switch_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[StateRequest]\nUrl=/switch/state\n[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n[CommandRequest]\nUrl=/switch/control\n[PhaseRequest]\nUrl=/switch/phase\n",
                encoding="utf-8",
            )
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath={switch_path}

[Measurement]
Type=external_meter
ConfigPath={meter_path}
"""
            )
            service = SimpleNamespace(
                config=parser,
                phase="L1",
                host="",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertEqual(resolved.runtime.meter_type, "template_meter")
            self.assertEqual(resolved.runtime.switch_type, "template_switch")
            self.assertIsInstance(resolved.meter, TemplateMeterBackend)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)

    def test_build_service_backends_prefers_direct_topology_over_conflicting_legacy_attrs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n[MeterRequest]\nUrl=/meter/state\n[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n",
                encoding="utf-8",
            )
            switch_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[StateRequest]\nUrl=/switch/state\n[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n[CommandRequest]\nUrl=/switch/control\n[PhaseRequest]\nUrl=/switch/phase\n",
                encoding="utf-8",
            )
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath={switch_path}

[Measurement]
Type=external_meter
ConfigPath={meter_path}
"""
            )
            service = SimpleNamespace(
                config=parser,
                phase="L1",
                host="",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
                backend_mode="combined",
                meter_backend_type="shelly_combined",
                switch_backend_type="shelly_combined",
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertEqual(resolved.runtime.meter_type, "template_meter")
            self.assertEqual(resolved.runtime.switch_type, "template_switch")
            self.assertIsInstance(resolved.meter, TemplateMeterBackend)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)

    def test_build_service_backends_supports_runtime_topology_without_reparsing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n[MeterRequest]\nUrl=/meter/state\n[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n",
                encoding="utf-8",
            )
            switch_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[StateRequest]\nUrl=/switch/state\n[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n[CommandRequest]\nUrl=/switch/control\n[PhaseRequest]\nUrl=/switch/phase\n",
                encoding="utf-8",
            )
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath={switch_path}

[Measurement]
Type=external_meter
ConfigPath={meter_path}
"""
            )
            topology = parse_topology_config(parser)
            service = SimpleNamespace(
                _topology_config=topology,
                phase="L1",
                host="",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
                backend_mode="combined",
                meter_backend_type="shelly_combined",
                switch_backend_type="shelly_combined",
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertEqual(resolved.runtime.meter_type, "template_meter")
            self.assertEqual(resolved.runtime.switch_type, "template_switch")
            self.assertIsInstance(resolved.meter, TemplateMeterBackend)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)
