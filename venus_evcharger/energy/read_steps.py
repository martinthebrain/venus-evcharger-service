# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit one-I/O-step contract for external energy connectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import EnergySourceDefinition, EnergySourceSnapshot


@dataclass(frozen=True, slots=True)
class EnergySourceReadStep:
    """Return either an incomplete read or one completed source snapshot."""

    snapshot: EnergySourceSnapshot | None

    @property
    def complete(self) -> bool:
        return self.snapshot is not None


class EnergySourceStepReader(Protocol):
    """Execute at most one transport operation for one configured source."""

    def __call__(
        self,
        owner: object,
        source: EnergySourceDefinition,
        observed_at: float,
    ) -> EnergySourceReadStep: ...


def completed_read(snapshot: EnergySourceSnapshot) -> EnergySourceReadStep:
    """Return one completed connector step."""
    return EnergySourceReadStep(snapshot)


def pending_read() -> EnergySourceReadStep:
    """Return one connector step whose continuation remains pending."""
    return EnergySourceReadStep(None)


__all__ = [
    "EnergySourceReadStep",
    "EnergySourceStepReader",
    "completed_read",
    "pending_read",
]
