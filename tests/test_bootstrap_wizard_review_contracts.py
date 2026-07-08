# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from venus_evcharger.bootstrap.wizard_review import manual_review_items


class WizardReviewContractTests(unittest.TestCase):
    def test_always_includes_auth_and_dbus_selector_first(self) -> None:
        self.assertEqual(
            manual_review_items(
                profile="simple_relay",
                policy_mode="manual",
                charger_backend=None,
                transport_kind="tcp",
                topology_preset=None,
            ),
            ("Auth", "DBus selector pinning"),
        )

    def test_transport_backend_adds_transport_wiring_with_kind(self) -> None:
        self.assertEqual(
            manual_review_items(
                profile="simple_relay",
                policy_mode="manual",
                charger_backend="modbus_charger",
                transport_kind="rtu",
                topology_preset=None,
            ),
            ("Auth", "DBus selector pinning", "transport wiring (rtu)"),
        )

    def test_endpoint_profiles_add_endpoint_review_item(self) -> None:
        for profile in ("native_device", "hybrid_topology", "multi_adapter_topology"):
            with self.subTest(profile=profile):
                self.assertIn(
                    "adapter endpoints or serial transport",
                    manual_review_items(profile, "manual", None, "tcp", None),
                )

    def test_hybrid_topology_and_phase_presets_add_phase_wiring(self) -> None:
        self.assertIn(
            "phase-switch member wiring",
            manual_review_items("hybrid_topology", "manual", None, "tcp", None),
        )
        for preset in (
            "goe-external-switch-group",
            "template-meter-goe-switch-group",
            "shelly-meter-goe-switch-group",
            "shelly-meter-modbus-switch-group",
        ):
            with self.subTest(preset=preset):
                self.assertIn(
                    "phase-switch member wiring",
                    manual_review_items("multi_adapter_topology", "manual", None, "tcp", preset),
                )

    def test_individual_address_presets_add_address_review(self) -> None:
        for preset in (
            "shelly-io-template-charger",
            "shelly-io-modbus-charger",
            "tasmota-io-template-charger",
            "tasmota-io-modbus-charger",
            "tuya-io-template-charger",
            "tuya-io-modbus-charger",
            "shelly-meter-goe",
            "tasmota-meter-goe",
            "tasmota-meter-modbus-charger",
            "tuya-meter-goe",
            "tuya-meter-modbus-charger",
            "shelly-meter-goe-switch-group",
            "shelly-meter-modbus-switch-group",
        ):
            with self.subTest(preset=preset):
                self.assertIn(
                    "individual meter/switch device addresses",
                    manual_review_items("multi_adapter_topology", "manual", None, "tcp", preset),
                )

    def test_topology_presets_do_not_apply_to_unrelated_profiles(self) -> None:
        self.assertNotIn(
            "individual meter/switch device addresses",
            manual_review_items("simple_relay", "manual", None, "tcp", "shelly-meter-goe"),
        )
        self.assertNotIn(
            "phase-switch member wiring",
            manual_review_items("simple_relay", "manual", None, "tcp", "goe-external-switch-group"),
        )

    def test_policy_modes_add_threshold_and_schedule_reviews(self) -> None:
        self.assertEqual(
            manual_review_items("simple_relay", "auto", None, "tcp", None),
            ("Auth", "DBus selector pinning", "Auto thresholds"),
        )
        self.assertEqual(
            manual_review_items("simple_relay", "scheduled", None, "tcp", None),
            ("Auth", "DBus selector pinning", "Auto thresholds", "scheduled settings"),
        )


if __name__ == "__main__":
    unittest.main()
