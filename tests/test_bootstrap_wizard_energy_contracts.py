# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from venus_evcharger.bootstrap import wizard_energy as energy


class BootstrapWizardEnergyContracts(unittest.TestCase):
    def test_prefix_capacity_and_assignment_helpers_are_exact(self) -> None:
        self.assertIsNone(energy.optional_capacity_wh(None))
        self.assertIsNone(energy.optional_capacity_wh(""))
        self.assertIsNone(energy.optional_capacity_wh("bad"))
        self.assertIsNone(energy.optional_capacity_wh("0"))
        self.assertIsNone(energy.optional_capacity_wh("-1"))
        self.assertEqual(energy.optional_capacity_wh("0.5"), 0.5)
        self.assertEqual(energy.optional_capacity_wh(" 12.5 "), 12.5)
        self.assertEqual(energy.normalized_recommendation_prefixes([" a ", "", " b "]), ("a", "b"))
        self.assertEqual(energy.merged_recommendation_prefixes(("a", "b"), ["b", "c"], None, " d "), ("a", "b", "c", "d"))
        self.assertTrue(energy._is_auto_energy_assignment_key("AutoUseCombinedBatterySoc"))
        self.assertTrue(energy._is_auto_energy_assignment_key("AutoEnergySources"))
        self.assertTrue(energy._is_auto_energy_assignment_key("AutoEnergySource.alpha.Profile"))
        self.assertFalse(energy._is_auto_energy_assignment_key("Other"))
        self.assertEqual(energy._assignment_source_id("AutoEnergySource.alpha.Profile"), "alpha")
        self.assertEqual(energy._assignment_source_id("AutoEnergySource.alpha.Profile.Extra"), "alpha")
        self.assertEqual(energy._assignment_source_id("AutoEnergySource.alpha"), "")
        self.assertEqual(energy._assignment_source_id("AutoEnergySource. beta .Profile"), "beta")
        self.assertEqual(energy._assignment_source_id("Other"), "")
        self.assertEqual(energy._energy_source_list_ids(" alpha, , beta "), ("alpha", "beta"))
        with self.assertRaisesRegex(ValueError, "Config assignment line is missing '=': broken"):
            energy._split_config_assignment("broken")
        self.assertEqual(energy._config_text_value({}, "AutoEnergySources"), "")
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoEnergySources=alpha,beta\n", encoding="utf-8")
            self.assertEqual(energy.existing_auto_energy_source_ids(config_path), ("alpha", "beta"))

    def test_merge_lines_and_capacity_follow_up_are_exact(self) -> None:
        source = {
            "source_id": "alpha",
            "profile": "huawei",
            "configPath": "/data/etc/alpha.ini",
            "host": "alpha.local",
            "port": 502,
            "unitId": 7,
            "usableCapacityWh": 1536.0,
        }
        self.assertEqual(
            energy.energy_source_merge_lines(source),
            [
                "AutoEnergySource.alpha.Profile=huawei",
                "AutoEnergySource.alpha.ConfigPath=/data/etc/alpha.ini",
                "AutoEnergySource.alpha.Host=alpha.local",
                "AutoEnergySource.alpha.Port=502",
                "AutoEnergySource.alpha.UnitId=7",
                "AutoEnergySource.alpha.UsableCapacityWh=1536",
            ],
        )
        self.assertEqual(energy.energy_source_merge_lines({"profile": "huawei"}), [])
        self.assertEqual(
            energy.energy_source_merge_lines({"source_id": "alpha", "profile": "", "host": "host"}),
            ["AutoEnergySource.alpha.Host=host"],
        )
        self.assertIsNone(energy.energy_source_capacity_follow_up({"source_id": "alpha"}))
        self.assertIsNone(energy.energy_source_capacity_follow_up({"capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh"}))
        self.assertEqual(
            energy.energy_source_capacity_follow_up(
                {
                    "source_id": "alpha",
                    "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
                    "capacityHint": "Custom hint",
                    "usableCapacityWh": "2048",
                }
            ),
            {
                "source_id": "alpha",
                "config_key": "AutoEnergySource.alpha.UsableCapacityWh",
                "placeholder": "2048",
                "hint": "Custom hint",
                "configured": True,
            },
        )
        self.assertEqual(
            energy.energy_source_capacity_follow_up(
                {
                    "source_id": "alpha",
                    "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
                }
            ),
            {
                "source_id": "alpha",
                "config_key": "AutoEnergySource.alpha.UsableCapacityWh",
                "placeholder": "<set-me>",
                "hint": "Set usable battery capacity in Wh for weighted combined SOC.",
                "configured": False,
            },
        )

    def test_suggestion_sets_overrides_and_assignments_are_exact(self) -> None:
        sources = (
            {"source_id": "", "capacityConfigKey": "ignored"},
            {"source_id": "alpha", "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh"},
            {"source_id": "beta"},
        )
        self.assertEqual(energy.existing_auto_energy_source_ids_from_suggestions(sources), {"alpha", "beta"})
        self.assertEqual(energy.unknown_capacity_override_source_ids({"beta": 1.0, "gamma": 2.0}, {"alpha", "beta"}), ["gamma"])
        self.assertEqual(
            energy.suggested_energy_sources_with_capacity(sources, 4096.0),
            (
                {"source_id": "", "capacityConfigKey": "ignored"},
                {"source_id": "alpha", "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh", "usableCapacityWh": 4096.0},
                {"source_id": "beta"},
            ),
        )
        self.assertEqual(
            energy.suggested_energy_sources_with_capacity_overrides(sources, {"alpha": 2048.0}),
            (
                {"source_id": "", "capacityConfigKey": "ignored"},
                {"source_id": "alpha", "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh", "usableCapacityWh": 2048.0},
                {"source_id": "beta"},
            ),
        )
        assignments = energy.suggested_energy_assignments(
            {"AutoUseCombinedBatterySoc": "0", "AutoEnergySources": "grid", "Keep": "yes"},
            ({"source_id": "alpha", "profile": "huawei", "host": "host=name"},),
        )
        self.assertEqual(
            assignments,
            {
                "AutoUseCombinedBatterySoc": "1",
                "AutoEnergySources": "grid,alpha",
                "Keep": "yes",
                "AutoEnergySource.alpha.Profile": "huawei",
                "AutoEnergySource.alpha.Host": "host=name",
            },
        )
        self.assertEqual(
            energy.merge_energy_source_ids(("grid",), ({"source_id": None}, {"profile": "missing"}, {"source_id": "alpha"})),
            ("grid", "alpha"),
        )
        with self.assertRaisesRegex(ValueError, "multiple recommendation bundles resolved to the same source id: dup"):
            energy.validate_unique_suggested_energy_sources(({"source_id": ""}, {"source_id": "dup"}, {"source_id": "dup"}))
        with self.assertRaisesRegex(ValueError, "multiple recommendation bundles resolved to the same source id: alpha, dup"):
            energy.validate_unique_suggested_energy_sources(
                (
                    {"source_id": "dup"},
                    {"source_id": "alpha"},
                    {"source_id": "dup"},
                    {"source_id": "alpha"},
                )
            )
        with self.assertRaisesRegex(ValueError, "alpha, gamma"):
            energy._validate_capacity_overrides(
                ({"source_id": "beta", "capacityConfigKey": "AutoEnergySource.beta.UsableCapacityWh"},),
                {"gamma": 1.0, "alpha": 2.0},
            )

    def test_merge_payload_and_lines_are_exact(self) -> None:
        source = {
            "source_id": "alpha",
            "profile": "huawei",
            "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
        }
        follow_up = energy.suggested_energy_capacity_follow_up((source,))
        self.assertEqual(
            energy.suggested_energy_merge_lines(("grid", "alpha"), (source,), follow_up),
            [
                "# Merge these lines into the main config when you want the suggested external energy source enabled.",
                "AutoUseCombinedBatterySoc=1",
                "AutoEnergySources=grid,alpha",
                "AutoEnergySource.alpha.Profile=huawei",
                "# Optional but recommended for weighted combined SOC:",
                "# AutoEnergySource.alpha.UsableCapacityWh=<set-me>",
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoEnergySources=grid\nAutoEnergySource.grid.Profile=dbus\n", encoding="utf-8")
            payload, files = energy.build_suggested_energy_merge(config_path, (source,))
        assert payload is not None
        self.assertEqual(payload["existing_source_ids"], ["grid"])
        self.assertEqual(payload["merged_source_ids"], ["grid", "alpha"])
        self.assertIs(payload["auto_use_combined_battery_soc"], True)
        self.assertEqual(payload["helper_file"], "wizard-auto-energy-merge.ini")
        self.assertEqual(payload["capacity_follow_up"], [dict(follow_up[0])])
        self.assertIs(payload["applied_to_config"], False)
        self.assertIn("AutoEnergySources=grid,alpha", payload["merge_block"])
        self.assertIn("\nAutoEnergySource.alpha.Profile=huawei\n", payload["merge_block"])
        self.assertEqual(files["wizard-auto-energy-merge.ini"], str(payload["merge_block"]) + "\n")

    def test_structured_bundle_helpers_are_exact(self) -> None:
        snippet = (
            "# comment\n"
            "AutoEnergySource.alpha.Profile=huawei\n"
            "AutoEnergySource.alpha.Host=host=value\n"
            "AutoEnergySource.alpha.Port=502\n"
            "AutoEnergySource.alpha.UnitId=7\n"
            "AutoEnergySource.alpha\n"
            "AutoEnergySource.beta.Profile=ignored\n"
        )
        self.assertEqual(
            energy.structured_energy_source_from_block("alpha", snippet),
            {"source_id": "alpha", "profile": "huawei", "host": "host=value", "port": 502, "unitId": 7},
        )
        self.assertEqual(energy.bundle_source_id(snippet, "fallback"), "alpha")
        self.assertEqual(energy.bundle_source_id("AutoEnergySource.alpha\n", "fallback"), "fallback")
        self.assertEqual(energy.bundle_source_id("AutoEnergySource.alpha=bad\nAutoEnergySource.beta.Profile=1\n", "fallback"), "beta")
        self.assertEqual(
            energy.bundle_source_id("InvalidAutoEnergySource.alpha.Profile=1\nAutoEnergySource.real.Profile=1\n", "fallback"),
            "real",
        )
        self.assertEqual(energy.bundle_source_id("AutoEnergySource.alpha\nAutoEnergySource.beta.Profile=1\n", "fallback"), "beta")
        self.assertEqual(energy.bundle_source_id("ignored.with.dot=value\nAutoEnergySource.alpha.Profile=1\n", "fallback"), "alpha")
        self.assertEqual(energy.bundle_source_id("AutoEnergySource.alpha.Profile.Extra=value\n", "fallback"), "alpha")
        self.assertEqual(energy.bundle_source_id("# none\n", "fallback"), "fallback")
        self.assertEqual(
            energy.bundle_target_names("huawei"),
            {
                "ini": "wizard-huawei-energy.ini",
                "wizard": "wizard-huawei-energy.wizard.txt",
                "summary": "wizard-huawei-energy.summary.txt",
            },
        )
        self.assertEqual(
            energy.bundle_target_names("custom"),
            {
                "ini": "wizard-energy-custom.ini",
                "wizard": "wizard-energy-custom.wizard.txt",
                "summary": "wizard-energy-custom.summary.txt",
            },
        )
        self.assertEqual(energy.bundle_labels("huawei")[0], "External energy source integration")
        self.assertEqual(energy.bundle_labels("custom")[0], "External energy source integration (custom)")
        self.assertEqual(energy.bundle_block_label("huawei"), "External energy source")
        self.assertEqual(energy.bundle_block_label("custom"), "External energy source (custom)")
        self.assertEqual(
            energy.bundle_target_names(""),
            {
                "ini": "wizard-huawei-energy.ini",
                "wizard": "wizard-huawei-energy.wizard.txt",
                "summary": "wizard-huawei-energy.summary.txt",
            },
        )
        self.assertEqual(
            energy.bundle_labels(""),
            ("External energy source integration", "Set usable battery capacity for weighted combined SOC"),
        )
        self.assertEqual(energy.bundle_block_label(""), "External energy source")

    def test_huawei_bundle_files_legacy_prefix_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir) / "bundle"
            Path(str(prefix) + ".ini").write_text(
                "AutoEnergySource.huawei.Profile=huawei\n"
                "AutoEnergySource.huawei.ConfigPath=/data/etc/huawei.ini\n",
                encoding="utf-8",
            )
            Path(str(prefix) + ".wizard.txt").write_text("wizard hint\n", encoding="utf-8")
            Path(str(prefix) + ".summary.txt").write_text("summary\n", encoding="utf-8")
            files, review, blocks, sources = energy.huawei_bundle_files(str(prefix))
        self.assertEqual(set(files), {"wizard-huawei-energy.ini", "wizard-huawei-energy.wizard.txt", "wizard-huawei-energy.summary.txt"})
        self.assertEqual(review, ("External energy source integration", "Set usable battery capacity for weighted combined SOC"))
        self.assertEqual(set(blocks), {"External energy source"})
        self.assertEqual(
            sources,
            (
                {
                    "source_id": "huawei",
                    "profile": "huawei",
                    "configPath": "/data/etc/huawei.ini",
                    "capacityRequiredForWeightedSoc": True,
                    "capacityConfigKey": "AutoEnergySource.huawei.UsableCapacityWh",
                    "capacityHint": "Set usable battery capacity in Wh for weighted combined SOC.",
                },
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir) / "default"
            Path(str(prefix) + ".ini").write_text("No structured source id here\n", encoding="utf-8")
            Path(str(prefix) + ".wizard.txt").write_text("wizard\n", encoding="utf-8")
            Path(str(prefix) + ".summary.txt").write_text("summary\n", encoding="utf-8")
            files, review, blocks, sources = energy.huawei_bundle_files(str(prefix))
        self.assertEqual(set(files), {"wizard-huawei-energy.ini", "wizard-huawei-energy.wizard.txt", "wizard-huawei-energy.summary.txt"})
        self.assertEqual(review[0], "External energy source integration")
        self.assertEqual(set(blocks), {"External energy source"})
        self.assertEqual(sources[0]["source_id"], "huawei")

    def test_huawei_bundle_files_custom_default_missing_and_manifest_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir) / "custom"
            Path(str(prefix) + ".ini").write_text("AutoEnergySource.custom.Profile=huawei\n", encoding="utf-8")
            Path(str(prefix) + ".wizard.txt").write_text("wizard\n", encoding="utf-8")
            Path(str(prefix) + ".summary.txt").write_text("summary\n", encoding="utf-8")
            files, review, blocks, sources = energy.huawei_bundle_files(str(prefix), source_id="fallback")
        self.assertEqual(set(files), {"wizard-energy-custom.ini", "wizard-energy-custom.wizard.txt", "wizard-energy-custom.summary.txt"})
        self.assertEqual(review[0], "External energy source integration (custom)")
        self.assertEqual(set(blocks), {"External energy source (custom)"})
        self.assertEqual(sources[0]["source_id"], "custom")

        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir) / "fallback"
            Path(str(prefix) + ".ini").write_text("Profile line without source id\n", encoding="utf-8")
            Path(str(prefix) + ".wizard.txt").write_text("wizard\n", encoding="utf-8")
            Path(str(prefix) + ".summary.txt").write_text("summary\n", encoding="utf-8")
            files, _review, _blocks, sources = energy.huawei_bundle_files(str(prefix), source_id="fallback")
        self.assertIn("wizard-energy-fallback.ini", files)
        self.assertEqual(sources[0]["source_id"], "fallback")

        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir) / "broken"
            Path(str(prefix) + ".ini").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError) as captured:
                energy.huawei_bundle_files(str(prefix))
            self.assertEqual(
                str(captured.exception),
                "Huawei recommendation bundle is incomplete: "
                + str(Path(str(prefix) + ".wizard.txt"))
                + ", "
                + str(Path(str(prefix) + ".summary.txt")),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "manifest_bundle"
            config_path = Path(temp_dir) / "config.ini"
            hint_path = Path(temp_dir) / "hint.txt"
            summary_path = Path(temp_dir) / "summary.txt"
            config_path.write_text("No structured source id here\n", encoding="utf-8")
            hint_path.write_text("manifest hint\n", encoding="utf-8")
            summary_path.write_text("manifest summary\n", encoding="utf-8")
            Path(str(base) + ".manifest.json").write_text(
                (
                    '{"schema_type":"energy-recommendation-bundle","schema_version":1,'
                    '"source_id":"manifested","profile":"huawei","config_path":"/data/etc/manifested.ini",'
                    f'"files":{{"config_snippet":"{config_path}","wizard_hint":"{hint_path}","summary":"{summary_path}"}}}}'
                ),
                encoding="utf-8",
            )
            files, review, blocks, sources = energy.huawei_bundle_files(str(base), source_id="ignored")
        self.assertEqual(set(files), {"wizard-energy-manifested.ini", "wizard-energy-manifested.wizard.txt", "wizard-energy-manifested.summary.txt"})
        self.assertEqual(review[0], "External energy source integration (manifested)")
        self.assertEqual(set(blocks), {"External energy source (manifested)"})
        self.assertEqual(sources[0]["source_id"], "manifested")


if __name__ == "__main__":
    unittest.main()
