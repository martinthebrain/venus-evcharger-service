# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from venus_evcharger.bootstrap import wizard_support


class WizardSupportContractTests(unittest.TestCase):
    def test_host_from_input_normalizes_hosts_and_reports_exact_errors(self) -> None:
        self.assertEqual(wizard_support.host_from_input("  charger.local  "), "charger.local")
        self.assertEqual(wizard_support.host_from_input("https://charger.local/status"), "charger.local")

        with self.assertRaisesRegex(ValueError, r"^Invalid host input 'http://'$"):
            wizard_support.host_from_input("http://")
        with self.assertRaisesRegex(ValueError, r"^Host must not be empty$"):
            wizard_support.host_from_input("   ")

    def test_base_url_from_input_normalizes_scheme_and_trailing_slashes(self) -> None:
        self.assertEqual(wizard_support.base_url_from_input(" charger.local/ "), "http://charger.local")
        self.assertEqual(wizard_support.base_url_from_input("https://charger.local/api/"), "https://charger.local/api")
        self.assertEqual(wizard_support.base_url_from_input("charger.localX"), "http://charger.localX")
        self.assertEqual(wizard_support.base_url_from_input("https://charger.local/apiX"), "https://charger.local/apiX")

        with self.assertRaisesRegex(ValueError, r"^Host must not be empty$"):
            wizard_support.base_url_from_input("   ")

    def test_transport_contract_covers_required_backends_and_defaults(self) -> None:
        self.assertTrue(wizard_support.backend_requires_transport("simpleevse_charger"))
        self.assertTrue(wizard_support.backend_requires_transport("smartevse_charger"))
        self.assertTrue(wizard_support.backend_requires_transport("modbus_charger"))
        self.assertFalse(wizard_support.backend_requires_transport("goe_charger"))
        self.assertFalse(wizard_support.backend_requires_transport("template_charger"))
        self.assertFalse(wizard_support.backend_requires_transport(None))
        self.assertEqual(wizard_support.default_transport_kind("modbus_charger"), "tcp")
        self.assertEqual(wizard_support.default_transport_kind("simpleevse_charger"), "serial_rtu")
        self.assertEqual(wizard_support.default_transport_kind(None), "serial_rtu")
        self.assertEqual(wizard_support.transport_summary("simpleevse_charger", "serial_rtu"), "serial_rtu")
        self.assertEqual(wizard_support.transport_summary("smartevse_charger", "tcp"), "tcp")
        self.assertEqual(wizard_support.transport_summary("modbus_charger", "tcp"), "tcp")
        self.assertIsNone(wizard_support.transport_summary("goe_charger", "serial_rtu"))

    def test_labels_notes_and_fallbacks_are_stable(self) -> None:
        self.assertEqual(
            wizard_support.profile_label("simple_relay"),
            "Recommended: Shelly PM/PM1 Gen4 measures and switches",
        )
        self.assertEqual(wizard_support.profile_label("unknown-profile"), "unknown-profile")
        self.assertEqual(wizard_support.policy_mode_label("manual"), "Manual charging")
        self.assertEqual(wizard_support.policy_mode_label("auto"), "PV surplus charging")
        self.assertEqual(wizard_support.policy_mode_label("scheduled"), "PV surplus plus scheduled fallback")
        self.assertEqual(wizard_support.policy_mode_label("unknown-mode"), "unknown-mode")
        self.assertEqual(
            wizard_support.policy_mode_note("scheduled"),
            "Scheduled mode behaves like Auto during the day window, then uses the configured night fallback after the latest end time.",
        )
        self.assertEqual(wizard_support.policy_mode_note("unknown-mode"), "unknown-mode")
        self.assertEqual(
            wizard_support.topology_preset_label("shelly-meter-goe"),
            "Recommended: Shelly meter + native go-e charger",
        )
        self.assertIsNone(wizard_support.topology_preset_label(None))
        self.assertEqual(wizard_support.topology_preset_label("custom-preset"), "custom-preset")

    def test_responsibility_summary_prefers_topology_then_profile_then_fallback(self) -> None:
        self.assertEqual(
            wizard_support.setup_responsibility_summary("simple_relay", "shelly-meter-goe"),
            "Shelly measures energy; go-e backend owns charger enable/current/status",
        )
        self.assertEqual(
            wizard_support.setup_responsibility_summary("native_device", None),
            "the charger backend owns metering, enable/disable, current control, and status where supported",
        )
        self.assertEqual(wizard_support.setup_responsibility_summary("custom-profile", None), "custom-profile")
        self.assertEqual(
            wizard_support.setup_responsibility_summary("simple_relay", "custom-topology"),
            "custom-topology",
        )


if __name__ == "__main__":
    unittest.main()
