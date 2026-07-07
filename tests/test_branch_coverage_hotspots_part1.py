# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageWizardEnergyCasesPart1:
    def test_wizard_energy_helper_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            missing_config = temp_path / "missing.ini"
            self.assertEqual(_config_auto_energy_sources_value(missing_config), "")
            config_path = temp_path / "existing.ini"
            config_path.write_text("[DEFAULT]\nAutoEnergySources=grid, alpha\n", encoding="utf-8")
            self.assertEqual(_config_auto_energy_sources_value(config_path), "grid, alpha")
            self.assertEqual(existing_auto_energy_source_ids(config_path), ("grid", "alpha"))
            config_path.write_text("[DEFAULT]\nautoenergysources=grid,alpha\nOther=value\n", encoding="utf-8")
            self.assertEqual(existing_auto_energy_assignments(config_path), {})
            self.assertEqual(existing_auto_energy_assignments(temp_path / "missing-assignments.ini"), {})
            self.assertEqual(normalized_recommendation_prefixes(None), ())
            self.assertEqual(normalized_recommendation_prefixes(" pref "), ("pref",))
            self.assertEqual(normalized_recommendation_prefixes([" a ", " ", "b "]), ("a", "b"))
            self.assertEqual(
                merged_recommendation_prefixes(("a", "b"), ["b", "c"], None, " d "),
                ("a", "b", "c", "d"),
            )
            self.assertIsNone(optional_capacity_wh("bad"))

            source = {"source_id": "alpha", "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh"}
            unchanged = suggested_energy_sources_with_capacity((source,), None)
            self.assertEqual(unchanged, (source,))
            self.assertEqual(suggested_energy_sources_with_capacity(({"source_id": "plain"},), 2048.0), ({"source_id": "plain"},))

            with self.assertRaisesRegex(ValueError, "unknown source ids"):
                suggested_energy_sources_with_capacity_overrides((source,), {"beta": 1000.0})
            with self.assertRaisesRegex(ValueError, "multiple recommendation bundles resolved"):
                validate_unique_suggested_energy_sources(({"source_id": "dup"}, {"source_id": "dup"}))
            self.assertEqual(
                validate_unique_suggested_energy_sources(({"source_id": ""}, {"source_id": "ok"})),
                ({"source_id": ""}, {"source_id": "ok"}),
            )

            updated = suggested_energy_sources_with_capacity((source,), 2048.0)
            self.assertEqual(updated[0]["usableCapacityWh"], 2048.0)
            self.assertEqual(suggested_energy_sources_with_capacity_overrides((source,), {}), (source,))
            overridden = suggested_energy_sources_with_capacity_overrides(
                (
                    {"source_id": "plain"},
                    source,
                    {"source_id": "beta", "capacityConfigKey": "AutoEnergySource.beta.UsableCapacityWh"},
                ),
                {"alpha": 4096.0},
            )
            self.assertEqual(overridden[1]["usableCapacityWh"], 4096.0)

            self.assertEqual(merge_energy_source_ids(("alpha",), ({"source_id": "alpha"}, {"source_id": "beta"})), ("alpha", "beta"))
            self.assertEqual(
                existing_source_ids_from_assignments(
                    {
                        "AutoEnergySources": "one",
                        "AutoEnergySource.two.Profile": "x",
                    }
                ),
                ("one", "two"),
            )

            self.assertEqual(energy_source_merge_lines({}), [])
            self.assertEqual(
                energy_source_merge_lines({"source_id": "alpha", "Port": 502, "usableCapacityWh": 1536.0})[-1],
                "AutoEnergySource.alpha.UsableCapacityWh=1536",
            )
            self.assertNotIn(
                "# Optional but recommended for weighted combined SOC:",
                suggested_energy_merge_lines(("alpha",), (source,), ()),
            )
            self.assertIsNone(energy_source_capacity_follow_up({"source_id": "alpha"}))
            self.assertEqual(
                energy_source_capacity_follow_up(
                    {
                        "source_id": "alpha",
                        "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
                        "usableCapacityWh": 1024.0,
                    }
                )["placeholder"],
                "1024",
            )

            merge_payload, merge_files = build_suggested_energy_merge(temp_path / "config.ini", ())
            self.assertIsNone(merge_payload)
            self.assertEqual(merge_files, {})
            assignments = suggested_energy_assignments(
                {"AutoUseCombinedBatterySoc": "0", "AutoEnergySources": "grid", "Keep": "yes"},
                (source,),
            )
            self.assertEqual(assignments["Keep"], "yes")

            self.assertIsNone(_structured_energy_source_line("#comment", "AutoEnergySource.alpha."))
            self.assertIsNone(_structured_energy_source_line("   ", "AutoEnergySource.alpha."))
            self.assertIsNone(_structured_energy_source_line("AutoEnergySource.alpha.Port", "AutoEnergySource.alpha."))
            self.assertEqual(_structured_energy_source_value("Port", "bad"), "bad")
            self.assertEqual(bundle_source_id("ignored=true\n", "fallback"), "fallback")
            self.assertEqual(
                bundle_source_id("AutoEnergySource..Port=1\nAutoEnergySource.beta.Port=1\n", "fallback"),
                "beta",
            )
            self.assertEqual(bundle_target_names("")["ini"], "wizard-huawei-energy.ini")
            self.assertEqual(bundle_labels("")[0], "External energy source integration")
            self.assertEqual(bundle_block_label(""), "External energy source")
            self.assertEqual(manual_review_union(("Auth",), ("Auth", "Meter")), ("Auth", "Meter"))

            structured = structured_energy_source_from_block(
                "alpha",
                "\n".join(
                    (
                        "# comment",
                        "AutoEnergySource.alpha.Port=bad",
                        "AutoEnergySource.alpha.UnitId=7",
                        "Other=ignored",
                    )
                ),
            )
            self.assertEqual(structured["port"], "bad")
            self.assertEqual(structured["unitId"], 7)
            self.assertEqual(bundle_target_names("alpha")["ini"], "wizard-energy-alpha.ini")
            self.assertEqual(bundle_labels("alpha")[0], "External energy source integration (alpha)")
            self.assertEqual(bundle_block_label("alpha"), "External energy source (alpha)")

            bundle_prefix = temp_path / "bundle"
            with self.assertRaisesRegex(ValueError, "incomplete"):
                huawei_bundle_files(str(bundle_prefix))

    def test_wizard_energy_merge_and_bundle_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nAutoEnergySources=grid\nAutoEnergySource.grid.Profile=template\n",
                encoding="utf-8",
            )
            suggested_sources = (
                {
                    "source_id": "alpha",
                    "profile": "huawei_mb_sdongle",
                    "configPath": "/data/etc/huawei.ini",
                    "usableCapacityWh": 2048.0,
                    "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
                },
            )
            assignments = suggested_energy_assignments({"AutoEnergySource.grid.Profile": "template"}, suggested_sources)
            self.assertIn("AutoEnergySource.alpha.Profile", assignments)
            merge_lines = suggested_energy_merge_lines(
                ("grid", "alpha"),
                suggested_sources,
                tuple(item for item in (energy_source_capacity_follow_up(source) for source in suggested_sources) if item),
            )
            self.assertIn("AutoEnergySources=grid,alpha", merge_lines)

            merge_payload, merge_files = build_suggested_energy_merge(config_path, suggested_sources)
            self.assertEqual(merge_payload["merged_source_ids"], ["grid", "alpha"])
            self.assertIn("wizard-auto-energy-merge.ini", merge_files)

            bundle_prefix = temp_path / "bundle"
            manifest_dir = bundle_prefix
            manifest_dir.mkdir()
            (manifest_dir / "config.ini").write_text(
                "AutoEnergySource.beta.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.beta.ConfigPath=/data/etc/beta.ini\n",
                encoding="utf-8",
            )
            (manifest_dir / "hint.txt").write_text("Hint\n", encoding="utf-8")
            (manifest_dir / "summary.txt").write_text("Summary\n", encoding="utf-8")
            (temp_path / "bundle.manifest.json").write_text(
                '{'
                '"schema_type":"energy-recommendation-bundle",'
                '"schema_version":1,'
                '"source_id":"beta",'
                '"profile":"huawei_mb_sdongle",'
                '"config_path":"/data/etc/beta.ini",'
                '"files":{"config_snippet":"'
                + str((manifest_dir / "config.ini").resolve())
                + '","wizard_hint":"'
                + str((manifest_dir / "hint.txt").resolve())
                + '","summary":"'
                + str((manifest_dir / "summary.txt").resolve())
                + '"}'
                '}',
                encoding="utf-8",
            )
            rendered_files, review_items, suggested_blocks, structured_sources = huawei_bundle_files(str(manifest_dir))
            self.assertIn("wizard-energy-beta.ini", rendered_files)
            self.assertIn("weighted combined SOC", review_items[1])
            self.assertIn("External energy source (beta)", suggested_blocks)
            self.assertEqual(structured_sources[0]["source_id"], "beta")

