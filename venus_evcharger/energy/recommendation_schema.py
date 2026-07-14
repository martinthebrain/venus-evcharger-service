# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema helpers for external energy recommendation bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, TypedDict

RECOMMENDATION_BUNDLE_SCHEMA_TYPE = "energy-recommendation-bundle"
RECOMMENDATION_BUNDLE_SCHEMA_VERSION = 1


class RecommendationBundleManifestFiles(TypedDict):
    """Validated file paths referenced by one recommendation bundle."""

    config_snippet: str
    wizard_hint: str
    summary: str


class RecommendationBundleManifest(TypedDict):
    """Validated recommendation bundle manifest payload."""

    schema_type: str
    schema_version: int
    source_id: str
    profile: str
    config_path: str
    files: RecommendationBundleManifestFiles


def recommendation_bundle_manifest_path(prefix: str) -> Path:
    """Return the manifest path for one recommendation bundle prefix."""
    return Path(str(Path(prefix)) + ".manifest.json")


def recommendation_bundle_manifest(
    *,
    source_id: str,
    profile: str,
    config_path: str,
    written_files: Mapping[str, str],
) -> RecommendationBundleManifest:
    """Build a versioned manifest for one recommendation bundle."""
    return {
        "schema_type": RECOMMENDATION_BUNDLE_SCHEMA_TYPE,
        "schema_version": RECOMMENDATION_BUNDLE_SCHEMA_VERSION,
        "source_id": source_id,
        "profile": profile,
        "config_path": config_path,
        "files": {
            "config_snippet": str(written_files["config_snippet"]),
            "wizard_hint": str(written_files["wizard_hint"]),
            "summary": str(written_files["summary"]),
        },
    }


def _manifest_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    return "" if value is None else str(value).strip()


def _manifest_required_string(payload: Mapping[str, object], key: str) -> str:
    value = _manifest_string(payload, key)
    if value:
        return value
    raise ValueError(f"Recommendation bundle manifest is missing {key}")


def _manifest_files(payload: Mapping[str, object]) -> Mapping[str, object]:
    files = payload.get("files")
    if isinstance(files, Mapping):
        return files
    raise ValueError("Recommendation bundle manifest is missing files")


def _normalized_manifest_files(files: Mapping[str, object]) -> RecommendationBundleManifestFiles:
    normalized_files: RecommendationBundleManifestFiles = {
        "config_snippet": _manifest_string(files, "config_snippet"),
        "wizard_hint": _manifest_string(files, "wizard_hint"),
        "summary": _manifest_string(files, "summary"),
    }
    for key, value in normalized_files.items():
        if not value:
            raise ValueError(f"Recommendation bundle manifest is missing files.{key}")
    return normalized_files


def validate_recommendation_bundle_manifest(payload: Mapping[str, object]) -> RecommendationBundleManifest:
    """Validate and normalize one recommendation bundle manifest."""
    schema_type = _manifest_string(payload, "schema_type")
    if schema_type != RECOMMENDATION_BUNDLE_SCHEMA_TYPE:
        raise ValueError(f"Unsupported recommendation bundle schema type '{schema_type}'")
    schema_version = payload.get("schema_version")
    if schema_version != RECOMMENDATION_BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported recommendation bundle schema version "
            f"'{schema_version}' (expected {RECOMMENDATION_BUNDLE_SCHEMA_VERSION})"
        )
    source_id = _manifest_required_string(payload, "source_id")
    profile = _manifest_required_string(payload, "profile")
    config_path = _manifest_required_string(payload, "config_path")
    normalized_files = _normalized_manifest_files(_manifest_files(payload))
    return {
        "schema_type": RECOMMENDATION_BUNDLE_SCHEMA_TYPE,
        "schema_version": RECOMMENDATION_BUNDLE_SCHEMA_VERSION,
        "source_id": source_id,
        "profile": profile,
        "config_path": config_path,
        "files": normalized_files,
    }
