# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_venus_evcharger_setup_wizard_extensions_support import *  # noqa: F401,F403

class _TestShellyWallboxSetupWizardExtensionsPart1:
    def test_configure_wallbox_applies_policy_tuning_and_goe_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=71,
                    phase="3P",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                    request_timeout_seconds=4.5,
                    auto_start_surplus_watts=2100.0,
                    auto_stop_surplus_watts=1650.0,
                    auto_min_soc=35.0,
                    auto_resume_soc=39.0,
                    scheduled_enabled_days="Mon,Wed,Fri",
                    scheduled_latest_end_time="07:15",
                    scheduled_night_current_amps=8.0,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertEqual(parser["DEFAULT"]["AutoStartSurplusWatts"], "2100")
            self.assertEqual(parser["DEFAULT"]["AutoStopSurplusWatts"], "1650")
            self.assertEqual(parser["DEFAULT"]["AutoMinSoc"], "35")
            self.assertEqual(parser["DEFAULT"]["AutoResumeSoc"], "39")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledEnabledDays"], "Mon,Wed,Fri")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledLatestEndTime"], "07:15")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledNightCurrentAmps"], "8")
            self.assertIn("RequestTimeoutSeconds=4.5\n", charger_text)
            self.assertEqual(result.answer_defaults["request_timeout_seconds"], 4.5)

    def test_configure_wallbox_keeps_switch_group_phase_layout_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="shared.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=72,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="goe-external-switch-group",
                    charger_backend="goe_charger",
                    switch_group_supported_phase_selections="P1,P1_P2_P3",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            switch_group = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            self.assertIn("SupportedPhaseSelections=P1,P1_P2_P3\n", switch_group)
            self.assertTrue(any("1P -> 3P" in warning for warning in result.warnings))
            self.assertTrue(any("shared primary endpoint" in warning for warning in result.warnings))

    def test_main_resume_last_uses_previous_wizard_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=73,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="wizard",
                    password="secret-value",
                    charger_backend="goe_charger",
                    request_timeout_seconds=3.5,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--resume-last",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertTrue(payload["imported_from"].endswith(".wizard-result.json"))
            self.assertEqual(payload["profile"], "native_device")
            self.assertEqual(payload["charger_backend"], "goe_charger")
            self.assertEqual(payload["answer_defaults"]["password_present"], False)
            self.assertIn("device_inventory", payload)
            self.assertTrue(payload["device_inventory"]["profiles"])

    def test_main_probe_role_limits_live_check_to_selected_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with (
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime._live_check_rendered_setup",
                    return_value={
                        "ok": True,
                        "checked_roles": ("charger",),
                        "roles": {
                            "meter": {"status": "skipped", "reason": "not requested"},
                            "switch": {"status": "skipped", "reason": "not requested"},
                            "charger": {"status": "ok", "payload": {"type": "goe_charger"}},
                        },
                    },
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "native_device",
                        "--charger-backend",
                        "goe_charger",
                        "--host",
                        "goe.local",
                        "--probe-role",
                        "charger",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["live_check"]["checked_roles"], ["charger"])
            self.assertEqual(payload["live_check"]["roles"]["meter"]["status"], "skipped")
            self.assertEqual(payload["live_check"]["roles"]["charger"]["status"], "ok")

    def test_main_inventory_show_and_add_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.168.1.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=74,
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

            show_stdout = io.StringIO()
            with redirect_stdout(show_stdout):
                show_rc = main(
                    [
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "show",
                    ]
                )
            show_payload = json.loads(show_stdout.getvalue())
            self.assertEqual(show_rc, 0)
            self.assertEqual(len(show_payload["devices"]), 1)

            add_stdout = io.StringIO()
            with redirect_stdout(add_stdout):
                add_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-device",
                        "--inventory-profile-id",
                        "switch_shelly_contactor_switch",
                        "--inventory-device-id",
                        "switch_aux",
                        "--inventory-label",
                        "Aux switch",
                        "--inventory-endpoint",
                        "http://192.168.1.45",
                    ]
                )
            add_payload = json.loads(add_stdout.getvalue())
            self.assertEqual(add_rc, 0)
            self.assertEqual(add_payload["device_id"], "switch_aux")
            self.assertEqual(len(add_payload["inventory"]["devices"]), 2)

    def test_main_inventory_set_endpoint_and_remove_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=75,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            endpoint_stdout = io.StringIO()
            with redirect_stdout(endpoint_stdout):
                endpoint_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "set-endpoint",
                        "--inventory-device-id",
                        "charger_device",
                        "--inventory-endpoint",
                        "http://updated.local",
                    ]
                )
            endpoint_payload = json.loads(endpoint_stdout.getvalue())
            self.assertEqual(endpoint_rc, 0)
            self.assertEqual(endpoint_payload["endpoint"], "http://updated.local")

            remove_stdout = io.StringIO()
            with redirect_stdout(remove_stdout):
                remove_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "remove-device",
                        "--inventory-device-id",
                        "charger_device",
                    ]
                )
            remove_payload = json.loads(remove_stdout.getvalue())
            self.assertEqual(remove_rc, 0)
            self.assertEqual(remove_payload["device_id"], "charger_device")
            self.assertEqual(len(remove_payload["inventory"]["devices"]), 0)

