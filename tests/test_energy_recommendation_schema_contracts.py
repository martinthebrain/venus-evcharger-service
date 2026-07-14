# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for energy recommendation manifests."""

from __future__ import annotations

import unittest
from pathlib import Path

from venus_evcharger.energy import recommendation_schema as schema


class EnergyRecommendationSchemaContractTests(unittest.TestCase):
    def test_manifest_path_and_builder_are_exact(self) -> None:
        self.assertEqual(schema.recommendation_bundle_manifest_path(" /tmp/bundle "), Path(" /tmp/bundle .manifest.json"))
        written = {"config_snippet": "config.ini", "wizard_hint": "hint.txt", "summary": "summary.txt"}
        self.assertEqual(
            schema.recommendation_bundle_manifest(
                source_id="source",
                profile="profile",
                config_path="/data/etc/source.ini",
                written_files=written,
            ),
            {
                "schema_type": "energy-recommendation-bundle",
                "schema_version": 1,
                "source_id": "source",
                "profile": "profile",
                "config_path": "/data/etc/source.ini",
                "files": {
                    "config_snippet": "config.ini",
                    "wizard_hint": "hint.txt",
                    "summary": "summary.txt",
                },
            },
        )

    def test_validation_normalizes_every_text_field(self) -> None:
        payload = {
            "schema_type": " energy-recommendation-bundle ",
            "schema_version": 1,
            "source_id": " source ",
            "profile": " profile ",
            "config_path": " /data/etc/source.ini ",
            "files": {
                "config_snippet": " config.ini ",
                "wizard_hint": " hint.txt ",
                "summary": " summary.txt ",
            },
        }
        self.assertEqual(
            schema.validate_recommendation_bundle_manifest(payload),
            {
                "schema_type": "energy-recommendation-bundle",
                "schema_version": 1,
                "source_id": "source",
                "profile": "profile",
                "config_path": "/data/etc/source.ini",
                "files": {
                    "config_snippet": "config.ini",
                    "wizard_hint": "hint.txt",
                    "summary": "summary.txt",
                },
            },
        )

    def test_validation_rejects_each_invalid_boundary(self) -> None:
        valid: dict[str, object] = {
            "schema_type": "energy-recommendation-bundle",
            "schema_version": 1,
            "source_id": "source",
            "profile": "profile",
            "config_path": "config.ini",
            "files": {"config_snippet": "a", "wizard_hint": "b", "summary": "c"},
        }
        invalid_cases = (
            ({**valid, "schema_type": "wrong"}, "Unsupported recommendation bundle schema type 'wrong'"),
            ({**valid, "schema_version": 2}, "Unsupported recommendation bundle schema version '2' (expected 1)"),
            ({**valid, "source_id": " "}, "Recommendation bundle manifest is missing source_id"),
            ({**valid, "profile": ""}, "Recommendation bundle manifest is missing profile"),
            ({**valid, "config_path": None}, "Recommendation bundle manifest is missing config_path"),
            ({**valid, "files": []}, "Recommendation bundle manifest is missing files"),
        )
        for payload, message in invalid_cases:
            with self.subTest(message=message), self.assertRaises(ValueError) as raised:
                schema.validate_recommendation_bundle_manifest(payload)
            self.assertEqual(str(raised.exception), message)
        for missing_key in ("config_snippet", "wizard_hint", "summary"):
            files = dict(valid["files"])
            del files[missing_key]
            with self.subTest(missing_key=missing_key), self.assertRaises(ValueError) as raised:
                schema.validate_recommendation_bundle_manifest({**valid, "files": files})
            self.assertEqual(
                str(raised.exception),
                f"Recommendation bundle manifest is missing files.{missing_key}",
            )


if __name__ == "__main__":
    unittest.main()
