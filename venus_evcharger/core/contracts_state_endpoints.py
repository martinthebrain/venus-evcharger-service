# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint-specific normalized local State API contract helpers."""

from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.core.contracts_basic import non_negative_float_or_none, non_negative_int, normalize_binary_flag
from venus_evcharger.core.contracts_state_shared import (
    _normalized_generic_mapping,
    _normalized_state_mapping_fields,
    normalized_state_api_kind,
    normalized_state_api_version,
)


def normalized_state_api_dbus_diagnostics_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    normalized_state = _normalized_generic_mapping(state)
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", True))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="dbus-diagnostics"),
        "state": normalized_state,
    }


def normalized_state_api_topology_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return _normalized_state_mapping_fields(payload, kind="topology")


def _normalized_state_update_mapping(value: Any) -> dict[str, Any]:
    state = _normalized_generic_mapping(value)
    for key in (
        "last_check_at",
        "last_run_at",
        "next_check_at",
        "boot_auto_due_at",
        "run_requested_at",
    ):
        if key in state:
            state[key] = non_negative_float_or_none(state.get(key))
    for key in ("available", "no_update_active"):
        if key in state:
            state[key] = bool(normalize_binary_flag(state.get(key)))
    return state


def normalized_state_api_update_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalized_state_mapping_fields(payload, kind="update")
    normalized["state"] = _normalized_state_update_mapping(normalized.get("state"))
    return normalized


def normalized_state_api_config_effective_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return _normalized_state_mapping_fields(payload, kind="config-effective")


def _normalized_state_health_mapping(value: Any) -> dict[str, Any]:
    state = _normalized_generic_mapping(value)
    for key in ("health_code", "listen_port"):
        if key in state:
            state[key] = non_negative_int(state.get(key))
    for key in (
        "fault_active",
        "runtime_overrides_active",
        "control_api_enabled",
        "control_api_running",
        "control_api_localhost_only",
        "update_stale",
    ):
        if key in state:
            state[key] = bool(normalize_binary_flag(state.get(key)))
    return state


def normalized_state_api_health_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalized_state_mapping_fields(payload, kind="health")
    normalized["state"] = _normalized_state_health_mapping(normalized.get("state"))
    return normalized
