# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard_render_text as render_text


class _FixedDateTime:
    @classmethod
    def now(cls) -> object:
        class _Now:
            def strftime(self, fmt: str) -> str:
                return f"formatted:{fmt}"

        return _Now()


class WizardRenderTextContractTests(unittest.TestCase):
    def test_paths_and_timestamp_contracts_are_stable(self) -> None:
        self.assertEqual(render_text.repo_root(), render_text.Path(render_text.__file__).resolve().parents[2])
        self.assertTrue((render_text.repo_root() / "venus_evcharger" / "bootstrap" / "wizard_render_text.py").exists())
        self.assertEqual(
            render_text.default_template_path().relative_to(render_text.repo_root()).as_posix(),
            "deploy/venus/config.venus_evcharger.ini",
        )
        self.assertEqual(render_text.default_config_path(), render_text.default_template_path())
        with patch.object(render_text, "datetime", _FixedDateTime):
            self.assertEqual(render_text.timestamp(), "formatted:%Y%m%d-%H%M%S")

    def test_case_preserving_parser_keeps_option_name_case(self) -> None:
        parser = render_text.CasePreservingConfigParser()
        parser.read_string("[DEFAULT]\nMixedCase=1\nlower=2\n")

        self.assertIn("MixedCase", parser["DEFAULT"])
        self.assertIn("lower", parser["DEFAULT"])
        self.assertNotIn("mixedcase", parser["DEFAULT"])

    def test_mode_value_maps_only_supported_modes(self) -> None:
        self.assertEqual(render_text.mode_value("manual"), "0")
        self.assertEqual(render_text.mode_value("auto"), "1")
        self.assertEqual(render_text.mode_value("scheduled"), "2")
        with self.assertRaises(KeyError):
            render_text.mode_value("unsupported")

    def test_replace_assignment_replaces_exact_key_and_preserves_final_newline(self) -> None:
        self.assertEqual(
            render_text.replace_assignment("Host=old\nOtherHost=keep\n", "Host", "new"),
            "Host=new\nOtherHost=keep\n",
        )
        with self.assertRaisesRegex(ValueError, "missing required key 'Host'"):
            render_text.replace_assignment("OtherHost=keep\n", "Host", "new")

    def test_replace_optional_assignment_handles_none_float_and_other_values(self) -> None:
        original = "Power=0\nName=old\n"

        self.assertEqual(render_text.replace_optional_assignment(original, "Power", None), original)
        self.assertEqual(render_text.replace_optional_assignment(original, "Power", 12.50), "Power=12.5\nName=old\n")
        self.assertEqual(render_text.replace_optional_assignment(original, "Name", "new"), "Power=0\nName=new\n")

    def test_upsert_default_assignments_updates_inserts_and_appends_predictably(self) -> None:
        self.assertEqual(render_text.upsert_default_assignments("Body=1\n", {}), "Body=1\n")
        self.assertEqual(
            render_text.upsert_default_assignments(
                "[DEFAULT]\nExisting=old\n\n[Other]\nValue=1\n",
                {"Existing": "new", "Added": "2"},
            ),
            "[DEFAULT]\nExisting=new\n\nAdded=2\n[Other]\nValue=1\n",
        )
        self.assertEqual(
            render_text.upsert_default_assignments("[DEFAULT]\nExisting=old\n", {"Added": "2"}),
            "[DEFAULT]\nExisting=old\n\nAdded=2\n",
        )
        self.assertEqual(render_text.upsert_default_assignments("Body=1\n", {"Added": "2"}), "Body=1\n\nAdded=2\n")
        self.assertEqual(
            render_text.upsert_default_assignments("[Other]\nValue=1\n", {"Added": "2"}),
            "[Other]\nValue=1\n\nAdded=2\n",
        )

    def test_remove_section_removes_only_exact_section_body(self) -> None:
        self.assertEqual(
            render_text.remove_section("[A]\na=1\n[Backends]\nb=2\n[BackendsExtra]\nc=3\n", "Backends"),
            "[A]\na=1\n[BackendsExtra]\nc=3\n",
        )
        self.assertEqual(render_text.remove_section("[Backends]\nb=2\n", "Backends"), "\n")
        self.assertEqual(render_text.remove_section("lead=1\n[Other]\na=1\n", "Backends"), "lead=1\n[Other]\na=1\n")

    def test_append_backends_replaces_existing_backend_section_or_removes_it(self) -> None:
        original = "[DEFAULT]\nHost=x\n\n[Backends]\nOld=1\n"

        self.assertEqual(render_text.append_backends(original, []), "[DEFAULT]\nHost=x\n")
        self.assertEqual(
            render_text.append_backends(original, ["Mode=split", "MeterType=none"]),
            "[DEFAULT]\nHost=x\n\n[Backends]\nMode=split\nMeterType=none\n",
        )

    def test_section_header_detection_is_strict(self) -> None:
        self.assertTrue(render_text._is_section_header("[DEFAULT]"))
        self.assertFalse(render_text._is_section_header(" [DEFAULT]"))
        self.assertFalse(render_text._is_section_header("[DEFAULT] "))

    def test_default_assignment_private_helpers_report_inserted_state_and_leading_spacing(self) -> None:
        remaining = {"Added": "2"}
        rendered, inserted = render_text._maybe_insert_default_assignments(
            "[Other]",
            ["[DEFAULT]"],
            remaining,
            inserted=False,
            in_default_section=True,
        )

        self.assertTrue(inserted)
        self.assertEqual(rendered, ["[DEFAULT]", "Added=2"])
        self.assertEqual(remaining, {})
        self.assertEqual(render_text._remaining_default_assignment_lines([], {"Added": "2"}), ["Added=2"])
        with self.assertRaises(TypeError) as inserted_error:
            render_text._maybe_insert_default_assignments(
                "[Other]",
                [],
                {},
                inserted=cast(bool, None),
                in_default_section=True,
            )
        self.assertEqual(str(inserted_error.exception), "inserted and in_default_section must be bool")
        with self.assertRaises(TypeError) as section_error:
            render_text._maybe_insert_default_assignments(
                "[Other]",
                [],
                {},
                inserted=False,
                in_default_section=cast(bool, None),
            )
        self.assertEqual(str(section_error.exception), "inserted and in_default_section must be bool")


if __name__ == "__main__":
    unittest.main()
