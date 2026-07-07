# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from contextlib import redirect_stderr
from io import StringIO

from venus_evcharger.bootstrap.wizard_cli_parser import build_parser


class BootstrapWizardCliParserTests(unittest.TestCase):
    def test_defaults_are_explicit(self) -> None:
        parser = build_parser("/etc/evcharger.ini", "/etc/template.ini")
        self.assertEqual(parser.description, "Optional setup wizard for the Venus EV charger config.")
        namespace = parser.parse_args([])
        self.assertEqual(namespace.config_path, "/etc/evcharger.ini")
        self.assertEqual(namespace.template_path, "/etc/template.ini")
        self.assertIsNone(namespace.profile)
        self.assertIsNone(namespace.inventory_action)
        self.assertIsNone(namespace.topology_preset)
        self.assertIsNone(namespace.charger_backend)
        self.assertIsNone(namespace.charger_preset)
        self.assertIsNone(namespace.host)
        self.assertIsNone(namespace.probe_roles)
        self.assertFalse(namespace.digest_auth)
        self.assertFalse(namespace.resume_last)
        self.assertFalse(namespace.clone_current)
        self.assertFalse(namespace.yes)
        self.assertFalse(namespace.force)
        self.assertFalse(namespace.dry_run)
        self.assertFalse(namespace.json)
        self.assertFalse(namespace.live_check)
        self.assertFalse(namespace.apply_energy_merge)
        self.assertFalse(namespace.non_interactive)

    def test_all_argument_groups_parse_to_expected_types(self) -> None:
        namespace = build_parser("/default.ini", "/template.ini").parse_args(
            [
                "--config-path",
                "/tmp/config.ini",
                "--template-path",
                "/tmp/template.ini",
                "--profile",
                "multi_adapter_topology",
                "--inventory-action",
                "add-device",
                "--inventory-path",
                "/tmp/inventory.ini",
                "--inventory-profile-id",
                "profile_1",
                "--inventory-device-id",
                "device_1",
                "--inventory-label",
                "Device",
                "--inventory-endpoint",
                "http://device.local",
                "--inventory-capability-id",
                "meter",
                "--inventory-kind",
                "meter",
                "--inventory-adapter-type",
                "template_meter",
                "--inventory-supported-phases",
                "L1,L2",
                "--inventory-vendor",
                "Vendor",
                "--inventory-model",
                "Model",
                "--inventory-description",
                "Description",
                "--inventory-channel",
                "ch1",
                "--inventory-measures-power",
                "--inventory-measures-energy",
                "--inventory-switching-mode",
                "contactor",
                "--inventory-supports-feedback",
                "--inventory-supports-phase-selection",
                "--inventory-binding-id",
                "measurement",
                "--inventory-binding-role",
                "measurement",
                "--inventory-binding-label",
                "Measurement",
                "--inventory-binding-phase-scope",
                "L1,L2",
                "--inventory-member-phases",
                "L1",
                "--topology-preset",
                "shelly-meter-goe",
                "--charger-backend",
                "goe_charger",
                "--charger-preset",
                "abb-terra-ac-modbus",
                "--host",
                "shared.local",
                "--meter-host",
                "meter.local",
                "--switch-host",
                "switch.local",
                "--charger-host",
                "charger.local",
                "--device-instance",
                "72",
                "--phase",
                "3P",
                "--policy-mode",
                "scheduled",
                "--transport",
                "tcp",
                "--transport-host",
                "modbus.local",
                "--transport-port",
                "1502",
                "--transport-device",
                "/dev/ttyUSB1",
                "--transport-unit-id",
                "7",
                "--digest-auth",
                "--username",
                "user",
                "--password",
                "secret",
                "--import-config",
                "/tmp/import.ini",
                "--resume-last",
                "--clone-current",
                "--yes",
                "--force",
                "--dry-run",
                "--json",
                "--live-check",
                "--probe-role",
                "meter",
                "--probe-role",
                "charger",
                "--request-timeout-seconds",
                "12.5",
                "--cerbo-relay-index",
                "1",
                "--cerbo-relay-contact-mode",
                "NC",
                "--switch-group-phase-layout",
                "P1,P1_P2,P1_P2_P3",
                "--auto-start-surplus-watts",
                "100.5",
                "--auto-stop-surplus-watts",
                "50.25",
                "--auto-min-soc",
                "20",
                "--auto-resume-soc",
                "80",
                "--scheduled-enabled-days",
                "mon,tue",
                "--scheduled-latest-end-time",
                "06:00",
                "--scheduled-night-current-amps",
                "8.5",
                "--energy-recommendation-prefix",
                "AutoEnergySource.hybrid",
                "--energy-recommendation-prefix",
                "AutoEnergySource.victron",
                "--huawei-recommendation-prefix",
                "AutoEnergySource.huawei",
                "--energy-default-usable-capacity-wh",
                "9000",
                "--huawei-usable-capacity-wh",
                "8000",
                "--energy-usable-capacity-wh",
                "hybrid=7000",
                "--energy-usable-capacity-wh",
                "victron=6000",
                "--apply-energy-merge",
                "--non-interactive",
            ]
        )
        self.assertEqual(namespace.config_path, "/tmp/config.ini")
        self.assertEqual(namespace.template_path, "/tmp/template.ini")
        self.assertEqual(namespace.profile, "multi_adapter_topology")
        self.assertEqual(namespace.inventory_action, "add-device")
        self.assertEqual(namespace.inventory_path, "/tmp/inventory.ini")
        self.assertEqual(namespace.inventory_profile_id, "profile_1")
        self.assertEqual(namespace.inventory_device_id, "device_1")
        self.assertEqual(namespace.inventory_label, "Device")
        self.assertEqual(namespace.inventory_endpoint, "http://device.local")
        self.assertEqual(namespace.inventory_capability_id, "meter")
        self.assertEqual(namespace.inventory_kind, "meter")
        self.assertEqual(namespace.inventory_adapter_type, "template_meter")
        self.assertEqual(namespace.inventory_supported_phases, "L1,L2")
        self.assertEqual(namespace.inventory_vendor, "Vendor")
        self.assertEqual(namespace.inventory_model, "Model")
        self.assertEqual(namespace.inventory_description, "Description")
        self.assertEqual(namespace.inventory_channel, "ch1")
        self.assertTrue(namespace.inventory_measures_power)
        self.assertTrue(namespace.inventory_measures_energy)
        self.assertEqual(namespace.inventory_switching_mode, "contactor")
        self.assertTrue(namespace.inventory_supports_feedback)
        self.assertTrue(namespace.inventory_supports_phase_selection)
        self.assertEqual(namespace.inventory_binding_id, "measurement")
        self.assertEqual(namespace.inventory_binding_role, "measurement")
        self.assertEqual(namespace.inventory_binding_label, "Measurement")
        self.assertEqual(namespace.inventory_binding_phase_scope, "L1,L2")
        self.assertEqual(namespace.inventory_member_phases, "L1")
        self.assertEqual(namespace.topology_preset, "shelly-meter-goe")
        self.assertEqual(namespace.charger_backend, "goe_charger")
        self.assertEqual(namespace.charger_preset, "abb-terra-ac-modbus")
        self.assertEqual(namespace.host, "shared.local")
        self.assertEqual(namespace.meter_host, "meter.local")
        self.assertEqual(namespace.switch_host, "switch.local")
        self.assertEqual(namespace.charger_host, "charger.local")
        self.assertEqual(namespace.device_instance, 72)
        self.assertEqual(namespace.phase, "3P")
        self.assertEqual(namespace.policy_mode, "scheduled")
        self.assertEqual(namespace.transport, "tcp")
        self.assertEqual(namespace.transport_host, "modbus.local")
        self.assertEqual(namespace.transport_port, 1502)
        self.assertEqual(namespace.transport_device, "/dev/ttyUSB1")
        self.assertEqual(namespace.transport_unit_id, 7)
        self.assertTrue(namespace.digest_auth)
        self.assertEqual(namespace.username, "user")
        self.assertEqual(namespace.password, "secret")
        self.assertEqual(namespace.import_config, "/tmp/import.ini")
        self.assertTrue(namespace.resume_last)
        self.assertTrue(namespace.clone_current)
        self.assertTrue(namespace.yes)
        self.assertTrue(namespace.force)
        self.assertTrue(namespace.dry_run)
        self.assertTrue(namespace.json)
        self.assertTrue(namespace.live_check)
        self.assertEqual(namespace.probe_roles, ["meter", "charger"])
        self.assertEqual(namespace.request_timeout_seconds, 12.5)
        self.assertEqual(namespace.cerbo_relay_index, 1)
        self.assertEqual(namespace.cerbo_relay_contact_mode, "NC")
        self.assertEqual(namespace.switch_group_phase_layout, "P1,P1_P2,P1_P2_P3")
        self.assertEqual(namespace.auto_start_surplus_watts, 100.5)
        self.assertEqual(namespace.auto_stop_surplus_watts, 50.25)
        self.assertEqual(namespace.auto_min_soc, 20.0)
        self.assertEqual(namespace.auto_resume_soc, 80.0)
        self.assertEqual(namespace.scheduled_enabled_days, "mon,tue")
        self.assertEqual(namespace.scheduled_latest_end_time, "06:00")
        self.assertEqual(namespace.scheduled_night_current_amps, 8.5)
        self.assertEqual(namespace.energy_recommendation_prefix, ["AutoEnergySource.hybrid", "AutoEnergySource.victron"])
        self.assertEqual(namespace.huawei_recommendation_prefix, ["AutoEnergySource.huawei"])
        self.assertEqual(namespace.energy_default_usable_capacity_wh, 9000.0)
        self.assertEqual(namespace.huawei_usable_capacity_wh, 8000.0)
        self.assertEqual(namespace.energy_usable_capacity_wh, ["hybrid=7000", "victron=6000"])
        self.assertTrue(namespace.apply_energy_merge)
        self.assertTrue(namespace.non_interactive)

    def test_choice_sets_accept_every_public_value(self) -> None:
        parser = build_parser("/default.ini", "/template.ini")
        inventory_actions = (
            "show",
            "show-bindings",
            "guided-add-profile",
            "guided-edit-binding",
            "add-device",
            "remove-device",
            "set-endpoint",
            "add-profile",
            "add-capability",
            "set-binding-member",
            "remove-binding-member",
        )
        for action in inventory_actions:
            with self.subTest(inventory_action=action):
                self.assertEqual(parser.parse_args(["--inventory-action", action]).inventory_action, action)
        for kind in ("switch", "meter", "charger"):
            with self.subTest(inventory_kind=kind):
                self.assertEqual(parser.parse_args(["--inventory-kind", kind]).inventory_kind, kind)
        for switching_mode in ("direct", "contactor"):
            with self.subTest(inventory_switching_mode=switching_mode):
                self.assertEqual(
                    parser.parse_args(["--inventory-switching-mode", switching_mode]).inventory_switching_mode,
                    switching_mode,
                )
        for role in ("actuation", "measurement", "charger"):
            with self.subTest(inventory_binding_role=role):
                self.assertEqual(parser.parse_args(["--inventory-binding-role", role]).inventory_binding_role, role)
        for phase in ("L1", "L2", "L3", "3P", "1P"):
            with self.subTest(phase=phase):
                self.assertEqual(parser.parse_args(["--phase", phase]).phase, phase)
        self.assertEqual(parser.parse_args(["--probe-role", "switch"]).probe_roles, ["switch"])
        self.assertEqual(parser.parse_args(["--cerbo-relay-index", "0"]).cerbo_relay_index, 0)
        self.assertEqual(parser.parse_args(["--cerbo-relay-contact-mode", "NO"]).cerbo_relay_contact_mode, "NO")

    def test_choices_and_numeric_types_are_rejected_by_argparse(self) -> None:
        parser = build_parser("/default.ini", "/template.ini")
        invalid_args = (
            ["--profile", "bad"],
            ["--inventory-action", "bad"],
            ["--inventory-kind", "bad"],
            ["--inventory-switching-mode", "bad"],
            ["--inventory-binding-role", "bad"],
            ["--topology-preset", "bad"],
            ["--charger-backend", "bad"],
            ["--charger-preset", "bad"],
            ["--phase", "bad"],
            ["--policy-mode", "bad"],
            ["--transport", "bad"],
            ["--probe-role", "bad"],
            ["--cerbo-relay-index", "2"],
            ["--cerbo-relay-contact-mode", "bad"],
            ["--switch-group-phase-layout", "bad"],
            ["--device-instance", "not-int"],
            ["--transport-port", "not-int"],
            ["--request-timeout-seconds", "not-float"],
            ["--auto-start-surplus-watts", "not-float"],
        )
        for args in invalid_args:
            with self.subTest(args=args):
                with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                    parser.parse_args(args)


if __name__ == "__main__":
    unittest.main()
