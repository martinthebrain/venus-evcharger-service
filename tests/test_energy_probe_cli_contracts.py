# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact rendering and file-bundle contracts for the energy probe CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.energy import probe_cli


_RECOMMENDATION: dict[str, object] = {
    "suggested_profile": "huawei_profile",
    "suggested_config_path": "/data/etc/huawei.ini",
    "config_snippet": "AutoEnergySource.custom.Profile=huawei_profile\nvalue=1",
    "wizard_hint_block": "wizard text",
    "summary": "summary text",
}


class EnergyProbeCliContractTests(unittest.TestCase):
    def test_render_modes_and_fallbacks_are_exact(self) -> None:
        payload: dict[str, object] = {"z": 1, "a": 2, "recommendation": _RECOMMENDATION}
        expected_json = json.dumps(payload, indent=2, sort_keys=True)
        self.assertEqual(
            probe_cli._render_payload(SimpleNamespace(command="detect-modbus-energy", emit="ini"), payload),
            expected_json,
        )
        self.assertEqual(
            probe_cli._render_payload(SimpleNamespace(command="validate-huawei-energy", emit="json"), payload),
            expected_json,
        )
        for emit, expected in (
            ("ini", "AutoEnergySource.custom.Profile=huawei_profile\nvalue=1"),
            ("wizard-hint", "wizard text"),
            ("summary", "summary text"),
        ):
            with self.subTest(emit=emit):
                self.assertEqual(
                    probe_cli._render_payload(SimpleNamespace(command="validate-huawei-energy", emit=emit), payload),
                    expected,
                )
        self.assertEqual(
            probe_cli._render_payload(
                SimpleNamespace(command="validate-huawei-energy", emit="unknown"),
                payload,
            ),
            expected_json,
        )
        self.assertEqual(
            probe_cli._render_payload(
                SimpleNamespace(command="validate-huawei-energy", emit="ini"),
                {"z": 1, "a": 2, "recommendation": "invalid"},
            ),
            json.dumps({"z": 1, "a": 2, "recommendation": "invalid"}, indent=2, sort_keys=True),
        )
        empty_recommendation_payload = {
            "z": 1,
            "a": 2,
            "recommendation": {"config_snippet": ""},
        }
        self.assertEqual(
            probe_cli._render_payload(
                SimpleNamespace(command="validate-huawei-energy", emit="ini"),
                empty_recommendation_payload,
            ),
            json.dumps(empty_recommendation_payload, indent=2, sort_keys=True),
        )

        self.assertEqual(probe_cli._recommendation_emit_field("ini"), "config_snippet")
        self.assertEqual(probe_cli._recommendation_emit_field("wizard-hint"), "wizard_hint_block")
        self.assertEqual(probe_cli._recommendation_emit_field("summary"), "summary")
        self.assertIsNone(probe_cli._recommendation_emit_field("json"))

        fallback_payload = {"z": 1, "a": 2}
        self.assertEqual(
            probe_cli._render_recommendation_field({"field": " value "}, "field", fallback_payload),
            " value ",
        )
        for value in (None, "", "  ", 1):
            with self.subTest(value=value):
                self.assertEqual(
                    probe_cli._render_recommendation_field({"field": value}, "field", fallback_payload),
                    json.dumps(fallback_payload, indent=2, sort_keys=True),
                )

    def test_written_file_enrichment_is_gated_and_delegated_exactly(self) -> None:
        payload: dict[str, object] = {"recommendation": _RECOMMENDATION, "value": 1}
        for args in (
            SimpleNamespace(command="detect-modbus-energy", write_recommendation_prefix="prefix"),
            SimpleNamespace(command="validate-huawei-energy", write_recommendation_prefix=""),
            SimpleNamespace(command="validate-huawei-energy"),
        ):
            with self.subTest(args=vars(args)):
                self.assertEqual(probe_cli._payload_with_written_files(args, payload), payload)
        self.assertEqual(
            probe_cli._payload_with_written_files(
                SimpleNamespace(command="validate-huawei-energy", write_recommendation_prefix="prefix"),
                {"recommendation": "invalid", "value": 1},
            ),
            {"recommendation": "invalid", "value": 1},
        )

        args = SimpleNamespace(command="validate-huawei-energy", write_recommendation_prefix=" prefix ")
        with patch.object(probe_cli, "_write_recommendation_bundle", return_value={"manifest": "manifest.json"}) as write:
            enriched = probe_cli._payload_with_written_files(args, payload)
        write.assert_called_once_with("prefix", _RECOMMENDATION)
        self.assertEqual(enriched, {**payload, "written_files": {"manifest": "manifest.json"}})
        self.assertNotIn("written_files", payload)

    def test_bundle_writer_creates_exact_files_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = str(Path(directory) / "nested" / "deeper" / "bundle")
            written = probe_cli._write_recommendation_bundle(prefix, _RECOMMENDATION)
            self.assertEqual(
                written,
                {
                    "config_snippet": prefix + ".ini",
                    "wizard_hint": prefix + ".wizard.txt",
                    "summary": prefix + ".summary.txt",
                    "manifest": prefix + ".manifest.json",
                },
            )
            self.assertEqual(Path(written["config_snippet"]).read_text(encoding="utf-8"), str(_RECOMMENDATION["config_snippet"]))
            self.assertEqual(Path(written["wizard_hint"]).read_text(encoding="utf-8"), "wizard text")
            self.assertEqual(Path(written["summary"]).read_text(encoding="utf-8"), "summary text")
            manifest_text = Path(written["manifest"]).read_text(encoding="utf-8")
            self.assertTrue(manifest_text.endswith("\n"))
            expected_manifest = {
                    "schema_type": "energy-recommendation-bundle",
                    "schema_version": 1,
                    "source_id": "custom",
                    "profile": "huawei_profile",
                    "config_path": "/data/etc/huawei.ini",
                    "files": {
                        "config_snippet": prefix + ".ini",
                        "wizard_hint": prefix + ".wizard.txt",
                        "summary": prefix + ".summary.txt",
                    },
                }
            self.assertEqual(json.loads(manifest_text), expected_manifest)
            self.assertEqual(manifest_text, json.dumps(expected_manifest, indent=2, sort_keys=True) + "\n")

        self.assertEqual(probe_cli._recommendation_text({"value": "text"}, "value"), "text")
        self.assertEqual(probe_cli._recommendation_text({"value": 1}, "value"), "")
        self.assertEqual(probe_cli._recommendation_text({}, "value"), "")

        with tempfile.TemporaryDirectory() as directory:
            missing_prefix = str(Path(directory) / "missing")
            missing_written = probe_cli._write_recommendation_bundle(missing_prefix, {})
            missing_manifest = json.loads(Path(missing_written["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(missing_manifest["profile"], "")
        self.assertEqual(missing_manifest["config_path"], "")

        with patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write_text:
            probe_cli._write_recommendation_bundle("mocked/bundle", _RECOMMENDATION)
        self.assertEqual(len(write_text.call_args_list), 4)
        for written_call in write_text.call_args_list:
            self.assertEqual(written_call.kwargs.get("encoding"), "utf-8")

    def test_source_id_parser_requires_assignment_prefix_and_field_separator(self) -> None:
        for line, expected in (
            (" AutoEnergySource.source.Profile=value ", "source"),
            ("AutoEnergySource.source.ConfigPath=value", "source"),
            ("AutoEnergySource.source.extra.Profile=value", "source"),
            ("AutoEnergySource..Profile=value", ""),
            ("AutoEnergySource.source=value", ""),
            ("Other.source.Profile=value", ""),
            ("AutoEnergySource.source.Profile", ""),
            ("AutoEnergySource.source=value.Profile", ""),
            ("", ""),
        ):
            with self.subTest(line=line):
                self.assertEqual(probe_cli._bundle_source_id_from_config_line(line), expected)

        self.assertEqual(
            probe_cli._bundle_source_id_from_recommendation(
                {"config_snippet": "comment\nAutoEnergySource.first.Profile=x\nAutoEnergySource.second.Profile=y"}
            ),
            "first",
        )
        self.assertEqual(probe_cli._bundle_source_id_from_recommendation({"config_snippet": "invalid"}), "huawei")
        self.assertEqual(probe_cli._bundle_source_id_from_recommendation({"config_snippet": None}), "huawei")


if __name__ == "__main__":
    unittest.main()
