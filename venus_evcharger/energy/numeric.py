# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared numeric helpers for energy aggregation and learning summaries."""

from __future__ import annotations

from typing import Iterable


def optional_float(value: object) -> float | None:
    """Return one numeric float only for already numeric inputs."""
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def optional_int(value: object) -> int | None:
    """Return one optional integer from non-boolean scalar input."""
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def non_negative_optional_float(value: object) -> float | None:
    """Return one non-negative optional float."""
    normalized = optional_float(value)
    if normalized is None:
        return None
    return max(0.0, float(normalized))


def sum_optional(values: Iterable[float | None]) -> float | None:
    """Return the sum of known values, or ``None`` when all values are unknown."""
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values)
