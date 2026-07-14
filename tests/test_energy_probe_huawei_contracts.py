# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for Huawei probe recommendations."""

from __future__ import annotations

import unittest

from venus_evcharger.energy import probe_huawei
from venus_evcharger.energy.profiles import EnergySourceProfile


class EnergyProbeHuaweiContractTests(unittest.TestCase):
    def test_ready_recommendation_bundle_is_exact(self) -> None:
        detection = {
            "detected": {"host": " 10.0.0.2 ", "port": "502", "unit_id": "2"},
            "profile_details": {"platform": "MB", "access_mode": "sdongle"},
        }
        recommendation = probe_huawei._huawei_recommendation(
            "huawei_mb_sdongle",
            detection=detection,
            required_fields_ok=True,
            meter_block_detected=True,
            source_id=" hybrid ",
        )
        template = "deploy/venus/template-energy-source-huawei-mb-modbus.ini"
        config_path = "/data/etc/huawei-mb-modbus.ini"
        self.assertEqual(
            recommendation,
            {
                "status": "ready",
                "bundle_schema_type": "energy-recommendation-bundle",
                "bundle_schema_version": 1,
                "suggested_profile": "huawei_mb_sdongle",
                "suggested_template": template,
                "suggested_config_path": config_path,
                "host": " 10.0.0.2 ",
                "port": "502",
                "unit_id": "2",
                "platform": "MB",
                "access_mode": "sdongle",
                "meter_block_detected": True,
                "required_fields_ok": True,
                "capacity_required_for_weighted_soc": True,
                "capacity_config_key": "AutoEnergySource.hybrid.UsableCapacityWh",
                "capacity_hint": "Set usable battery capacity in Wh when you want weighted combined SOC.",
                "summary": (
                    f"Use profile huawei_mb_sdongle with template {template}; "
                    "host= 10.0.0.2 , port=502, unit=2; meter block present."
                ),
                "config_snippet": "\n".join(
                    (
                        "# Add the source id to AutoEnergySources in your main config.",
                        "# Example: AutoEnergySources=victron,hybrid",
                        "AutoEnergySource.hybrid.Profile=huawei_mb_sdongle",
                        f"AutoEnergySource.hybrid.ConfigPath={config_path}",
                        "AutoEnergySource.hybrid.Host=10.0.0.2",
                        "AutoEnergySource.hybrid.Port=502",
                        "AutoEnergySource.hybrid.UnitId=2",
                        "# Copy the matching starter template to the ConfigPath above:",
                        f"# {template}",
                        "# Optional but recommended when you want weighted combined SOC:",
                        "# AutoEnergySource.hybrid.UsableCapacityWh=<set-me>",
                    )
                ),
                "wizard_hint_block": "\n".join(
                    (
                        "Huawei recommendation",
                        "- profile: huawei_mb_sdongle",
                        f"- template: {template}",
                        f"- config path: {config_path}",
                        "- host: 10.0.0.2",
                        "- port: 502",
                        "- unit id: 2",
                        "- meter block: present",
                        "- source id: hybrid",
                        "- capacity follow-up: set AutoEnergySource.hybrid.UsableCapacityWh for weighted combined SOC",
                        "- next step: copy the template, then paste the config snippet into the main config",
                    )
                ),
                "notes": [
                    "Huawei meter block detected",
                    "Configured Huawei energy fields responded successfully",
                ],
            },
        )

    def test_incomplete_bundle_normalizes_missing_mappings_and_source_id(self) -> None:
        recommendation = probe_huawei._huawei_recommendation(
            "unknown-profile",
            detection={"detected": [], "profile_details": None},
            required_fields_ok=False,
            meter_block_detected=False,
            source_id=" ",
        )
        self.assertEqual(recommendation["status"], "incomplete")
        self.assertEqual(recommendation["host"], "")
        self.assertIsNone(recommendation["port"])
        self.assertIsNone(recommendation["unit_id"])
        self.assertEqual(recommendation["platform"], "")
        self.assertEqual(recommendation["access_mode"], "")
        self.assertEqual(recommendation["capacity_config_key"], "AutoEnergySource.huawei.UsableCapacityWh")
        self.assertEqual(
            recommendation["notes"],
            [
                "Huawei meter block not detected",
                "One or more required Huawei energy fields did not respond",
            ],
        )
        self.assertNotIn(".Host=", recommendation["config_snippet"])
        self.assertNotIn(".Port=", recommendation["config_snippet"])
        self.assertNotIn(".UnitId=", recommendation["config_snippet"])
        self.assertEqual(
            recommendation["summary"],
            "Use profile unknown-profile with template deploy/venus/template-energy-source-huawei-mb-modbus.ini; "
            "host=unknown; meter block missing.",
        )
        self.assertIn("- meter block: not detected", recommendation["wizard_hint_block"])

    def test_template_and_config_path_selection_cover_every_family(self) -> None:
        cases = {
            "huawei_map0_unit1": "deploy/venus/template-energy-source-huawei-mb-unit1-modbus.ini",
            "huawei_map0_unit2": "deploy/venus/template-energy-source-huawei-mb-unit2-modbus.ini",
            "huawei_ma_native_lan": "deploy/venus/template-energy-source-huawei-ma-modbus.ini",
            "huawei_l1_native_lan": "deploy/venus/template-energy-source-huawei-ma-modbus.ini",
            " HUAWEI_MA_CUSTOM ": "deploy/venus/template-energy-source-huawei-ma-modbus.ini",
            "huawei_mb_native_lan": "deploy/venus/template-energy-source-huawei-mb-modbus.ini",
            "unknown": "deploy/venus/template-energy-source-huawei-mb-modbus.ini",
        }
        for profile_name, expected in cases.items():
            with self.subTest(profile_name=profile_name):
                self.assertEqual(probe_huawei._recommended_huawei_template(profile_name), expected)
        self.assertEqual(
            probe_huawei._recommended_huawei_config_path(
                "deploy/venus/template-energy-source-huawei-ma-modbus.ini"
            ),
            "/data/etc/huawei-ma-modbus.ini",
        )
        self.assertEqual(
            probe_huawei._recommended_huawei_config_path(" custom.ini "),
            "/data/etc/custom.ini",
        )
        ma_profile = EnergySourceProfile(
            profile_name="custom",
            role="hybrid-inverter",
            connector_type="modbus",
            platform="ma",
        )
        self.assertTrue(probe_huawei._recommended_huawei_ma_profile(ma_profile, "other"))
        self.assertFalse(probe_huawei._recommended_huawei_ma_profile(None, "huawei_mb_native_lan"))
        self.assertEqual(
            probe_huawei._huawei_recommendation(
                "huawei_l1_native_lan",
                detection={},
                required_fields_ok=True,
                meter_block_detected=False,
                source_id="l1",
            )["suggested_template"],
            "deploy/venus/template-energy-source-huawei-ma-modbus.ini",
        )

    def test_location_hint_mapping_and_integer_boundaries_are_exact(self) -> None:
        self.assertEqual(
            probe_huawei._recommendation_location_text(
                {"host": "gateway", "port": 0, "unit_id": 0}
            ),
            "host=gateway, port=0, unit=0",
        )
        self.assertEqual(
            probe_huawei._recommendation_hint_values(
                {"host": " gateway ", "port": 0, "unit_id": 0}
            ),
            ("gateway", "unknown", "unknown"),
        )
        self.assertEqual(probe_huawei._recommendation_location_text({}), "host=unknown")
        self.assertEqual(probe_huawei._recommendation_hint_values({}), ("unknown", "unknown", "unknown"))
        self.assertEqual(probe_huawei._mapping_value({"item": {"x": 1}}, "item"), {"x": 1})
        self.assertEqual(probe_huawei._mapping_value({"item": "invalid"}, "item"), {})
        for value, expected in ((None, None), (True, None), ("bad", None), (" 7 ", 7), (0, 0)):
            with self.subTest(value=value):
                self.assertEqual(probe_huawei._optional_int(value), expected)


if __name__ == "__main__":
    unittest.main()
