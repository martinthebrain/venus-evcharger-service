# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation primitives shared by semantic energy IPC contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from venus_evcharger.ipc.energy_types import (
    ENERGY_IPC_SCHEMA_VERSION,
    EnergyRefreshScope,
    EnergyRefreshUrgency,
    EnergySourceKind,
    EnergySourceState,
    EnergyValueStatus,
)

_VALUE_STATUSES = frozenset({"fresh", "stale", "unavailable", "error", "unknown"})
_SOURCE_KINDS = frozenset({"grid", "pv_ac", "pv_dc", "battery"})
_SOURCE_STATES = frozenset({"online", "offline", "unknown"})
_REFRESH_SCOPES = frozenset({"all", "grid", "pv", "battery", "topology", "energy_source"})
_REFRESH_URGENCIES = frozenset({"normal", "priority"})
_REFRESH_SCOPE_VALUES: Mapping[str, EnergyRefreshScope] = {
    "all": "all",
    "grid": "grid",
    "pv": "pv",
    "battery": "battery",
    "topology": "topology",
    "energy_source": "energy_source",
}


def mapping(value: object, label: str) -> Mapping[str, object]:
    if not _is_mapping(value) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def exact_fields(value: Mapping[str, object], *, required: set[str], label: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing or extra:
        raise ValueError(f"{label} fields mismatch missing={sorted(missing)} extra={sorted(extra)}")


def text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    return normalized


def optional_text(value: object, label: str) -> str | None:
    return None if value is None else text(value, label)


def text_tuple(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{label} must be a sequence")
    normalized = tuple(text(item, label) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must contain unique values")
    return normalized


def unique_text_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{label} must be a tuple")
    return text_tuple(value, label)


def value_status(value: object) -> EnergyValueStatus:
    normalized = _literal(value, _VALUE_STATUSES, "measurement status")
    if normalized == "fresh":
        return "fresh"
    if normalized == "stale":
        return "stale"
    if normalized == "unavailable":
        return "unavailable"
    if normalized == "error":
        return "error"
    return "unknown"


def source_kind(value: object) -> EnergySourceKind:
    normalized = _literal(value, _SOURCE_KINDS, "energy source kind")
    if normalized == "grid":
        return "grid"
    if normalized == "pv_ac":
        return "pv_ac"
    if normalized == "pv_dc":
        return "pv_dc"
    return "battery"


def source_state(value: object) -> EnergySourceState:
    normalized = _literal(value, _SOURCE_STATES, "energy source state")
    if normalized == "online":
        return "online"
    if normalized == "offline":
        return "offline"
    return "unknown"


def refresh_scope(value: object) -> EnergyRefreshScope:
    normalized = _literal(value, _REFRESH_SCOPES, "energy refresh scope")
    return _REFRESH_SCOPE_VALUES[normalized]


def refresh_urgency(value: object) -> EnergyRefreshUrgency:
    normalized = _literal(value, _REFRESH_URGENCIES, "energy refresh urgency")
    return "priority" if normalized == "priority" else "normal"


def optional_finite_float(value: object, label: str) -> float | None:
    return None if value is None else _finite_float(value, label)


def positive_float(value: object, label: str) -> float:
    normalized = _finite_float(value, label)
    if normalized <= 0.0:
        raise ValueError(f"{label} must be positive")
    return normalized


def non_negative_float(value: object, label: str) -> float:
    normalized = _finite_float(value, label)
    if normalized < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return normalized


def bounded_float(value: object, label: str, minimum: float, maximum: float) -> float:
    normalized = _finite_float(value, label)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return normalized


def non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def schema_version(
    value: object,
    label: str,
    *,
    expected: int = ENERGY_IPC_SCHEMA_VERSION,
) -> int:
    version = non_negative_int(value, f"{label} schema_version")
    if version != expected:
        raise ValueError(f"{label} has an unsupported schema_version")
    return version


def _literal(value: object, allowed: frozenset[str], label: str) -> str:
    normalized = text(value, label)
    if normalized not in allowed:
        raise ValueError(f"{label} has invalid value {normalized!r}")
    return normalized


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite")
    return normalized


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


__all__ = [
    "bounded_float",
    "exact_fields",
    "mapping",
    "non_negative_float",
    "non_negative_int",
    "optional_finite_float",
    "optional_text",
    "positive_float",
    "refresh_scope",
    "refresh_urgency",
    "schema_version",
    "source_kind",
    "source_state",
    "text",
    "text_tuple",
    "unique_text_tuple",
    "value_status",
]
