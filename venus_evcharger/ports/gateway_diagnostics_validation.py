# SPDX-License-Identifier: GPL-3.0-or-later
"""Primitive runtime validators for the gateway diagnostics contract."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence, Set
from typing import TypeGuard

def is_string_object_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    """Narrow an untrusted JSON object to the diagnostics mapping contract."""
    return _is_object_mapping(value) and all(isinstance(key, str) for key in value)


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    """Narrow a value before validating typed tuple members."""
    return isinstance(value, tuple)


def exact_mapping(
    value: object,
    label: str,
    fields: Set[str],
) -> Mapping[str, object]:
    if not is_string_object_mapping(value):
        raise TypeError(f"{label} must be an object with string keys")
    if set(value) != fields:
        raise ValueError(f"{label} fields do not match the schema")
    return value


def object_sequence(value: object, label: str) -> Sequence[object]:
    """Parse an untrusted JSON array without accepting text as a sequence."""
    if not _is_object_sequence(value):
        raise TypeError(f"{label} must be an array")
    return value


def _is_object_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return (
        not isinstance(value, (str, bytes, bytearray))
        and isinstance(value, Sequence)
    )


def member_text(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} is invalid")
    return value


def text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be a boolean")
    return value


def non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def non_negative_float(value: object, label: str) -> float:
    result = finite_float(value, label)
    if result < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return result


def positive_float(value: object, label: str) -> float:
    result = finite_float(value, label)
    if result <= 0.0:
        raise ValueError(f"{label} must be positive")
    return result


def bounded_float(value: object, label: str, minimum: float, maximum: float) -> float:
    result = finite_float(value, label)
    if result < minimum or result > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return result


def normalized_epoch_timestamp(value: object, captured_at: object) -> float:
    """Normalize an untrusted epoch timestamp at the diagnostics boundary."""
    timestamp = _non_negative_finite_or_zero(value)
    boundary = _non_negative_finite_or_zero(captured_at)
    return min(timestamp, boundary)


def _non_negative_finite_or_zero(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    return result if math.isfinite(result) and result > 0.0 else 0.0


__all__ = [
    "boolean",
    "bounded_float",
    "exact_mapping",
    "finite_float",
    "is_object_tuple",
    "is_string_object_mapping",
    "member_text",
    "non_negative_float",
    "non_negative_int",
    "normalized_epoch_timestamp",
    "object_sequence",
    "positive_float",
    "text",
]
