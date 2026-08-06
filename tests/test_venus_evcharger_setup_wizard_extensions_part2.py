# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_venus_evcharger_setup_wizard_extensions_support import *  # noqa: F401,F403

class _TestShellyWallboxSetupWizardExtensionsPart2:
    def test_main_inventory_can_add_profile_capability_and_binding_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._simple_relay_config(temp_dir)

            add_profile_rc, add_profile_payload = self._run_json_inventory_action(
                config_path,
                [
                    "--inventory-action",
                    "add-profile",
                    "--inventory-profile-id",
                    "custom_node",
                    "--inventory-label",
                    "Custom node",
                    "--inventory-capability-id",
                    "switch",
                    "--inventory-kind",
                    "switch",
                    "--inventory-adapter-type",
                    "template_switch",
                    "--inventory-supported-phases",
                    "L2",
                    "--inventory-switching-mode",
                    "contactor",
                    "--inventory-supports-feedback",
                ],
            )
            self.assertEqual(add_profile_rc, 0)
            self.assertEqual(add_profile_payload["profile_id"], "custom_node")

            add_capability_rc, add_capability_payload = self._run_json_inventory_action(
                config_path,
                [
                    "--inventory-action",
                    "add-capability",
                    "--inventory-profile-id",
                    "custom_node",
                    "--inventory-capability-id",
                    "meter",
                    "--inventory-kind",
                    "meter",
                    "--inventory-adapter-type",
                    "template_meter",
                    "--inventory-supported-phases",
                    "L2",
                    "--inventory-measures-power",
                    "--inventory-measures-energy",
                ],
            )
            self.assertEqual(add_capability_rc, 0)
            custom_profile = self._inventory_profile(add_capability_payload, "custom_node")
            self.assertEqual(len(custom_profile["capabilities"]), 2)

            add_device_rc, add_device_payload = self._run_json_inventory_action(
                config_path,
                [
                    "--inventory-action",
                    "add-device",
                    "--inventory-profile-id",
                    "custom_node",
                    "--inventory-device-id",
                    "custom_node_l2",
                    "--inventory-label",
                    "Custom node L2",
                    "--inventory-endpoint",
                    "http://custom-node.local",
                ],
            )
            self.assertEqual(add_device_rc, 0)
            self.assertEqual(add_device_payload["device_id"], "custom_node_l2")

            bind_rc, bind_payload = self._run_json_inventory_action(
                config_path,
                [
                    "--inventory-action",
                    "set-binding-member",
                    "--inventory-binding-id",
                    "custom_measurement",
                    "--inventory-binding-role",
                    "measurement",
                    "--inventory-binding-label",
                    "Custom measurement",
                    "--inventory-device-id",
                    "custom_node_l2",
                    "--inventory-capability-id",
                    "meter",
                    "--inventory-member-phases",
                    "L2",
                ],
            )
            self.assertEqual(bind_rc, 0)
            custom_binding = self._inventory_binding(bind_payload, "custom_measurement")
            self.assertEqual(custom_binding["phase_scope"], ["L2"])
            self.assertEqual(custom_binding["members"][0]["device_id"], "custom_node_l2")

            unbind_rc, unbind_payload = self._run_json_inventory_action(
                config_path,
                [
                    "--inventory-action",
                    "remove-binding-member",
                    "--inventory-binding-id",
                    "custom_measurement",
                    "--inventory-device-id",
                    "custom_node_l2",
                ],
            )
            self.assertEqual(unbind_rc, 0)
            self.assertFalse(self._inventory_has_binding(unbind_payload, "custom_measurement"))

    def test_main_inventory_guided_add_profile_builds_profile_device_and_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=77,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend=None,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            inventory_path = config_path.with_name(f"{config_path.name}.wizard-inventory.ini")
            with (
                patch(
                    "builtins.input",
                    side_effect=[
                        "custom_meter",
                        "Custom meter",
                        "2",
                        "",
                        "",
                        "L2",
                        "y",
                        "y",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "meter_l2",
                        "",
                        "http://meter-l2.local",
                        "",
                        "",
                        "",
                        "",
                    ],
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "guided-add-profile",
                    ]
                )

            self.assertEqual(rc, 0)
            parser = configparser.ConfigParser()
            parser.read(inventory_path, encoding="utf-8")
            self.assertEqual(parser["Profile:custom_meter"]["Label"], "Custom meter")
            self.assertEqual(parser["Capability:custom_meter:meter"]["Kind"], "meter")
            self.assertEqual(parser["Capability:custom_meter:meter"]["SupportedPhases"], "L2")
            self.assertEqual(parser["Device:meter_l2"]["Profile"], "custom_meter")
            self.assertEqual(parser["Binding:custom_meter_measurement"]["Role"], "measurement")
            self.assertEqual(parser["BindingMember:custom_meter_measurement:1"]["Device"], "meter_l2")

    def test_main_inventory_guided_edit_binding_builds_measurement_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=78,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )
            inventory_path = config_path.with_name(f"{config_path.name}.wizard-inventory.ini")

            for profile_id, label, phase, device_id in (
                ("meter_l1_profile", "Meter L1", "L1", "meter_l1"),
                ("meter_l2_profile", "Meter L2", "L2", "meter_l2"),
            ):
                rc_profile = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-profile",
                        "--inventory-profile-id",
                        profile_id,
                        "--inventory-label",
                        label,
                        "--inventory-capability-id",
                        "meter",
                        "--inventory-kind",
                        "meter",
                        "--inventory-adapter-type",
                        "template_meter",
                        "--inventory-supported-phases",
                        phase,
                        "--inventory-measures-power",
                        "--inventory-measures-energy",
                    ]
                )
                self.assertEqual(rc_profile, 0)
                rc_device = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-device",
                        "--inventory-profile-id",
                        profile_id,
                        "--inventory-device-id",
                        device_id,
                        "--inventory-label",
                        label,
                        "--inventory-endpoint",
                        f"http://{device_id}.local",
                    ]
                )
                self.assertEqual(rc_device, 0)

            stdout = io.StringIO()
            with (
                patch(
                    "builtins.input",
                    side_effect=[
                        "",
                        "garage_measurement",
                        "Garage measurement",
                        "L1,L2",
                        "2",
                        "",
                        "y",
                        "3",
                        "",
                        "n",
                    ],
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "guided-edit-binding",
                    ]
                )

            self.assertEqual(rc, 0)
            parser = configparser.ConfigParser()
            parser.read(inventory_path, encoding="utf-8")
            self.assertEqual(parser["Binding:garage_measurement"]["Role"], "measurement")
            self.assertEqual(parser["Binding:garage_measurement"]["PhaseScope"], "L1,L2")
            self.assertEqual(parser["BindingMember:garage_measurement:1"]["Device"], "meter_l1")
            self.assertEqual(parser["BindingMember:garage_measurement:1"]["Phases"], "L1")
            self.assertEqual(parser["BindingMember:garage_measurement:2"]["Device"], "meter_l2")
            self.assertEqual(parser["BindingMember:garage_measurement:2"]["Phases"], "L2")


if __name__ == "__main__":
    unittest.main()

