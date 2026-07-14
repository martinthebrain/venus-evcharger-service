# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete shape contracts for bootstrap update status artifacts."""

from __future__ import annotations

import unittest

from venus_evcharger.core.contracts_bootstrap import (
    _default_update_result,
    _normalized_bootstrap_core_fields,
    _normalized_bootstrap_flag,
    _normalized_text,
    _postprocess_bootstrap_core_fields,
    normalized_bootstrap_string_list,
    normalized_bootstrap_update_mode,
    normalized_bootstrap_update_result,
    normalized_bootstrap_update_status_fields,
)


class TestCoreContractsBootstrapContracts(unittest.TestCase):
    def test_text_mode_and_result_matrix(self) -> None:
        self.assertEqual(_normalized_text(None), "")
        self.assertEqual(_normalized_text(" value "), "value")
        self.assertEqual(_normalized_text(12), "12")
        self.assertEqual(normalized_bootstrap_update_mode(" apply "), "apply")
        self.assertEqual(normalized_bootstrap_update_mode(" dry-run "), "dry-run")
        self.assertEqual(normalized_bootstrap_update_mode(None), "apply")
        self.assertEqual(normalized_bootstrap_update_mode("invalid"), "apply")
        self.assertEqual(_default_update_result("dry-run"), "preview")
        self.assertEqual(_default_update_result("apply"), "failed")
        self.assertEqual(normalized_bootstrap_update_result("success"), "success")
        expected = {
            ("apply", "success"): "success",
            ("apply", "failed"): "failed",
            ("apply", "preview"): "failed",
            ("apply", "invalid"): "failed",
            ("dry-run", "success"): "preview",
            ("dry-run", "failed"): "failed",
            ("dry-run", "preview"): "preview",
            ("dry-run", "invalid"): "preview",
        }
        for (mode, result), normalized in expected.items():
            with self.subTest(mode=mode, result=result):
                self.assertEqual(normalized_bootstrap_update_result(result, mode=mode), normalized)

    def test_string_list_and_flag_contracts(self) -> None:
        self.assertEqual(normalized_bootstrap_string_list(None), [])
        self.assertEqual(normalized_bootstrap_string_list(("one",)), [])
        self.assertEqual(normalized_bootstrap_string_list([" one ", "", None, 2]), ["one", "2"])
        raw = {"enabled": 2, "disabled": 0, "invalid": "bad"}
        self.assertTrue(_normalized_bootstrap_flag(raw, "enabled"))
        self.assertFalse(_normalized_bootstrap_flag(raw, "disabled", 1))
        self.assertFalse(_normalized_bootstrap_flag(raw, "invalid"))
        self.assertTrue(_normalized_bootstrap_flag(raw, "missing", 1))
        self.assertFalse(_normalized_bootstrap_flag(raw, "missing"))

    def test_core_fields_contract(self) -> None:
        raw = {
            "mode": " apply ",
            "result": " failed ",
            "failure_reason": " failure ",
            "promoted_release": " release ",
            "promotion_aborted_reason": " aborted ",
            "config_merge_backup_path": " /backup ",
            "config_validation_passed": 1,
            "current_preserved": 1,
            "config_merge_changed": 1,
            "config_merge_backup_required": 1,
        }
        self.assertEqual(
            _normalized_bootstrap_core_fields(raw),
            {
                "mode": "apply",
                "result": "failed",
                "failure_reason": "failure",
                "promoted_release": "release",
                "promotion_aborted_reason": "aborted",
                "config_merge_backup_path": "/backup",
                "config_validation_passed": True,
                "current_preserved": True,
                "config_merge_changed": True,
                "config_merge_backup_required": True,
            },
        )
        successful = dict(raw, result="success")
        self.assertEqual(_normalized_bootstrap_core_fields(successful)["result"], "success")

    def test_postprocess_contracts(self) -> None:
        fields = {
            "mode": "apply",
            "result": "success",
            "failure_reason": "stale",
            "promoted_release": "release",
            "promotion_aborted_reason": "aborted",
            "config_merge_backup_path": "/backup",
            "config_validation_passed": True,
            "current_preserved": False,
            "config_merge_changed": False,
            "config_merge_backup_required": True,
        }
        result = _postprocess_bootstrap_core_fields(fields)
        self.assertIs(result, fields)
        self.assertEqual(result["failure_reason"], "")
        self.assertEqual(result["config_merge_backup_path"], "")
        self.assertEqual(result["promotion_aborted_reason"], "")
        self.assertEqual(result["promoted_release"], "release")

        preview = dict(fields, mode="dry-run", result="preview", promoted_release="release", config_merge_changed=True)
        preview["config_merge_backup_path"] = "/backup"
        result = _postprocess_bootstrap_core_fields(preview)
        self.assertEqual(result["failure_reason"], "")
        self.assertEqual(result["promoted_release"], "")
        self.assertEqual(result["config_merge_backup_path"], "")

    def test_empty_status_shape_contract(self) -> None:
        self.assertEqual(
            normalized_bootstrap_update_status_fields(None),
            {
                "timestamp_utc": "",
                "mode": "apply",
                "result": "failed",
                "failure_reason": "",
                "target_dir": "",
                "old_version": "",
                "new_version": "",
                "old_bundle_sha256": "",
                "new_bundle_sha256": "",
                "current_preserved": False,
                "already_current": False,
                "promoted_release": "",
                "promotion_aborted_reason": "",
                "rollback_reason": "",
                "config_merge_changed": False,
                "config_merge_comment_preserved": True,
                "config_merge_skipped_reason": "",
                "config_merge_backup_path": "",
                "config_merge_backup_required": False,
                "config_merge_added_keys": [],
                "config_merge_added_sections": [],
                "config_schema_before": "",
                "config_schema_target": "",
                "config_migrations_applied": [],
                "config_validation_passed": False,
                "config_validation_mode": "",
            },
        )

    def test_full_failed_status_shape_contract(self) -> None:
        payload = {
            "timestamp_utc": " timestamp ",
            "mode": "apply",
            "result": "failed",
            "failure_reason": "failure",
            "target_dir": " /target ",
            "old_version": " 1.0 ",
            "new_version": " 2.0 ",
            "old_bundle_sha256": " old-sha ",
            "new_bundle_sha256": " new-sha ",
            "current_preserved": 1,
            "already_current": 1,
            "promoted_release": " release ",
            "promotion_aborted_reason": " aborted ",
            "rollback_reason": " rollback ",
            "config_merge_changed": 1,
            "config_merge_comment_preserved": 0,
            "config_merge_skipped_reason": " skipped ",
            "config_merge_backup_path": " /backup ",
            "config_merge_backup_required": 1,
            "config_merge_added_keys": [" key "],
            "config_merge_added_sections": [" section "],
            "config_schema_before": " old-schema ",
            "config_schema_target": " new-schema ",
            "config_migrations_applied": [" migration "],
            "config_validation_passed": 1,
            "config_validation_mode": " strict ",
        }
        self.assertEqual(
            normalized_bootstrap_update_status_fields(payload),
            {
                "timestamp_utc": "timestamp",
                "mode": "apply",
                "result": "failed",
                "failure_reason": "failure",
                "target_dir": "/target",
                "old_version": "1.0",
                "new_version": "2.0",
                "old_bundle_sha256": "old-sha",
                "new_bundle_sha256": "new-sha",
                "current_preserved": True,
                "already_current": True,
                "promoted_release": "release",
                "promotion_aborted_reason": "aborted",
                "rollback_reason": "rollback",
                "config_merge_changed": True,
                "config_merge_comment_preserved": False,
                "config_merge_skipped_reason": "skipped",
                "config_merge_backup_path": "/backup",
                "config_merge_backup_required": True,
                "config_merge_added_keys": ["key"],
                "config_merge_added_sections": ["section"],
                "config_schema_before": "old-schema",
                "config_schema_target": "new-schema",
                "config_migrations_applied": ["migration"],
                "config_validation_passed": True,
                "config_validation_mode": "strict",
            },
        )


if __name__ == "__main__":
    unittest.main()
