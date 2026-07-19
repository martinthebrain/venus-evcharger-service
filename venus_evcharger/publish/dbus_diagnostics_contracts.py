# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed values returned by the diagnostic publishing components."""

from __future__ import annotations

from dataclasses import dataclass


DiagnosticValue = str | int | float


@dataclass(frozen=True)
class DiagnosticSnapshot:
    """Complete diagnostic data built for one publish cycle."""

    counters: dict[str, DiagnosticValue]
    ages: dict[str, float]
