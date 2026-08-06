# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_setup_cases_presets_support import *  # noqa: F401,F403

class __WizardSetupPresetCasesPart1:
    def test_configure_wallbox_generates_phase_switch_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="hybrid_topology",
                    host_input="192.0.2.80",
                    meter_host_input=None,
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=63,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="simpleevse_charger",
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.80",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            group_text = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            self.assertIn("SwitchType=switch_group\n", config_path.read_text(encoding="utf-8"))
            self.assertIn("P1=wizard-phase1-switch.ini\n", group_text)
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "switch": "switch.local"})
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": True, "charger": True})
            self.assertEqual(result.topology_config["topology"]["type"], "hybrid_topology")
            self.assertEqual(result.topology_config["actuator"]["type"], "switch_group")
            self.assertEqual(result.topology_config["measurement"]["type"], "charger_native")
            self.assertEqual(result.topology_config["charger"]["type"], "simpleevse_charger")

    def test_configure_wallbox_generates_native_modbus_tcp_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="192.0.2.90",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=64,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.0.2.91",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=7,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Type=modbus_charger\n", charger_text)
            self.assertIn("Transport=tcp\n", charger_text)
            self.assertIn("Host=192.0.2.91\n", charger_text)
            self.assertIn("UnitId=7\n", charger_text)
            self.assertEqual(result.transport_kind, "tcp")
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": False, "charger": True})

    def test_configure_wallbox_generates_abb_terra_ac_modbus_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="terra.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="terra.local",
                    device_instance=74,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="modbus_charger",
                    charger_preset="abb-terra-ac-modbus",
                    transport_kind="tcp",
                    transport_host="terra.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Preset=abb-terra-ac-modbus\n", charger_text)
            self.assertIn("Address=16645\n", charger_text)
            self.assertIn("TrueValue=0\n", charger_text)
            self.assertIn("Address=16640\n", charger_text)
            self.assertIn("Scale=1000\n", charger_text)

    def test_configure_wallbox_generates_cfos_power_brain_modbus_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="cfos.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="cfos.local",
                    device_instance=75,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="modbus_charger",
                    charger_preset="cfos-power-brain-modbus",
                    transport_kind="tcp",
                    transport_host="cfos.local",
                    transport_port=4701,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Preset=cfos-power-brain-modbus\n", charger_text)
            self.assertIn("SupportedPhaseSelections=P1,P1_P2_P3\n", charger_text)
            self.assertIn("Address=8094\n", charger_text)
            self.assertIn("Address=8093\n", charger_text)
            self.assertIn("Map=P1:1,P1_P2_P3:0\n", charger_text)
            self.assertEqual(result.warnings[-1], "The cFos preset only writes charging_enable, charging_cur_limit, and relay_select regularly; avoid adding periodic writes to other cFos registers because they persist to flash.")

    def test_configure_wallbox_generates_openwb_modbus_secondary_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="openwb.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="openwb.local",
                    device_instance=77,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="modbus_charger",
                    charger_preset="openwb-modbus-secondary",
                    transport_kind="tcp",
                    transport_host="openwb.local",
                    transport_port=1502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Preset=openwb-modbus-secondary\n", charger_text)
            self.assertIn("EnableUsesCurrentWrite=1\n", charger_text)
            self.assertIn("EnableDefaultCurrentAmps=6\n", charger_text)
            self.assertIn("Address=10171\n", charger_text)
            self.assertIn("Address=10116\n", charger_text)
            self.assertEqual(
                result.warnings[-1],
                "The openWB Modbus preset expects the charger in secondary Modbus mode. If you enable the openWB heartbeat, keep this service polling continuously so the heartbeat does not expire.",
            )

    def test_configure_wallbox_generates_topology_preset_with_shelly_io_and_modbus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="192.0.2.92",
                    meter_host_input="192.0.2.20",
                    switch_host_input="192.0.2.21",
                    charger_host_input=None,
                    device_instance=65,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="shelly-io-modbus-charger",
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.0.2.93",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=8,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            switch_text = (config_path.parent / "wizard-switch.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Type=shelly_meter\n", meter_text)
            self.assertIn("Type=shelly_switch\n", switch_text)
            self.assertIn("Type=modbus_charger\n", charger_text)
            self.assertIn("Host=192.0.2.20\n", meter_text)
            self.assertIn("Host=192.0.2.21\n", switch_text)
            self.assertEqual(result.role_hosts, {"meter": "192.0.2.20", "switch": "192.0.2.21"})
            self.assertEqual(result.topology_preset, "shelly-io-modbus-charger")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": True})
            self.assertEqual(result.topology_config["topology"]["type"], "hybrid_topology")
            self.assertEqual(result.topology_config["actuator"]["type"], "shelly_switch")
            self.assertEqual(result.topology_config["measurement"]["type"], "external_meter")
            self.assertEqual(result.topology_config["charger"]["type"], "modbus_charger")

    def test_configure_wallbox_generates_topology_preset_with_shelly_meter_and_goe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="goe.local",
                    meter_host_input="meter.local",
                    switch_host_input=None,
                    charger_host_input="charger.local",
                    device_instance=66,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="shelly-meter-goe",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            config_text = config_path.read_text(encoding="utf-8")
            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=shelly_meter\n", config_text)
            self.assertIn("SwitchType=none\n", config_text)
            self.assertIn("ChargerType=goe_charger\n", config_text)
            self.assertIn("Type=shelly_meter\n", meter_text)
            self.assertIn("Host=meter.local\n", meter_text)
            self.assertIn("Type=goe_charger\nBaseUrl=http://charger.local\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "meter": "meter.local"})
            self.assertEqual(result.topology_preset, "shelly-meter-goe")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": False, "charger": True})
            self.assertEqual(result.topology_config["topology"]["type"], "native_device")
            self.assertEqual(result.topology_config["measurement"]["type"], "external_meter")
            self.assertEqual(result.topology_config["charger"]["type"], "goe_charger")


