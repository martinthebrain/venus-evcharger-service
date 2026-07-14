# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI rendering and bundle writing helpers for energy probe output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from .recommendation_schema import recommendation_bundle_manifest, recommendation_bundle_manifest_path


_VALIDATE_COMMAND = "validate-huawei-energy"
_TEXT_ENCODING = "utf-8"
_SOURCE_PREFIX = "AutoEnergySource."


def _json_text(payload: Mapping[str, object], *, trailing_newline: bool = False) -> str:
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    return rendered + "\n" if trailing_newline else rendered


def _render_payload(args: argparse.Namespace, payload: Mapping[str, object]) -> str:
    emit_mode = str(args.emit)
    if args.command != _VALIDATE_COMMAND:
        return _json_text(payload)
    recommendation = payload.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return _json_text(payload)
    field_name = _recommendation_emit_field(emit_mode)
    if field_name is None:
        return _json_text(payload)
    return _render_recommendation_field(recommendation, field_name, payload)


def _recommendation_emit_field(emit_mode: str) -> str | None:
    emit_fields = {
        "ini": "config_snippet",
        "wizard-hint": "wizard_hint_block",
        "summary": "summary",
    }
    return emit_fields.get(emit_mode)


def _render_recommendation_field(
    recommendation: Mapping[str, object],
    field_name: str,
    payload: Mapping[str, object],
) -> str:
    value = recommendation.get(field_name)
    if isinstance(value, str) and value.strip():
        return value
    return _json_text(payload)


def _payload_with_written_files(args: argparse.Namespace, payload: Mapping[str, object]) -> dict[str, object]:
    prefix = str(getattr(args, "write_recommendation_prefix", None) or "").strip()
    if args.command != _VALIDATE_COMMAND or not prefix:
        return dict(payload)
    recommendation = payload.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return dict(payload)
    enriched = dict(payload)
    enriched["written_files"] = _write_recommendation_bundle(prefix, recommendation)
    return enriched


def _write_recommendation_bundle(prefix: str, recommendation: Mapping[str, object]) -> dict[str, str]:
    base_prefix = str(Path(prefix))
    targets = _recommendation_bundle_targets(base_prefix)
    _write_recommendation_bundle_contents(targets, recommendation)
    written_files = {key: str(path) for key, path in targets.items()}
    manifest_path = _write_recommendation_manifest(base_prefix, recommendation, written_files)
    return {**written_files, "manifest": str(manifest_path)}


def _recommendation_bundle_targets(base_prefix: str) -> dict[str, Path]:
    return {
        "config_snippet": Path(base_prefix + ".ini"),
        "wizard_hint": Path(base_prefix + ".wizard.txt"),
        "summary": Path(base_prefix + ".summary.txt"),
    }


def _write_recommendation_bundle_contents(
    targets: Mapping[str, Path],
    recommendation: Mapping[str, object],
) -> None:
    contents = {
        "config_snippet": _recommendation_text(recommendation, "config_snippet"),
        "wizard_hint": _recommendation_text(recommendation, "wizard_hint_block"),
        "summary": _recommendation_text(recommendation, "summary"),
    }
    for path in targets.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for key, path in targets.items():
        path.write_text(contents[key], encoding=_TEXT_ENCODING)


def _write_recommendation_manifest(
    base_prefix: str,
    recommendation: Mapping[str, object],
    written_files: Mapping[str, str],
) -> Path:
    manifest_path = recommendation_bundle_manifest_path(base_prefix)
    manifest = recommendation_bundle_manifest(
        source_id=_bundle_source_id_from_recommendation(recommendation),
        profile=str(recommendation.get("suggested_profile") or "").strip(),
        config_path=str(recommendation.get("suggested_config_path") or "").strip(),
        written_files=written_files,
    )
    manifest_path.write_text(_json_text(manifest, trailing_newline=True), encoding=_TEXT_ENCODING)
    return manifest_path


def _bundle_source_id_from_recommendation(recommendation: Mapping[str, object]) -> str:
    raw_snippet = recommendation.get("config_snippet")
    if not isinstance(raw_snippet, str):
        return "huawei"
    config_snippet = raw_snippet
    for raw_line in config_snippet.splitlines():
        source_id = _bundle_source_id_from_config_line(raw_line)
        if source_id:
            return source_id
    return "huawei"


def _recommendation_text(recommendation: Mapping[str, object], field_name: str) -> str:
    value = recommendation.get(field_name)
    return value if isinstance(value, str) else ""


def _bundle_source_id_from_config_line(raw_line: str) -> str:
    line = raw_line.strip()
    if not line.startswith(_SOURCE_PREFIX):
        return ""
    source_path = line[len(_SOURCE_PREFIX) :]
    if "." not in source_path:
        return ""
    source_id, _, field_assignment = source_path.partition(".")
    if "=" not in field_assignment:
        return ""
    return source_id.strip()
