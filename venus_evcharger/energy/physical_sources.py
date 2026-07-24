# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical-source identity rules for multi-path battery observations."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .models import EnergySourceSnapshot


def unique_weighted_soc_sources(
    sources: Iterable[EnergySourceSnapshot],
) -> tuple[EnergySourceSnapshot, ...]:
    """Select one deterministic weighted-SOC observation per physical battery."""
    independent: list[EnergySourceSnapshot] = []
    physical: dict[str, EnergySourceSnapshot] = {}
    for source in sources:
        if not source.physical_id:
            independent.append(source)
            continue
        existing = physical.get(source.physical_id)
        if existing is None or _soc_source_quality(source) > _soc_source_quality(existing):
            physical[source.physical_id] = source
    return tuple((*independent, *physical.values()))


def _soc_source_quality(source: EnergySourceSnapshot) -> tuple[bool, float, float, str]:
    captured_at = _finite_quality_value(source.captured_at, float("-inf"))
    confidence = _finite_quality_value(source.confidence, float("-inf"))
    return (
        bool(source.online),
        captured_at,
        confidence,
        source.source_id,
    )


def _finite_quality_value(value: float | None, fallback: float) -> float:
    if value is None:
        return fallback
    numeric = float(value)
    return numeric if math.isfinite(numeric) else fallback


__all__ = ["unique_weighted_soc_sources"]
