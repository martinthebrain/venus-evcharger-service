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


def _soc_source_quality(source: EnergySourceSnapshot) -> tuple[bool, bool, int, float, str]:
    captured_at = source.captured_at
    has_finite_timestamp = captured_at is not None and math.isfinite(float(captured_at))
    confidence = float(source.confidence)
    finite_confidence = confidence if math.isfinite(confidence) else float("-inf")
    return (
        bool(source.online),
        has_finite_timestamp,
        int(source.physical_priority),
        finite_confidence,
        source.source_id,
    )


__all__ = ["unique_weighted_soc_sources"]
