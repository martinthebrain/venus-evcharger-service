# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency-free learned charge-power profile contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import SupportsFloat, SupportsIndex, TypeAlias

NumericInput: TypeAlias = str | bytes | bytearray | SupportsFloat | SupportsIndex | None


@dataclass(frozen=True)
class LearnedChargePowerProfile:
    """Normalized learned charge-power profile kept by the update cycle."""

    state: str
    power: float | None
    updated_at: float | None
    learning_since: float | None
    sample_count: int
    phase_signature: str | None
    voltage_signature: float | None
    signature_mismatch_sessions: int
    checked_session_started_at: float | None
    confidence: float
    stability_score: float
    reason: str
    detail: str


def normalized_learning_score(value: NumericInput) -> float:
    """Return one normalized 0..1 score for learning diagnostics."""
    if value is None:
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return round(max(0.0, min(1.0, score)), 3)


def normalized_learning_text(value: object, default: str) -> str:
    """Return one compact diagnostic string."""
    text = "" if value is None else str(value).strip()
    return text or default
