# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts for atomic charger and switch readback snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from venus_evcharger.backend.models import ChargerState, SwitchState


@dataclass(frozen=True, slots=True)
class TimedChargerState:
    """One immutable charger state captured at a single point in time."""

    state: ChargerState
    captured_at: float


@dataclass(frozen=True, slots=True)
class TimedSwitchState:
    """One immutable switch state captured at a single point in time."""

    state: SwitchState
    captured_at: float


@dataclass(frozen=True, slots=True)
class ReadbackSnapshots:
    """One atomic view of all backend readbacks owned by the runtime."""

    charger: TimedChargerState | None
    switch: TimedSwitchState | None


class ReadbackStore(Protocol):
    """Read-only port exposed to update-cycle policy code."""

    def snapshot(self) -> ReadbackSnapshots: ...  # pragma: no cover


class MutableReadbackStore(Protocol):
    """Writer port exposed only to backend readback producers."""

    def snapshot(self) -> ReadbackSnapshots: ...  # pragma: no cover

    def replace_charger(self, snapshot: TimedChargerState | None) -> None: ...  # pragma: no cover

    def replace_switch(self, snapshot: TimedSwitchState | None) -> None: ...  # pragma: no cover


__all__ = [
    "MutableReadbackStore",
    "ReadbackSnapshots",
    "ReadbackStore",
    "TimedChargerState",
    "TimedSwitchState",
]
