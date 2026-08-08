# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata contracts and normalization for gateway cache values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict

from venus_evcharger.dbus_gateway_core import CacheFreshnessKind, CacheSourceState

_NUMERIC_METADATA_TYPES = (str, bytes, bytearray, int, float)
_CACHE_FRESHNESS_KINDS: dict[str, CacheFreshnessKind] = {
    "external_read": "external_read",
    "local_owned": "local_owned",
    "static": "static",
    "diagnostic": "diagnostic",
}

__all__ = [
    "CacheValueMetadata",
    "ExternalReadMetadata",
    "merge_cache_value_metadata",
    "metadata_float",
    "normalize_freshness_kind",
]


@dataclass(frozen=True, slots=True)
class CacheValueMetadata:
    """Normalized metadata attached to one cache observation."""

    source: str
    status: str = "fresh"
    confidence: float = 1.0
    last_error: str = ""
    now: float | None = None
    freshness_kind: CacheFreshnessKind = "external_read"
    source_state: CacheSourceState = "active"
    stale_after_seconds: float | None = None
    confirmed: bool = True
    reason_code: str = ""


class ExternalReadMetadata(TypedDict, total=False):
    """Optional metadata accepted by the external-read cache boundary."""

    source: str
    status: str
    confidence: float
    last_error: str
    now: float | None
    stale_after_seconds: float | None
    source_state: CacheSourceState
    confirmed: bool
    reason_code: str


def merge_cache_value_metadata(
    metadata: CacheValueMetadata | None,
    fields: Mapping[str, object],
) -> CacheValueMetadata:
    """Merge untyped boundary fields over normalized cache metadata."""
    if metadata is None:
        return _metadata_from_fields(fields)
    if not fields:
        return metadata
    return CacheValueMetadata(
        source=str(fields.get("source", metadata.source)),
        status=str(fields.get("status", metadata.status)),
        confidence=metadata_float(fields.get("confidence"), metadata.confidence),
        last_error=str(fields.get("last_error", metadata.last_error)),
        now=_metadata_now(fields.get("now"), metadata.now),
        freshness_kind=normalize_freshness_kind(fields.get("freshness_kind"), metadata.freshness_kind),
        source_state=_normalize_source_state(fields.get("source_state"), metadata.source_state),
        stale_after_seconds=_optional_metadata_float(
            fields.get("stale_after_seconds"),
            metadata.stale_after_seconds,
        ),
        confirmed=_metadata_bool(fields.get("confirmed"), metadata.confirmed),
        reason_code=str(fields.get("reason_code", metadata.reason_code)),
    )


def normalize_freshness_kind(
    value: object,
    fallback: CacheFreshnessKind,
) -> CacheFreshnessKind:
    """Normalize a boundary value to a supported cache freshness policy."""
    normalized = str(value) if value is not None else fallback
    return _CACHE_FRESHNESS_KINDS.get(normalized, fallback)


def _normalize_source_state(
    value: object,
    fallback: CacheSourceState,
) -> CacheSourceState:
    """Normalize a boundary value to a supported source state."""
    normalized = str(value) if value is not None else fallback
    if normalized == "active":
        return "active"
    if normalized == "unavailable":
        return "unavailable"
    if normalized == "error":
        return "error"
    return fallback


def metadata_float(value: object, fallback: float) -> float:
    """Return a numeric metadata value or its explicit fallback."""
    if value is None:
        return fallback
    if isinstance(value, _NUMERIC_METADATA_TYPES):
        return float(value)
    return fallback


def _metadata_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _metadata_from_fields(fields: Mapping[str, object]) -> CacheValueMetadata:
    return CacheValueMetadata(
        source=str(fields.get("source", "")),
        status=str(fields.get("status", "fresh")),
        confidence=metadata_float(fields.get("confidence"), 1.0),
        last_error=str(fields.get("last_error", "")),
        now=_metadata_now(fields.get("now")),
        freshness_kind=normalize_freshness_kind(fields.get("freshness_kind"), "external_read"),
        source_state=_normalize_source_state(fields.get("source_state"), "active"),
        stale_after_seconds=_optional_metadata_float(fields.get("stale_after_seconds")),
        confirmed=_metadata_bool(fields.get("confirmed"), True),
        reason_code=str(fields.get("reason_code", "")),
    )


def _optional_metadata_float(
    value: object,
    fallback: float | None = None,
) -> float | None:
    if value is None:
        return fallback
    numeric_fallback = 0.0 if fallback is None else fallback
    return max(0.0, metadata_float(value, numeric_fallback))


def _metadata_now(
    value: object,
    fallback: float | None = None,
) -> float | None:
    if value is None:
        return fallback
    if isinstance(value, _NUMERIC_METADATA_TYPES):
        return float(value)
    return fallback
