# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for bootstrap updater preview, status, and audit artifacts."""

from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.core.contracts_basic import normalize_binary_flag

BOOTSTRAP_UPDATE_MODES = frozenset({"apply", "dry-run"})
BOOTSTRAP_UPDATE_RESULTS = frozenset({"success", "failed", "preview"})


def _normalized_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalized_bootstrap_update_mode(value: Any) -> str:
    mode = _normalized_text(value)
    return mode if mode in BOOTSTRAP_UPDATE_MODES else "apply"


def _default_update_result(mode: str) -> str:
    return "preview" if mode == "dry-run" else "failed"


def normalized_bootstrap_update_result(value: Any, *, mode: Any = None) -> str:
    normalized_mode = normalized_bootstrap_update_mode(mode)
    result = _normalized_text(value)
    if result not in BOOTSTRAP_UPDATE_RESULTS:
        return _default_update_result(normalized_mode)
    remapped_results = {
        "dry-run": {"success": "preview"},
        "apply": {"preview": "failed"},
    }
    return remapped_results[normalized_mode].get(result, result)


def normalized_bootstrap_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized_items: list[str] = []
    for item in value:
        text = _normalized_text(item)
        if text:
            normalized_items.append(text)
    return normalized_items


def _normalized_bootstrap_flag(raw: Mapping[str, Any], key: str, default: Any = 0) -> bool:
    return bool(normalize_binary_flag(raw.get(key, default)))


def _normalized_bootstrap_core_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    mode = normalized_bootstrap_update_mode(raw.get("mode"))
    return {
        "mode": mode,
        "result": normalized_bootstrap_update_result(raw.get("result"), mode=mode),
        "failure_reason": _normalized_text(raw.get("failure_reason")),
        "promoted_release": _normalized_text(raw.get("promoted_release")),
        "promotion_aborted_reason": _normalized_text(raw.get("promotion_aborted_reason")),
        "config_merge_backup_path": _normalized_text(raw.get("config_merge_backup_path")),
        "config_validation_passed": _normalized_bootstrap_flag(raw, "config_validation_passed"),
        "current_preserved": _normalized_bootstrap_flag(raw, "current_preserved"),
        "config_merge_changed": _normalized_bootstrap_flag(raw, "config_merge_changed"),
        "config_merge_backup_required": _normalized_bootstrap_flag(raw, "config_merge_backup_required"),
    }


def _postprocess_bootstrap_core_fields(fields: dict[str, Any]) -> dict[str, Any]:
    if fields["result"] in {"success", "preview"}:
        fields["failure_reason"] = ""
    if not fields["config_merge_changed"]:
        fields["config_merge_backup_path"] = ""
    if not fields["current_preserved"]:
        fields["promotion_aborted_reason"] = ""
    if fields["mode"] == "dry-run":
        fields["promoted_release"] = ""
        fields["config_merge_backup_path"] = ""
    return fields


def normalized_bootstrap_update_status_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    core = _postprocess_bootstrap_core_fields(_normalized_bootstrap_core_fields(raw))

    return {
        "timestamp_utc": _normalized_text(raw.get("timestamp_utc")),
        "mode": core["mode"],
        "result": core["result"],
        "failure_reason": core["failure_reason"],
        "target_dir": _normalized_text(raw.get("target_dir")),
        "old_version": _normalized_text(raw.get("old_version")),
        "new_version": _normalized_text(raw.get("new_version")),
        "old_bundle_sha256": _normalized_text(raw.get("old_bundle_sha256")),
        "new_bundle_sha256": _normalized_text(raw.get("new_bundle_sha256")),
        "old_source_commit": _normalized_text(raw.get("old_source_commit")),
        "new_source_commit": _normalized_text(raw.get("new_source_commit")),
        "source_repo": _normalized_text(raw.get("source_repo")),
        "source_channel": _normalized_text(raw.get("source_channel")),
        "deployment_receipt_path": _normalized_text(raw.get("deployment_receipt_path")),
        "current_preserved": core["current_preserved"],
        "already_current": _normalized_bootstrap_flag(raw, "already_current"),
        "promoted_release": core["promoted_release"],
        "promotion_aborted_reason": core["promotion_aborted_reason"],
        "rollback_reason": _normalized_text(raw.get("rollback_reason")),
        "config_merge_changed": core["config_merge_changed"],
        "config_merge_comment_preserved": _normalized_bootstrap_flag(raw, "config_merge_comment_preserved", True),
        "config_merge_skipped_reason": _normalized_text(raw.get("config_merge_skipped_reason")),
        "config_merge_backup_path": core["config_merge_backup_path"],
        "config_merge_backup_required": core["config_merge_backup_required"],
        "config_merge_added_keys": normalized_bootstrap_string_list(raw.get("config_merge_added_keys")),
        "config_merge_added_sections": normalized_bootstrap_string_list(raw.get("config_merge_added_sections")),
        "config_schema_before": _normalized_text(raw.get("config_schema_before")),
        "config_schema_target": _normalized_text(raw.get("config_schema_target")),
        "config_migrations_applied": normalized_bootstrap_string_list(raw.get("config_migrations_applied")),
        "config_validation_passed": core["config_validation_passed"],
        "config_validation_mode": _normalized_text(raw.get("config_validation_mode")),
    }
