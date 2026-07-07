# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.wizard_branch_runtime_cases_common import _imported_defaults, _namespace
from venus_evcharger.bootstrap import wizard_cli_non_interactive as noninteractive


class BootstrapWizardCliNonInteractiveContractTests(unittest.TestCase):
    def test_scalar_normalizers_contract(self) -> None:
        imported = _imported_defaults(
            profile="native_device",
            policy_mode="scheduled",
            digest_auth=True,
            topology_preset="template-stack",
            charger_backend="goe_charger",
            device_instance=61,
            phase="L2",
            username="imported-user",
        )
        self.assertEqual(noninteractive.non_interactive_profile(_namespace(), imported), "native_device")
        with self.assertRaises(ValueError) as missing_profile:
            noninteractive.non_interactive_profile(_namespace(), _imported_defaults())
        self.assertEqual(
            str(missing_profile.exception),
            "--profile is required in --non-interactive mode unless --import-config/--clone-current provides one",
        )
        with self.assertRaises(ValueError) as bad_profile:
            noninteractive.non_interactive_profile(_namespace(profile="bad"), imported)
        self.assertEqual(str(bad_profile.exception), "Unsupported profile: bad")

        self.assertEqual(noninteractive.non_interactive_policy_mode(_namespace(), imported), "scheduled")
        self.assertEqual(noninteractive.non_interactive_policy_mode(_namespace(), _imported_defaults()), "manual")
        with self.assertRaises(ValueError) as bad_policy:
            noninteractive.non_interactive_policy_mode(_namespace(policy_mode="bad"), imported)
        self.assertEqual(str(bad_policy.exception), "Unsupported policy mode: bad")

        self.assertTrue(noninteractive.non_interactive_digest_auth(_namespace(digest_auth=True), _imported_defaults()))
        self.assertTrue(noninteractive.non_interactive_digest_auth(_namespace(), imported))
        self.assertFalse(noninteractive.non_interactive_digest_auth(_namespace(), _imported_defaults()))
        self.assertEqual(
            noninteractive.non_interactive_topology_preset(_namespace(), _imported_defaults(), "multi_adapter_topology"),
            "template-stack",
        )
        self.assertEqual(noninteractive.non_interactive_topology_preset(_namespace(topology_preset="native"), imported, "native_device"), "native")
        self.assertEqual(noninteractive.non_interactive_device_instance(_namespace(), imported), 61)
        self.assertEqual(noninteractive.non_interactive_device_instance(_namespace(), _imported_defaults()), 60)
        self.assertEqual(noninteractive.non_interactive_phase(_namespace(), imported), "L2")
        self.assertEqual(noninteractive.non_interactive_phase(_namespace(), _imported_defaults()), "L1")
        self.assertEqual(noninteractive.non_interactive_string("cli", "imported"), "cli")
        self.assertEqual(noninteractive.non_interactive_string(None, "imported"), "imported")
        self.assertEqual(noninteractive.non_interactive_string(None, None), "")

    def test_backend_preset_and_relay_contracts(self) -> None:
        imported = _imported_defaults(charger_backend="goe_charger", charger_preset="abb-terra-ac-modbus")
        self.assertEqual(noninteractive.non_interactive_backend(_namespace(), imported, "native_device"), "goe_charger")
        self.assertEqual(noninteractive.non_interactive_backend(_namespace(), _imported_defaults(), "hybrid_topology"), "simpleevse_charger")
        self.assertEqual(
            noninteractive.non_interactive_backend(
                _namespace(),
                _imported_defaults(charger_backend="modbus_charger"),
                "native_device",
            ),
            "modbus_charger",
        )
        self.assertEqual(noninteractive.non_interactive_backend(_namespace(charger_backend="modbus_charger"), imported, "native_device"), "modbus_charger")
        with self.assertRaises(ValueError) as bad_backend:
            noninteractive.non_interactive_backend(_namespace(charger_backend="bad"), imported, "native_device")
        self.assertEqual(str(bad_backend.exception), "Unsupported charger backend: bad")

        self.assertEqual(noninteractive.resolved_backend("shelly-meter-goe", None, "template_charger"), "goe_charger")
        self.assertEqual(noninteractive.resolved_backend(None, "abb-terra-ac-modbus", "template_charger"), "modbus_charger")
        self.assertEqual(noninteractive.resolved_backend("shelly-meter-modbus-charger", "abb-terra-ac-modbus", "template_charger"), "modbus_charger")
        self.assertEqual(noninteractive.resolved_backend(None, None, "template_charger"), "template_charger")
        self.assertEqual(
            noninteractive.non_interactive_charger_preset(
                _namespace(charger_preset="abb-terra-ac-modbus"),
                _imported_defaults(),
            ),
            "abb-terra-ac-modbus",
        )
        self.assertEqual(
            noninteractive.non_interactive_charger_preset(
                _namespace(charger_preset="abb-terra-ac-modbus"),
                _imported_defaults(),
            ),
            "abb-terra-ac-modbus",
        )
        with self.assertRaises(ValueError) as unsupported:
            noninteractive.non_interactive_charger_preset(
                _namespace(charger_preset="unknown-modbus"),
                _imported_defaults(),
            )
        self.assertEqual(
            str(unsupported.exception),
            "Unsupported charger preset: unknown-modbus",
        )

        self.assertEqual(noninteractive.non_interactive_cerbo_relay_inputs(_namespace(), None), (0, "NO"))
        self.assertEqual(noninteractive.non_interactive_cerbo_relay_inputs(_namespace(), "shelly-meter-cerbo-relay"), (0, "NO"))
        self.assertEqual(
            noninteractive.non_interactive_cerbo_relay_inputs(
                _namespace(cerbo_relay_contact_mode="NO"),
                "shelly-meter-cerbo-relay",
            ),
            (0, "NO"),
        )
        self.assertEqual(
            noninteractive.non_interactive_cerbo_relay_inputs(
                _namespace(cerbo_relay_index=1, cerbo_relay_contact_mode="nc"),
                "shelly-meter-cerbo-relay",
            ),
            (1, "NC"),
        )
        with self.assertRaises(ValueError) as bad_relay:
            noninteractive.non_interactive_cerbo_relay_inputs(_namespace(cerbo_relay_index=2), "shelly-meter-cerbo-relay")
        self.assertEqual(str(bad_relay.exception), "--cerbo-relay-index must be 0 or 1")
        with self.assertRaises(ValueError) as bad_contact:
            noninteractive.non_interactive_cerbo_relay_inputs(_namespace(cerbo_relay_contact_mode="bad"), "shelly-meter-cerbo-relay")
        self.assertEqual(str(bad_contact.exception), "--cerbo-relay-contact-mode must be NO or NC")

    def test_transport_and_policy_tuple_contracts(self) -> None:
        namespace = _namespace()
        imported = _imported_defaults()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_cli_non_interactive.non_interactive_transport_inputs",
                return_value=("tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7),
            ) as transport_inputs,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.preset_specific_defaults", return_value=(12.5, "P1")) as preset_defaults,
        ):
            self.assertEqual(
                noninteractive._non_interactive_transport_answers(
                    namespace,
                    imported,
                    backend="modbus_charger",
                    charger_preset="abb-terra-ac-modbus",
                    host_input="host.local",
                    topology_preset="template-stack",
                ),
                ("tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7, 12.5, "P1"),
            )
        transport_inputs.assert_called_once_with(namespace, "modbus_charger", "abb-terra-ac-modbus", "host.local", imported)
        preset_defaults.assert_called_once_with(
            namespace,
            imported,
            backend="modbus_charger",
            topology_preset="template-stack",
            charger_preset="abb-terra-ac-modbus",
        )

        with patch(
            "venus_evcharger.bootstrap.wizard_cli_non_interactive.policy_defaults",
            return_value=(100.0, 50.0, 20.0, 80.0, "mon,tue", "06:00", 6.0),
        ) as defaults:
            self.assertEqual(
                noninteractive._non_interactive_policy_answers(namespace, imported),
                ("manual", 100.0, 50.0, 20.0, 80.0, "mon,tue", "06:00", 6.0),
            )
        defaults.assert_called_once_with("manual", imported, namespace)

        self.assertEqual(
            noninteractive._effective_transport_answers("modbus_charger", "host.local", "tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7),
            ("tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7),
        )
        self.assertEqual(
            noninteractive._effective_transport_answers(None, "http://relay.local/rpc", "tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7),
            ("serial_rtu", "relay.local", 502, "/dev/ttyUSB0", 1),
        )

    def test_non_interactive_answers_orchestrates_all_fields(self) -> None:
        namespace = _namespace(
            host="cli.local",
            username="cli-user",
            password="cli-pass",
            phase="L3",
            device_instance=77,
        )
        imported = _imported_defaults()
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.non_interactive_profile", return_value="multi_adapter_topology") as profile,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.non_interactive_topology_preset", return_value="template-stack") as topology,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.non_interactive_backend", return_value="template_charger") as backend,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.non_interactive_charger_preset", return_value="abb-terra-ac-modbus") as preset,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.resolved_backend", return_value="modbus_charger") as resolved,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.role_host_defaults", return_value=("meter.local", "switch.local", "charger.local")) as role_hosts,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.resolved_primary_host", return_value="primary.local") as primary,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive._non_interactive_transport_answers", return_value=("tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7, 12.5, "P1")) as transport,
            patch(
                "venus_evcharger.bootstrap.wizard_cli_non_interactive._non_interactive_policy_answers",
                return_value=("scheduled", 100.0, 50.0, 20.0, 80.0, "mon,tue", "06:00", 6.0),
            ) as policy,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive._effective_transport_answers", return_value=("tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7)) as effective,
            patch("venus_evcharger.bootstrap.wizard_cli_non_interactive.non_interactive_cerbo_relay_inputs", return_value=(1, "NC")) as relay,
        ):
            answers = noninteractive.non_interactive_answers(namespace, imported)

        self.assertEqual(answers.profile, "multi_adapter_topology")
        self.assertEqual(answers.host_input, "primary.local")
        self.assertEqual(answers.meter_host_input, "meter.local")
        self.assertEqual(answers.switch_host_input, "switch.local")
        self.assertEqual(answers.charger_host_input, "charger.local")
        self.assertEqual(answers.device_instance, 77)
        self.assertEqual(answers.phase, "L3")
        self.assertEqual(answers.policy_mode, "scheduled")
        self.assertIs(answers.digest_auth, False)
        self.assertEqual(answers.username, "cli-user")
        self.assertEqual(answers.password, "cli-pass")
        self.assertEqual(answers.topology_preset, "template-stack")
        self.assertEqual(answers.charger_backend, "modbus_charger")
        self.assertEqual(answers.charger_preset, "abb-terra-ac-modbus")
        self.assertEqual(answers.request_timeout_seconds, 12.5)
        self.assertEqual(answers.cerbo_relay_index, 1)
        self.assertEqual(answers.cerbo_relay_contact_mode, "NC")
        self.assertEqual(answers.switch_group_supported_phase_selections, "P1")
        self.assertEqual(answers.auto_start_surplus_watts, 100.0)
        self.assertEqual(answers.auto_stop_surplus_watts, 50.0)
        self.assertEqual(answers.auto_min_soc, 20.0)
        self.assertEqual(answers.auto_resume_soc, 80.0)
        self.assertEqual(answers.scheduled_enabled_days, "mon,tue")
        self.assertEqual(answers.scheduled_latest_end_time, "06:00")
        self.assertEqual(answers.scheduled_night_current_amps, 6.0)
        self.assertEqual(answers.transport_kind, "tcp")
        self.assertEqual(answers.transport_host, "modbus.local")
        self.assertEqual(answers.transport_port, 1502)
        self.assertEqual(answers.transport_device, "/dev/ttyUSB9")
        self.assertEqual(answers.transport_unit_id, 7)
        profile.assert_called_once_with(namespace, imported)
        topology.assert_called_once_with(namespace, imported, "multi_adapter_topology")
        backend.assert_called_once_with(namespace, imported, "multi_adapter_topology")
        preset.assert_called_once_with(namespace, imported)
        resolved.assert_called_once_with("template-stack", "abb-terra-ac-modbus", "template_charger")
        role_hosts.assert_called_once_with(namespace, imported, "multi_adapter_topology", "template-stack", "cli.local")
        primary.assert_called_once_with(namespace, imported, "meter.local", "switch.local", "charger.local")
        transport.assert_called_once_with(
            namespace,
            imported,
            backend="modbus_charger",
            charger_preset="abb-terra-ac-modbus",
            host_input="primary.local",
            topology_preset="template-stack",
        )
        policy.assert_called_once_with(namespace, imported)
        effective.assert_called_once_with("modbus_charger", "primary.local", "tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7)
        relay.assert_called_once_with(namespace, "template-stack")

    def test_non_interactive_answers_uses_imported_and_builtin_fallbacks(self) -> None:
        imported = _imported_defaults(
            host_input="imported.local",
            profile="multi_adapter_topology",
            digest_auth=True,
            username="imported-user",
            password="imported-pass",
        )
        answers = noninteractive.non_interactive_answers(_namespace(), imported)
        self.assertEqual(answers.host_input, "imported.local")
        self.assertEqual(answers.meter_host_input, "imported.local")
        self.assertEqual(answers.switch_host_input, "imported.local")
        self.assertEqual(answers.charger_host_input, "imported.local")
        self.assertIs(answers.digest_auth, True)
        self.assertEqual(answers.username, "imported-user")
        self.assertEqual(answers.password, "imported-pass")

        default_answers = noninteractive.non_interactive_answers(
            _namespace(profile="multi_adapter_topology"),
            _imported_defaults(),
        )
        self.assertEqual(default_answers.host_input, "192.168.1.50")
        self.assertEqual(default_answers.meter_host_input, "192.168.1.50")
        self.assertEqual(default_answers.switch_host_input, "192.168.1.50")
        self.assertEqual(default_answers.charger_host_input, "192.168.1.50")


if __name__ == "__main__":
    unittest.main()
