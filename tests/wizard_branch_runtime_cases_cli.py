# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_runtime_cases_common import (
    _imported_defaults,
    _namespace,
    io,
    patch,
    redirect_stdout,
    wizard_cli,
)
from venus_evcharger.bootstrap.wizard_cli_non_interactive import non_interactive_cerbo_relay_inputs


class _WizardBranchRuntimeCliCases:
    def test_wizard_cli_helpers_cover_prompt_loops_imports_and_noninteractive_errors(self) -> None:
        with patch("builtins.input", return_value="typed"):
            self.assertEqual(wizard_cli._prompt_text("Prompt", "default"), "typed")
        with patch("builtins.input", return_value=""):
            self.assertEqual(wizard_cli._prompt_text("Prompt", "default"), "default")
        with patch("builtins.input", return_value=""):
            self.assertTrue(wizard_cli.prompt_yes_no("Question", True))
        with patch("builtins.input", return_value="yes"):
            self.assertTrue(wizard_cli.prompt_yes_no("Question", False))

        self.assertEqual(wizard_cli._choice_from_raw("2", ("a", "b")), "b")
        self.assertIsNone(wizard_cli._choice_from_raw("9", ("a", "b")))
        self.assertIsNone(wizard_cli._choice_from_raw("x", ("a", "b")))
        self.assertEqual(wizard_cli._resolved_choice_input("", ("a", "b"), "a"), "a")

        parser = wizard_cli.build_parser("/tmp/config.ini", "/tmp/template.ini")
        namespace = parser.parse_args(
            [
                "--energy-recommendation-prefix",
                "/tmp/energy-rec",
                "--huawei-recommendation-prefix",
                "/tmp/huawei-rec",
                "--huawei-recommendation-prefix",
                "/tmp/huawei-rec-2",
                "--apply-energy-merge",
                "--energy-default-usable-capacity-wh",
                "12288",
                "--huawei-usable-capacity-wh",
                "15360",
                "--energy-usable-capacity-wh",
                "hybrid_a=10240",
            ]
        )
        self.assertEqual(namespace.energy_recommendation_prefix, ["/tmp/energy-rec"])
        self.assertEqual(namespace.huawei_recommendation_prefix, ["/tmp/huawei-rec", "/tmp/huawei-rec-2"])
        self.assertTrue(namespace.apply_energy_merge)
        self.assertEqual(namespace.energy_default_usable_capacity_wh, 12288.0)
        self.assertEqual(namespace.huawei_usable_capacity_wh, 15360.0)
        self.assertEqual(namespace.energy_usable_capacity_wh, ["hybrid_a=10240"])
        namespace = parser.parse_args(["--resume-last", "--config-path", "/tmp/missing.ini"])
        with self.assertRaisesRegex(ValueError, "no prior wizard result exists"):
            wizard_cli.resolve_imported_defaults(namespace)

        namespace = parser.parse_args(["--clone-current", "--config-path", "/tmp/missing.ini"])
        with self.assertRaisesRegex(ValueError, "config does not exist"):
            wizard_cli.resolve_imported_defaults(namespace)

        with patch("builtins.input", side_effect=["9", "2"]):
            self.assertEqual(wizard_cli._prompt_choice("Pick", ("a", "b")), "b")

        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_yes_no", return_value=True):
            self.assertEqual(wizard_cli._prompt_password("imported-secret"), "imported-secret")
        with patch("venus_evcharger.bootstrap.wizard_cli.getpass.getpass", return_value="typed-secret"):
            self.assertEqual(wizard_cli._prompt_password(""), "typed-secret")

        imported = _imported_defaults()
        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_yes_no", return_value=True):
            self.assertTrue(wizard_cli._interactive_digest_auth(_namespace(), imported))
        self.assertTrue(wizard_cli._interactive_digest_auth(_namespace(digest_auth=True), imported))
        self.assertEqual(wizard_cli._interactive_backend_choice("advanced_manual", "template_charger"), "template_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", return_value="goe_charger"):
            self.assertEqual(wizard_cli._interactive_backend_choice("native_device", None), "goe_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", return_value="simpleevse_charger"):
            self.assertEqual(wizard_cli._interactive_backend_choice("hybrid_topology", None), "simpleevse_charger")
        self.assertEqual(
            wizard_cli._interactive_backend(_namespace(charger_backend="modbus_charger"), "native_device", imported, None),
            "modbus_charger",
        )
        self.assertEqual(
            wizard_cli._interactive_charger_preset(_namespace(charger_preset="abb-terra-ac-modbus"), imported, "modbus_charger"),
            "abb-terra-ac-modbus",
        )
        with self.assertRaisesRegex(ValueError, "is not supported for backend goe_charger"):
            wizard_cli._interactive_charger_preset(_namespace(charger_preset="abb-terra-ac-modbus"), imported, "goe_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_optional_choice", return_value=None):
            self.assertIsNone(
                wizard_cli._interactive_charger_preset(
                    _namespace(),
                    _imported_defaults(charger_preset="unsupported"),
                    "modbus_charger",
                )
            )
        self.assertEqual(
            wizard_cli._non_interactive_charger_preset(
                _namespace(charger_preset="abb-terra-ac-modbus"),
                _imported_defaults(),
                "goe_charger",
            ),
            "abb-terra-ac-modbus",
        )
        with self.assertRaisesRegex(ValueError, "is not supported for backend goe_charger"):
            wizard_cli._non_interactive_charger_preset(
                _namespace(charger_preset="not-a-real-preset"),
                _imported_defaults(),
                "goe_charger",
            )
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", return_value="none"):
            self.assertIsNone(
                wizard_cli._prompt_optional_choice(
                    "Choose",
                    ("none", "abb-terra-ac-modbus"),
                    {"none": "None", "abb-terra-ac-modbus": "ABB"},
                    None,
                )
            )
        self.assertEqual(
            wizard_cli._interactive_transport_inputs("goe_charger", None, "goe.local", imported),
            ("serial_rtu", "goe.local", 502, "/dev/ttyUSB0", 1),
        )
        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_transport_inputs", return_value=("tcp", "modbus.local", 1502, "/dev/ttyUSB0", 3)):
            self.assertEqual(
                wizard_cli._interactive_transport_inputs("modbus_charger", "abb-terra-ac-modbus", "modbus.local", imported),
                ("tcp", "modbus.local", 1502, "/dev/ttyUSB0", 3),
            )
        self.assertEqual(wizard_cli._interactive_topology_preset(_namespace(), _imported_defaults(), "native_device"), None)
        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_topology_preset", return_value="template-stack"):
            self.assertEqual(
                wizard_cli._interactive_topology_preset(_namespace(), _imported_defaults(), "multi_adapter_topology"),
                "template-stack",
            )
        self.assertEqual(wizard_cli._interactive_cerbo_relay_inputs(_namespace(), None), (0, "NO"))
        self.assertEqual(
            wizard_cli._interactive_cerbo_relay_inputs(
                _namespace(cerbo_relay_index=1, cerbo_relay_contact_mode="nc"),
                "shelly-meter-cerbo-relay",
            ),
            (1, "NC"),
        )
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", side_effect=["1", "NC"]):
            self.assertEqual(
                wizard_cli._interactive_cerbo_relay_inputs(_namespace(), "shelly-meter-cerbo-relay"),
                (1, "NC"),
            )
        self.assertEqual(wizard_cli._interactive_username(_namespace(username="user"), imported, True), "user")
        self.assertEqual(wizard_cli._interactive_password(_namespace(password="secret"), imported, True), "secret")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_text", return_value="77"):
            self.assertEqual(wizard_cli._interactive_device_instance(_namespace(), imported), 77)

        stdout = io.StringIO()
        with (
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_profile", return_value="multi_adapter_topology"),
            patch("venus_evcharger.bootstrap.wizard_cli._prompt_text", side_effect=["shared.local", "81"]),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_topology_preset", return_value="template-stack"),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_backend", return_value="template_charger"),
            patch("venus_evcharger.bootstrap.wizard_cli.prompt_role_hosts", return_value=("meter.local", "switch.local", "charger.local")),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_transport_inputs", return_value=("serial_rtu", "shared.local", 502, "/dev/ttyUSB0", 1)),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_auth_inputs", return_value=(False, "", "")),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_policy_mode", return_value="manual"),
            patch("venus_evcharger.bootstrap.wizard_cli.prompt_preset_specific_defaults", return_value=(None, "P1,P1_P2,P1_P2_P3")),
            patch("venus_evcharger.bootstrap.wizard_cli.prompt_policy_defaults", return_value=(None, None, None, None, None, None, None)),
            patch("venus_evcharger.bootstrap.wizard_cli.role_prompt_intro", return_value="Intro text"),
            redirect_stdout(stdout),
        ):
            answers = wizard_cli._interactive_answers(_namespace(phase="L1"), imported)
        self.assertEqual(answers.device_instance, 81)
        self.assertIn("Intro text", stdout.getvalue())

        with self.assertRaisesRegex(ValueError, "--profile is required"):
            wizard_cli._non_interactive_profile(_namespace(), _imported_defaults())
        with self.assertRaisesRegex(ValueError, "Unsupported profile"):
            wizard_cli._non_interactive_profile(_namespace(profile="bad"), _imported_defaults())
        with self.assertRaisesRegex(ValueError, "Unsupported policy mode"):
            wizard_cli._non_interactive_policy_mode(_namespace(policy_mode="bad"), _imported_defaults())
        with self.assertRaisesRegex(ValueError, "Unsupported charger backend"):
            wizard_cli._non_interactive_backend(_namespace(charger_backend="bad"), _imported_defaults(), "native_device", None)
        self.assertTrue(wizard_cli._non_interactive_digest_auth(_namespace(digest_auth=True), _imported_defaults()))
        self.assertEqual(non_interactive_cerbo_relay_inputs(_namespace(), None), (0, "NO"))
        self.assertEqual(
            non_interactive_cerbo_relay_inputs(
                _namespace(cerbo_relay_index=1, cerbo_relay_contact_mode="nc"),
                "shelly-meter-cerbo-relay",
            ),
            (1, "NC"),
        )
        with self.assertRaisesRegex(ValueError, "--cerbo-relay-index"):
            non_interactive_cerbo_relay_inputs(_namespace(cerbo_relay_index=3), "shelly-meter-cerbo-relay")
        with self.assertRaisesRegex(ValueError, "--cerbo-relay-contact-mode"):
            non_interactive_cerbo_relay_inputs(
                _namespace(cerbo_relay_contact_mode="bad"),
                "shelly-meter-cerbo-relay",
            )
