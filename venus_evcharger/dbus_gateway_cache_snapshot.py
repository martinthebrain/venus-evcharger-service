# SPDX-License-Identifier: GPL-3.0-or-later
"""Projection and read contracts for DBus gateway cache snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.core.contracts import timestamp_age_within
from venus_evcharger.dbus_gateway_cache_metadata import metadata_float, normalize_freshness_kind
from venus_evcharger.dbus_gateway_core import (
    CacheFreshnessKind,
    _now,
    float_or_zero,
    normalized_object_mapping,
    read_json_file,
)
from venus_evcharger.ipc.command_types import CommandPayload

__all__ = [
    "CacheLivenessPolicy",
    "cache_value_entry",
    "load_cache_snapshot",
    "project_cache_value",
]


@dataclass(frozen=True, slots=True)
class CacheLivenessPolicy:
    """Liveness inputs needed to project one mutable cache entry."""

    stale_after_seconds: float
    local_service_registered: bool
    local_service_name: str


def project_cache_value(
    item: Mapping[str, object],
    now: float,
    policy: CacheLivenessPolicy,
) -> CommandPayload:
    """Project stored timestamps and ownership into a public cache value."""
    confirmed_at = float_or_zero(item.get("confirmed_at"))
    changed_at = float_or_zero(item.get("changed_at"))
    age = _value_age(confirmed_at, now)
    return {
        **dict(item),
        "age_s": age,
        "change_age_s": _value_age(changed_at, now),
        "status": _projected_status(item, age, policy),
    }


def load_cache_snapshot(
    path: str,
    *,
    max_age_seconds: float = 30.0,
    now: float | None = None,
) -> CommandPayload:
    """Load a structurally valid cache snapshot within its age budget."""
    payload = _snapshot_payload(read_json_file(path))
    if payload is None:
        return {}
    current = _now() if now is None else float(now)
    if _snapshot_is_expired(payload, current, max_age_seconds):
        return {}
    return {str(key): value for key, value in payload.items()}


def cache_value_entry(
    snapshot: Mapping[str, object],
    key: str,
) -> CommandPayload | None:
    """Return a detached value entry from a validated cache shape."""
    values = normalized_object_mapping(snapshot.get("values"))
    if values is None:
        return None
    return normalized_object_mapping(values.get(key))


def _snapshot_payload(payload: object) -> CommandPayload | None:
    normalized = normalized_object_mapping(payload)
    if normalized is None:
        return None
    return normalized if float_or_zero(normalized.get("captured_at")) > 0.0 else None


def _snapshot_is_expired(
    payload: Mapping[str, object],
    now: float,
    max_age_seconds: float,
) -> bool:
    captured_at = float_or_zero(payload.get("captured_at"))
    return max_age_seconds >= 0.0 and not timestamp_age_within(
        captured_at,
        now,
        max_age_seconds,
    )


def _value_age(updated_at: float, now: float) -> float:
    return max(0.0, now - updated_at) if updated_at else 0.0


def _projected_status(
    item: Mapping[str, object],
    age: float,
    policy: CacheLivenessPolicy,
) -> str:
    status = str(item.get("status", "unknown"))
    freshness_kind = normalize_freshness_kind(item.get("freshness_kind"), "external_read")
    if _is_local_cache_value(item, freshness_kind, policy.local_service_name):
        return _local_value_status(status, policy.local_service_registered)
    stale_after = metadata_float(item.get("stale_after_s"), policy.stale_after_seconds)
    if _external_value_is_stale(status, freshness_kind, age, stale_after):
        return "stale"
    return status


def _is_local_cache_value(
    item: Mapping[str, object],
    freshness_kind: CacheFreshnessKind,
    service_name: str,
) -> bool:
    if not service_name:
        return False
    if freshness_kind in {"local_owned", "static"}:
        return True
    source = item.get("source")
    return (
        freshness_kind == "diagnostic"
        and isinstance(source, str)
        and source.startswith(f"{service_name}/")
    )


def _local_value_status(status: str, service_registered: bool) -> str:
    if status != "fresh":
        return status
    return "fresh" if service_registered else "unavailable"


def _external_value_is_stale(
    status: str,
    freshness_kind: CacheFreshnessKind,
    age: float,
    stale_after_seconds: float,
) -> bool:
    return (
        freshness_kind == "external_read"
        and status == "fresh"
        and stale_after_seconds > 0.0
        and age > stale_after_seconds
    )
