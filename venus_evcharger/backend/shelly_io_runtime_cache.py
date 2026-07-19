# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic cached charger-state helpers for Shelly I/O runtime support."""

from __future__ import annotations

import math
from collections.abc import Callable

from venus_evcharger.backend.models import ChargerState
from venus_evcharger.ports.readback import MutableReadbackStore


class ShellyRuntimeCache:
    """Resolve complete charger states from the shared readback store."""

    def __init__(self, store: MutableReadbackStore, clock: Callable[[], float]) -> None:
        self.store = store
        self._clock = clock

    @staticmethod
    def _cached_optional_text(value: object) -> str | None:
        return None if value is None else str(value)

    def cached_charger_state(
        self,
        *,
        now: float | None = None,
        max_age_seconds: float | None = None,
    ) -> ChargerState | None:
        snapshot = self.store.snapshot().charger
        if snapshot is None:
            return None
        if self._cached_charger_state_expired(snapshot.captured_at, now, max_age_seconds):
            return None
        if not self._charger_state_has_cached_data(snapshot.state):
            return None
        return snapshot.state

    def _cached_charger_state_expired(
        self,
        captured_at: float,
        now: float | None,
        max_age_seconds: float | None,
    ) -> bool:
        if not math.isfinite(captured_at):
            return True
        if max_age_seconds is None:
            return False
        current = self._clock() if now is None else float(now)
        return (current - captured_at) > max(0.0, float(max_age_seconds))

    @staticmethod
    def _charger_state_has_cached_data(state: ChargerState) -> bool:
        return any(
            value is not None
            for value in (
                state.enabled,
                state.current_amps,
                state.phase_selection,
                state.actual_current_amps,
                state.power_w,
                state.energy_kwh,
                state.status_text,
                state.fault_text,
            )
        )


__all__ = ["ShellyRuntimeCache"]
