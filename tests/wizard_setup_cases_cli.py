# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path, main
from venus_evcharger.bootstrap.wizard_cli import build_answers, build_parser


class _WizardSetupCliCases:
    def test_main_dry_run_emits_json_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "multi_adapter_topology",
                        "--host",
                        "adapter.local",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertFalse(config_path.exists())
            self.assertEqual(payload["profile"], "multi_adapter_topology")
            self.assertEqual(payload["validation"]["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_main_non_interactive_refuses_existing_files_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("legacy\n", encoding="utf-8")

            rc = main(
                [
                    "--non-interactive",
                    "--config-path",
                    str(config_path),
                    "--profile",
                    "simple_relay",
                    "--host",
                    "192.0.2.44",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "legacy\n")

    def test_main_non_interactive_force_overwrites_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("legacy\n", encoding="utf-8")

            rc = main(
                [
                    "--non-interactive",
                    "--force",
                    "--config-path",
                    str(config_path),
                    "--profile",
                    "simple_relay",
                    "--host",
                    "192.0.2.55",
                ]
            )

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            self.assertEqual(rc, 0)
            self.assertEqual(parser["DEFAULT"]["Host"], "192.0.2.55")
            self.assertTrue(any(path.name.startswith("config.ini.wizard-backup-") for path in config_path.parent.iterdir()))

    def test_main_import_config_seeds_non_interactive_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.ini"
            configure_wallbox(
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
                config_path=source_path,
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
                        "--import-config",
                        str(source_path),
                        "--config-path",
                        str(Path(temp_dir) / "preview.ini"),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["imported_from"], str(source_path))
            self.assertEqual(payload["profile"], "multi_adapter_topology")
            self.assertEqual(payload["topology_preset"], "shelly-io-modbus-charger")
            self.assertEqual(payload["charger_backend"], "modbus_charger")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["role_hosts"], {"meter": "192.0.2.20", "switch": "192.0.2.21"})

    def test_main_import_config_recognizes_goe_switch_group_topology_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="goe.local",
                    meter_host_input="meter.local",
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=70,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="shelly-meter-goe-switch-group",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=source_path,
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
                        "--import-config",
                        str(source_path),
                        "--config-path",
                        str(Path(temp_dir) / "preview.ini"),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["topology_preset"], "shelly-meter-goe-switch-group")
            self.assertEqual(payload["charger_backend"], "goe_charger")
            self.assertEqual(
                payload["role_hosts"],
                {"charger": "http://charger.local", "meter": "meter.local", "switch": "http://switch.local"},
            )

    def test_main_dry_run_uses_role_specific_host_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "multi_adapter_topology",
                        "--topology-preset",
                        "template-meter-goe-switch-group",
                        "--meter-host",
                        "meter.local",
                        "--switch-host",
                        "http://switch.local",
                        "--charger-host",
                        "goe.local",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(
                payload["role_hosts"],
                {"charger": "goe.local", "meter": "meter.local", "switch": "http://switch.local"},
            )

    def test_main_clone_current_reuses_existing_config_as_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
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
                        "--clone-current",
                        "--config-path",
                        str(config_path),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["imported_from"], str(config_path))
            self.assertEqual(payload["profile"], "simple_relay")
            self.assertEqual(payload["config_path"], str(config_path))

    def test_build_answers_interactive_uses_hidden_password_prompt(self) -> None:
        parser = build_parser("/tmp/config.ini", str(default_template_path()))
        namespace = parser.parse_args(
            ["--profile", "simple_relay", "--phase", "L1", "--policy-mode", "manual", "--device-instance", "60"]
        )
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text", side_effect=["192.0.2.50", "admin"]),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_yes_no", return_value=True),
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.getpass.getpass", return_value="very-secret") as password_prompt,
        ):
            answers, _ = build_answers(namespace)

        self.assertTrue(answers.digest_auth)
        self.assertEqual(answers.username, "admin")
        self.assertEqual(answers.password, "very-secret")
        password_prompt.assert_called_once_with("Password: ")

    def test_main_dry_run_reports_optional_live_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with (
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime.live_check_rendered_setup",
                    return_value={
                        "ok": True,
                        "checked_roles": ("charger",),
                        "roles": {"charger": {"status": "ok", "payload": {"type": "goe_charger"}}},
                    },
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--live-check",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "native_device",
                        "--charger-backend",
                        "goe_charger",
                        "--host",
                        "goe.local",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(
                payload["live_check"],
                {
                    "checked_roles": ["charger"],
                    "ok": True,
                    "roles": {"charger": {"payload": {"type": "goe_charger"}, "status": "ok"}},
                },
            )
            self.assertEqual(payload["topology_config"]["topology"]["type"], "native_device")
            self.assertEqual(payload["topology_config"]["charger"]["type"], "goe_charger")
