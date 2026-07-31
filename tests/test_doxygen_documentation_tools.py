# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.dev.check_doxygen_output import (
    _expected_function_counts,
    _require_complete_member_count,
)
from scripts.dev.doxygen_python_filter import english_brief, filter_source


class TestDoxygenDocumentationTools(unittest.TestCase):
    def test_identifier_briefs_are_grammatical_and_specific(self) -> None:
        self.assertEqual(
            english_brief("supports_phase_selection"),
            "Return whether the callable supports phase selection.",
        )
        self.assertEqual(
            english_brief("uses_split_backends"),
            "Return whether the callable uses split backends.",
        )
        self.assertEqual(
            english_brief("needs_early_rescan"),
            "Return whether the callable requires early rescan.",
        )
        self.assertEqual(english_brief("run"), "Run the service operation.")
        self.assertEqual(english_brief("custom_transition"), "Handle custom transition.")
        self.assertEqual(
            english_brief("charger_supports_phase_selection"),
            "Return whether charger supports phase selection.",
        )
        self.assertNotIn("requested condition", english_brief("load"))

    def test_filter_adds_build_only_briefs_without_changing_function_body(self) -> None:
        source = "def supports_feature() -> bool:\n    return True\n"
        filtered = filter_source(source, "example.py")

        self.assertIn("## @brief Return whether the callable supports feature.", filtered)
        self.assertIn(source, filtered)

    def test_manifest_counts_must_account_for_nested_callables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "function_count": 12,
                        "doxygen_member_count": 10,
                        "nested_function_count": 2,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(_expected_function_counts(manifest), (12, 10, 2))

            manifest.write_text(
                json.dumps(
                    {
                        "function_count": 12,
                        "doxygen_member_count": 10,
                        "nested_function_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "counts are inconsistent"):
                _expected_function_counts(manifest)

    def test_doxygen_member_count_is_exact(self) -> None:
        _require_complete_member_count([object(), object()], 2)
        with self.assertRaisesRegex(SystemExit, "expected exactly 3"):
            _require_complete_member_count([object(), object()], 3)


if __name__ == "__main__":
    unittest.main()
