# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_setup_cases_presets_support import *  # noqa: F401,F403

class __WizardSetupPresetCasesPart2:
    def test_configure_wallbox_generates_topology_preset_with_shelly_meter_and_cerbo_relay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="192.0.2.76",
                    meter_host_input="192.0.2.76",
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=78,
                    phase="L1",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="shelly-meter-cerbo-relay",
                    charger_backend=None,
                    cerbo_relay_index=1,
                    cerbo_relay_contact_mode="NC",
                    transport_kind="serial_rtu",
                    transport_host="",
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
            switch_text = (config_path.parent / "wizard-switch.ini").read_text(encoding="utf-8")
            self.assertIn("Mode=split\n", config_text)
            self.assertIn("MeterType=shelly_meter\n", config_text)
            self.assertIn(f"MeterConfigPath={config_path.parent / 'wizard-meter.ini'}\n", config_text)
            self.assertIn("SwitchType=cerbo_gx_relay_switch\n", config_text)
            self.assertIn(f"SwitchConfigPath={config_path.parent / 'wizard-switch.ini'}\n", config_text)
            self.assertIn("ChargerType=\n", config_text)
            self.assertIn("Type=shelly_meter\n", meter_text)
            self.assertIn("Host=192.0.2.76\n", meter_text)
            self.assertIn("Type=cerbo_gx_relay_switch\n", switch_text)
            self.assertIn("RelayIndex=1\n", switch_text)
            self.assertIn("ContactMode=NC\n", switch_text)
            self.assertIn("EnsureManualFunction=1\n", switch_text)
            self.assertEqual(result.role_hosts, {"meter": "192.0.2.76"})
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": False})
            self.assertEqual(result.topology_preset, "shelly-meter-cerbo-relay")
            self.assertEqual(result.topology_config["topology"]["type"], "simple_relay")
            self.assertEqual(result.topology_config["actuator"]["type"], "cerbo_gx_relay_switch")
            self.assertEqual(result.topology_config["measurement"]["type"], "external_meter")
            self.assertIsNone(result.topology_config["charger"])
            switch_device = next(
                device for device in result.device_inventory["devices"] if device["id"] == "switch_device"
            )
            self.assertEqual(switch_device["endpoint"], "local://cerbo-gx/relay/1")

    def test_configure_wallbox_generates_topology_preset_with_shelly_meter_and_modbus_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="shared.local",
                    meter_host_input="meter.local",
                    switch_host_input=None,
                    charger_host_input="cfos.local",
                    device_instance=76,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="shelly-meter-modbus-charger",
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

            config_text = config_path.read_text(encoding="utf-8")
            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=shelly_meter\n", config_text)
            self.assertIn("SwitchType=none\n", config_text)
            self.assertIn("ChargerType=modbus_charger\n", config_text)
            self.assertIn("Type=shelly_meter\n", meter_text)
            self.assertIn("Preset=cfos-power-brain-modbus\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "cfos.local", "meter": "meter.local"})
            self.assertEqual(result.topology_preset, "shelly-meter-modbus-charger")

    def test_configure_wallbox_generates_topology_preset_with_goe_and_switch_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=67,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="goe-external-switch-group",
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
            group_text = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=none\n", config_text)
            self.assertIn("SwitchType=switch_group\n", config_text)
            self.assertIn("ChargerType=goe_charger\n", config_text)
            self.assertIn("SupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n", group_text)
            self.assertTrue((config_path.parent / "wizard-phase1-switch.ini").exists())
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertIn("BaseUrl=http://charger.local\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "switch": "switch.local"})
            self.assertEqual(result.topology_preset, "goe-external-switch-group")
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": True, "charger": True})
            self.assertEqual(result.topology_config["topology"]["type"], "hybrid_topology")
            self.assertEqual(result.topology_config["actuator"]["type"], "switch_group")
            self.assertEqual(result.topology_config["measurement"]["type"], "charger_native")
            self.assertEqual(result.topology_config["charger"]["type"], "goe_charger")

    def test_configure_wallbox_generates_topology_preset_with_template_meter_goe_and_switch_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="adapter.local",
                    meter_host_input="meter.local",
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=68,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="template-meter-goe-switch-group",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="adapter.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            self.assertIn("Type=template_meter\n", meter_text)
            self.assertIn("BaseUrl=http://meter.local\n", meter_text)
            self.assertIn("Type=goe_charger\nBaseUrl=http://charger.local\n", charger_text)
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "meter": "meter.local", "switch": "switch.local"})
            self.assertEqual(result.topology_preset, "template-meter-goe-switch-group")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_configure_wallbox_generates_topology_preset_with_shelly_meter_modbus_and_switch_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="192.0.2.94",
                    meter_host_input="192.0.2.24",
                    switch_host_input="switch.local",
                    charger_host_input=None,
                    device_instance=69,
                    phase="3P",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="shelly-meter-modbus-switch-group",
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.0.2.95",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=9,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            group_text = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            self.assertIn("Type=modbus_charger\n", charger_text)
            self.assertIn("Transport=tcp\n", charger_text)
            self.assertIn("Host=192.0.2.95\n", charger_text)
            self.assertIn("SupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n", group_text)
            self.assertIn("Host=192.0.2.24\n", meter_text)
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertEqual(result.role_hosts, {"meter": "192.0.2.24", "switch": "switch.local"})
            self.assertEqual(result.topology_preset, "shelly-meter-modbus-switch-group")
            self.assertEqual(result.transport_kind, "tcp")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_configure_wallbox_generates_tuya_meter_switch_template_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="tuya.local",
                    meter_host_input="tuya-meter.local",
                    switch_host_input="tuya-switch.local",
                    charger_host_input="charger.local",
                    device_instance=70,
                    phase="L1",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="tuya-io-template-charger",
                    charger_backend="template_charger",
                    transport_kind="serial_rtu",
                    transport_host="charger.local",
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
            switch_text = (config_path.parent / "wizard-switch.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=tuya_meter\n", config_text)
            self.assertIn("SwitchType=tuya_switch\n", config_text)
            self.assertIn("ChargerType=template_charger\n", config_text)
            self.assertIn("Type=tuya_meter\n", meter_text)
            self.assertIn("BaseUrl=http://tuya-meter.local\n", meter_text)
            self.assertIn("Type=tuya_switch\n", switch_text)
            self.assertIn("BaseUrl=http://tuya-switch.local\n", switch_text)
            self.assertEqual(result.topology_preset, "tuya-io-template-charger")
            self.assertEqual(result.topology_config["actuator"]["type"], "tuya_switch")
            meter_profile = next(profile for profile in result.device_inventory["profiles"] if profile["id"] == "meter_external_meter")
            self.assertEqual(meter_profile["capabilities"][0]["adapter_type"], "tuya_meter")

    def test_configure_wallbox_generates_tasmota_meter_switch_template_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="tasmota.local",
                    meter_host_input="tasmota-meter.local",
                    switch_host_input="tasmota-switch.local",
                    charger_host_input="charger.local",
                    device_instance=71,
                    phase="L1",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="tasmota-io-template-charger",
                    charger_backend="template_charger",
                    transport_kind="serial_rtu",
                    transport_host="charger.local",
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
            switch_text = (config_path.parent / "wizard-switch.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=tasmota_meter\n", config_text)
            self.assertIn("SwitchType=tasmota_switch\n", config_text)
            self.assertIn("ChargerType=template_charger\n", config_text)
            self.assertIn("Type=tasmota_meter\n", meter_text)
            self.assertIn("Url=/cm?cmnd=Status+8\n", meter_text)
            self.assertIn("Type=tasmota_switch\n", switch_text)
            self.assertIn("Url=/cm?cmnd=Power+$enabled_text\n", switch_text)
            self.assertEqual(result.topology_preset, "tasmota-io-template-charger")
            self.assertEqual(result.topology_config["actuator"]["type"], "tasmota_switch")
            meter_profile = next(profile for profile in result.device_inventory["profiles"] if profile["id"] == "meter_external_meter")
            self.assertEqual(meter_profile["capabilities"][0]["adapter_type"], "tasmota_meter")

    def test_configure_wallbox_defaults_multi_adapter_without_preset_to_template_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="template.local",
                    meter_host_input="meter.local",
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=72,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="template_charger",
                    transport_kind="serial_rtu",
                    transport_host="charger.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            switch_text = (config_path.parent / "wizard-switch.ini").read_text(encoding="utf-8")
            self.assertIsNone(result.topology_preset)
            self.assertIn("Type=template_meter\n", meter_text)
            self.assertIn("Type=template_switch\n", switch_text)
            self.assertEqual(result.topology_config["actuator"]["type"], "template_switch")
