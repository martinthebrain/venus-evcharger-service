# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for normalized local State API v1 payloads."""

from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.core.contracts_basic import normalize_binary_flag

STATE_API_VERSIONS = frozenset({"v1"})
STATE_API_KINDS = frozenset(
    {
        "build",
        "contracts",
        "automation",
        "healthz",
        "summary",
        "runtime",
        "operational",
        "dbus-diagnostics",
        "topology",
        "update",
        "victron-bias-recommendation",
        "config-effective",
        "health",
        "version",
    }
)


def _normalized_text(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return text or default


def _optional_float(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def normalized_state_api_version(value: Any) -> str:
    version = _normalized_text(value)
    return version if version in STATE_API_VERSIONS else "v1"


def normalized_state_api_kind(value: Any, *, default: str | None = None) -> str:
    normalized_default = default if default in STATE_API_KINDS else "summary"
    kind = _normalized_text(value).lower()
    return kind if kind in STATE_API_KINDS else normalized_default


def normalized_state_api_summary_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", True))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind")),
        "summary": _normalized_text(raw.get("summary")),
    }


def normalized_state_api_runtime_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", True))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="runtime"),
        "state": dict(state) if isinstance(state, Mapping) else {},
    }


def _normalized_generic_mapping(value: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized[str(key)] = item
    return normalized


def _normalized_state_mapping_fields(payload: Mapping[str, Any] | None, *, kind: str) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", True))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default=kind),
        "state": _normalized_generic_mapping(raw.get("state")),
    }
