# SPDX-License-Identifier: GPL-3.0-or-later
"""Primitive runtime validators for the gateway diagnostics contract."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast


def exact_mapping(value: object, label: str, fields: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object with string keys")
    untyped = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in untyped):
        raise TypeError(f"{label} must be an object with string keys")
    if set(untyped) != fields:
        raise ValueError(f"{label} fields do not match the schema")
    return cast(Mapping[str, object], untyped)


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


__all__ = [
    "boolean",
    "bounded_float",
    "exact_mapping",
    "finite_float",
    "member_text",
    "non_negative_float",
    "non_negative_int",
    "positive_float",
    "text",
]
