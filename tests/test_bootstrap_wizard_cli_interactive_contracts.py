# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from tests.wizard_branch_runtime_cases_common import _imported_defaults, _namespace
from venus_evcharger.bootstrap import wizard_cli_interactive as interactive


class BootstrapWizardCliInteractiveContractTests(unittest.TestCase):
    def test_profile_backend_and_policy_contracts(self) -> None:
        imported = _imported_defaults(profile="advanced_manual", policy_mode="auto")
        self.assertEqual(interactive._interactive_profile(_namespace(), imported), "advanced_manual")
        self.assertEqual(interactive._interactive_profile(_namespace(profile="simple_relay"), imported), "simple_relay")
        with self.assertRaises(ValueError) as bad_profile:
            interactive._interactive_profile(_namespace(profile="bad"), imported)
        self.assertEqual(str(bad_profile.exception), "Unsupported profile: bad")

        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="multi_adapter_topology") as prompt:
            self.assertEqual(interactive._interactive_profile(_namespace(), _imported_defaults()), "multi_adapter_topology")
        prompt.assert_called_once_with(
            "Choose the setup topology:",
            interactive.PROFILE_VALUES,
            {key: value for key, value in interactive.PROFILE_LABELS},
            "simple_relay",
        )
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value=None):
            self.assertEqual(interactive._interactive_profile(_namespace(), _imported_defaults()), "simple_relay")

        self.assertIsNone(interactive._backend_prompt_spec("advanced_manual"))
        self.assertEqual(
            interactive._backend_prompt_spec("native_device"),
            (
                (
                    "goe_charger",
                    "simpleevse_charger",
                    "smartevse_charger",
                    "template_charger",
                    "modbus_charger",
                ),
                "goe_charger",
            ),
        )
        self.assertEqual(
            interactive._backend_prompt_spec("hybrid_topology"),
            (("simpleevse_charger", "smartevse_charger"), "simpleevse_charger"),
        )
        self.assertEqual(interactive._interactive_backend_choice("advanced_manual", "template_charger"), "template_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="goe_charger") as prompt:
            self.assertEqual(interactive._interactive_backend_choice("native_device", None), "goe_charger")
        prompt.assert_called_once_with(
            "Choose the charger backend:",
            (
                "goe_charger",
                "simpleevse_charger",
                "smartevse_charger",
                "template_charger",
                "modbus_charger",
            ),
            default="goe_charger",
        )
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="modbus_charger"):
            self.assertEqual(interactive._interactive_backend_choice("native_device", None), "modbus_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="bad"):
            with self.assertRaises(ValueError) as bad_backend_choice:
                interactive._interactive_backend_choice("native_device", None)
        self.assertEqual(str(bad_backend_choice.exception), "Unsupported charger backend: bad")
        self.assertEqual(
            interactive._interactive_backend(
                _namespace(charger_backend="modbus_charger"),
                "native_device",
                imported,
                "ignored",
            ),
            "modbus_charger",
        )
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.default_backend", return_value="goe_charger") as default_backend,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_backend_choice", return_value="smartevse_charger") as backend_choice,
        ):
            self.assertEqual(interactive._interactive_backend(_namespace(), "native_device", imported, "ignored"), "smartevse_charger")
        default_backend.assert_called_once_with("native_device", imported)
        backend_choice.assert_called_once_with("native_device", "goe_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive.default_backend", return_value="bad"):
            with self.assertRaises(ValueError) as bad_backend:
                interactive._interactive_backend(_namespace(), "native_device", imported, None)
        self.assertEqual(str(bad_backend.exception), "Unsupported charger backend: bad")

        self.assertEqual(interactive._interactive_policy_mode(_namespace(policy_mode="scheduled"), imported), "scheduled")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="manual") as prompt:
            self.assertEqual(interactive._interactive_policy_mode(_namespace(), imported), "manual")
        prompt.assert_called_once_with("Choose the initial policy mode:", ("manual", "auto", "scheduled"), default="auto")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value=None) as prompt:
            self.assertEqual(interactive._interactive_policy_mode(_namespace(), _imported_defaults()), "manual")
        prompt.assert_called_once_with("Choose the initial policy mode:", ("manual", "auto", "scheduled"), default="manual")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="bad"):
            with self.assertRaises(ValueError) as bad_policy:
                interactive._interactive_policy_mode(_namespace(), _imported_defaults())
        self.assertEqual(str(bad_policy.exception), "Unsupported policy mode: bad")

    def test_auth_device_phase_and_relay_contracts(self) -> None:
        imported = _imported_defaults(digest_auth=True, username="imported-user", password="imported-pass", device_instance=61, phase="L2")
        self.assertTrue(interactive._interactive_digest_auth(_namespace(digest_auth=True), imported))
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_yes_no", return_value=False) as yes_no:
            self.assertFalse(interactive._interactive_digest_auth(_namespace(), imported))
        yes_no.assert_called_once_with("Does this setup require authentication?", True)
        self.assertFalse(interactive._interactive_digest_auth(_namespace(digest_auth=None), imported))

        self.assertEqual(interactive._interactive_username(_namespace(username="cli-user"), imported, True), "cli-user")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text", return_value="typed-user") as prompt:
            self.assertEqual(interactive._interactive_username(_namespace(), imported, True), "typed-user")
        prompt.assert_called_once_with("Username", "imported-user")
        self.assertEqual(interactive._interactive_username(_namespace(), imported, False), "imported-user")
        self.assertEqual(interactive._interactive_username(_namespace(), _imported_defaults(), False), "")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text", return_value="admin") as prompt:
            self.assertEqual(interactive._interactive_username(_namespace(), _imported_defaults(), True), "admin")
        prompt.assert_called_once_with("Username", "admin")

        self.assertEqual(interactive._interactive_password(_namespace(password="cli-pass"), imported, True), "cli-pass")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_password", return_value="typed-pass") as prompt:
            self.assertEqual(interactive._interactive_password(_namespace(), imported, True), "typed-pass")
        prompt.assert_called_once_with("imported-pass")
        self.assertEqual(interactive._interactive_password(_namespace(), imported, False), "imported-pass")

        self.assertEqual(interactive._interactive_device_instance(_namespace(device_instance=77), imported), 77)
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text", return_value="81") as prompt:
            self.assertEqual(interactive._interactive_device_instance(_namespace(), imported), 81)
        prompt.assert_called_once_with("DeviceInstance", "61")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text", return_value="60") as prompt:
            self.assertEqual(interactive._interactive_device_instance(_namespace(), _imported_defaults()), 60)
        prompt.assert_called_once_with("DeviceInstance", "60")

        self.assertEqual(interactive._interactive_phase(_namespace(phase="L3"), imported), "L3")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="3P") as prompt:
            self.assertEqual(interactive._interactive_phase(_namespace(), imported), "3P")
        prompt.assert_called_once_with("Choose the phase baseline:", ("L1", "L2", "L3", "3P"), default="L2")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", return_value="L1") as prompt:
            self.assertEqual(interactive._interactive_phase(_namespace(), _imported_defaults()), "L1")
        prompt.assert_called_once_with("Choose the phase baseline:", ("L1", "L2", "L3", "3P"), default="L1")

        self.assertEqual(interactive._interactive_cerbo_relay_inputs(_namespace(), None), (0, "NO"))
        self.assertEqual(
            interactive._interactive_cerbo_relay_inputs(
                _namespace(cerbo_relay_index=1, cerbo_relay_contact_mode="nc"),
                "shelly-meter-cerbo-relay",
            ),
            (1, "NC"),
        )
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_choice", side_effect=["1", "NC"]) as prompt:
            self.assertEqual(interactive._interactive_cerbo_relay_inputs(_namespace(), "shelly-meter-cerbo-relay"), (1, "NC"))
        self.assertEqual(
            prompt.mock_calls,
            [
                call("Choose the Cerbo GX relay:", ("0", "1"), {"0": "Relay 1", "1": "Relay 2"}, "0"),
                call(
                    "Which contact is wired?",
                    ("NO", "NC"),
                    {"NO": "NO, normally open, recommended fail-off", "NC": "NC, normally closed"},
                    "NO",
                ),
            ],
        )

    def test_topology_transport_and_charger_preset_contracts(self) -> None:
        imported = _imported_defaults(topology_preset="template-stack", charger_preset="cfos-power-brain-modbus")
        self.assertEqual(interactive._interactive_topology_preset(_namespace(), imported, "multi_adapter_topology"), "template-stack")
        self.assertEqual(interactive._interactive_topology_preset(_namespace(topology_preset="native"), imported, "native_device"), "native")
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_topology_preset", return_value="template-stack") as prompt:
            self.assertEqual(
                interactive._interactive_topology_preset(_namespace(), _imported_defaults(), "multi_adapter_topology"),
                "template-stack",
            )
        prompt.assert_called_once_with(interactive._prompt_choice, "template-stack")

        self.assertEqual(
            interactive._interactive_transport_inputs("goe_charger", None, "goe.local", imported),
            ("serial_rtu", "goe.local", 502, "/dev/ttyUSB0", 1),
        )
        with patch(
            "venus_evcharger.bootstrap.wizard_cli_interactive.prompt_transport_inputs",
            return_value=("tcp", "modbus.local", 1502, "/dev/ttyUSB0", 3),
        ) as prompt:
            self.assertEqual(
                interactive._interactive_transport_inputs("modbus_charger", "abb-terra-ac-modbus", "host.local", imported),
                ("tcp", "modbus.local", 1502, "/dev/ttyUSB0", 3),
            )
        prompt.assert_called_once_with(
            "modbus_charger",
            "abb-terra-ac-modbus",
            "host.local",
            imported,
            prompt_choice=interactive._prompt_choice,
            prompt_text=interactive._prompt_text,
        )

        self.assertEqual(interactive._resolved_backend(None, "abb-terra-ac-modbus", "template_charger"), "modbus_charger")
        self.assertEqual(interactive._resolved_backend(None, None, "template_charger"), "template_charger")
        self.assertEqual(
            interactive._charger_preset_labels()["cfos-power-brain-modbus"],
            "cFos Power Brain over Modbus",
        )
        self.assertEqual(
            interactive._interactive_charger_preset(
                _namespace(charger_preset="abb-terra-ac-modbus"),
                imported,
                "modbus_charger",
            ),
            "abb-terra-ac-modbus",
        )
        with self.assertRaises(ValueError) as unsupported:
            interactive._interactive_charger_preset(
                _namespace(charger_preset="abb-terra-ac-modbus"),
                imported,
                "goe_charger",
            )
        self.assertEqual(
            str(unsupported.exception),
            "--charger-preset abb-terra-ac-modbus is not supported for backend goe_charger",
        )
        with self.assertRaises(ValueError) as unsupported_none:
            interactive._validated_namespace_charger_preset("abb-terra-ac-modbus", (), None)
        self.assertEqual(
            str(unsupported_none.exception),
            "--charger-preset abb-terra-ac-modbus is not supported for backend none",
        )
        self.assertIsNone(interactive._interactive_charger_preset(_namespace(), imported, "goe_charger"))
        with patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_optional_choice", return_value="cfos-power-brain-modbus") as prompt:
            self.assertEqual(interactive._interactive_charger_preset(_namespace(), imported, "modbus_charger"), "cfos-power-brain-modbus")
        prompt.assert_called_once_with(
            "Choose an optional device preset:",
            ("none", "abb-terra-ac-modbus", "cfos-power-brain-modbus", "openwb-modbus-secondary"),
            {
                "none": "Generic backend mapping",
                "abb-terra-ac-modbus": "ABB Terra AC over Modbus",
                "cfos-power-brain-modbus": "cFos Power Brain over Modbus",
                "openwb-modbus-secondary": "openWB secondary over Modbus",
            },
            "cfos-power-brain-modbus",
        )

    def test_interactive_answers_orchestrates_all_fields(self) -> None:
        namespace = _namespace(phase="L1")
        imported = _imported_defaults()
        stdout = Mock()
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_profile", return_value="multi_adapter_topology") as profile,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text", return_value="shared.local") as prompt_text,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_topology_preset", return_value="template-stack") as topology,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_backend", return_value="template_charger") as backend,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_charger_preset", return_value="abb-terra-ac-modbus") as preset,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._resolved_backend", return_value="template_charger") as resolved_backend,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.role_prompt_intro", return_value="Intro text") as intro,
            patch("builtins.print", stdout),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_role_hosts", return_value=("meter.local", "switch.local", "charger.local")) as role_hosts,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.resolved_primary_host", return_value="primary.local") as primary_host,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_transport_inputs", return_value=("tcp", "primary.local", 1502, "/dev/ttyUSB9", 7)) as transport,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_auth_inputs", return_value=(False, "", "")) as auth,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_policy_mode", return_value="manual") as policy,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_preset_specific_defaults", return_value=(12.5, "P1")) as preset_defaults,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_cerbo_relay_inputs", return_value=(1, "NC")) as relay,
            patch(
                "venus_evcharger.bootstrap.wizard_cli_interactive.prompt_policy_defaults",
                return_value=(100.0, 50.0, 20.0, 80.0, "mon,tue", "06:00", 6.0),
            ) as policy_defaults,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_device_instance", return_value=60) as device_instance,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_phase", return_value="L1") as phase,
        ):
            answers = interactive.interactive_answers(namespace, imported)

        self.assertEqual(answers.profile, "multi_adapter_topology")
        self.assertEqual(answers.host_input, "primary.local")
        self.assertEqual(answers.meter_host_input, "meter.local")
        self.assertEqual(answers.switch_host_input, "switch.local")
        self.assertEqual(answers.charger_host_input, "charger.local")
        self.assertEqual(answers.charger_backend, "template_charger")
        self.assertEqual(answers.charger_preset, "abb-terra-ac-modbus")
        self.assertEqual(answers.policy_mode, "manual")
        self.assertEqual(answers.topology_preset, "template-stack")
        self.assertEqual(answers.request_timeout_seconds, 12.5)
        self.assertEqual(answers.switch_group_supported_phase_selections, "P1")
        self.assertEqual(answers.cerbo_relay_index, 1)
        self.assertEqual(answers.cerbo_relay_contact_mode, "NC")
        self.assertEqual(answers.auto_start_surplus_watts, 100.0)
        self.assertEqual(answers.auto_stop_surplus_watts, 50.0)
        self.assertEqual(answers.auto_min_soc, 20.0)
        self.assertEqual(answers.auto_resume_soc, 80.0)
        self.assertEqual(answers.scheduled_enabled_days, "mon,tue")
        self.assertEqual(answers.scheduled_latest_end_time, "06:00")
        self.assertEqual(answers.scheduled_night_current_amps, 6.0)
        self.assertEqual(answers.transport_kind, "tcp")
        self.assertEqual(answers.transport_host, "primary.local")
        self.assertEqual(answers.transport_port, 1502)
        self.assertEqual(answers.transport_device, "/dev/ttyUSB9")
        self.assertEqual(answers.transport_unit_id, 7)
        profile.assert_called_once_with(namespace, imported)
        prompt_text.assert_called_once_with("Primary host or IP", "192.168.1.50")
        topology.assert_called_once_with(namespace, imported, "multi_adapter_topology")
        backend.assert_called_once_with(namespace, "multi_adapter_topology", imported, "template-stack")
        preset.assert_called_once_with(namespace, imported, "template_charger")
        resolved_backend.assert_called_once_with("template-stack", "abb-terra-ac-modbus", "template_charger")
        intro.assert_called_once_with("multi_adapter_topology", "template-stack")
        stdout.assert_called_once_with("Intro text")
        role_hosts.assert_called_once_with(
            namespace,
            imported,
            "multi_adapter_topology",
            "template-stack",
            "shared.local",
            prompt_text=prompt_text,
        )
        primary_host.assert_called_once_with(namespace, imported, "meter.local", "switch.local", "charger.local")
        transport.assert_called_once_with("template_charger", "abb-terra-ac-modbus", "primary.local", imported)
        auth.assert_called_once_with(namespace, imported)
        policy.assert_called_once_with(namespace, imported)
        preset_defaults.assert_called_once_with(
            namespace,
            imported,
            profile="multi_adapter_topology",
            backend="template_charger",
            topology_preset="template-stack",
            charger_preset="abb-terra-ac-modbus",
            prompt_choice=interactive._prompt_choice,
            prompt_text=prompt_text,
        )
        relay.assert_called_once_with(namespace, "template-stack")
        policy_defaults.assert_called_once_with("manual", imported, namespace, prompt_text=prompt_text)
        device_instance.assert_called_once_with(namespace, imported)
        phase.assert_called_once_with(namespace, imported)

    def test_interactive_answers_prefers_namespace_host_without_prompting(self) -> None:
        namespace = _namespace(host="cli.local")
        imported = _imported_defaults(host_input="imported.local")
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_profile", return_value="multi_adapter_topology"),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._prompt_text") as prompt_text,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_topology_preset", return_value="template-stack"),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_backend", return_value="template_charger"),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_charger_preset", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._resolved_backend", return_value="template_charger"),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.role_prompt_intro", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_role_hosts", return_value=("meter.local", "switch.local", "charger.local")) as role_hosts,
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.resolved_primary_host", return_value="primary.local"),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_transport_inputs", return_value=("serial_rtu", "primary.local", 502, "/dev/ttyUSB0", 1)),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_auth_inputs", return_value=(False, "", "")),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_policy_mode", return_value="manual"),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive.prompt_preset_specific_defaults", return_value=(None, None)),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_cerbo_relay_inputs", return_value=(0, "NO")),
            patch(
                "venus_evcharger.bootstrap.wizard_cli_interactive.prompt_policy_defaults",
                return_value=(None, None, None, None, None, None, None),
            ),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_device_instance", return_value=60),
            patch("venus_evcharger.bootstrap.wizard_cli_interactive._interactive_phase", return_value="L1"),
        ):
            interactive.interactive_answers(namespace, imported)

        prompt_text.assert_not_called()
        self.assertEqual(role_hosts.call_args.args[4], "cli.local")


if __name__ == "__main__":
    unittest.main()
