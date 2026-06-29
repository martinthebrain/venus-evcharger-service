# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached charger-state helpers for Shelly I/O runtime support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from venus_evcharger.backend.models import ChargerState
from venus_evcharger.backend.shelly_io_types import normalize_phase_value
from venus_evcharger.core.contracts import finite_float_or_none

if TYPE_CHECKING:
    from venus_evcharger.backend.shelly_io_types import ShellyIoHost


class ShellyIoRuntimeCache:
    """Rebuild normalized charger state from the service runtime cache."""

    if TYPE_CHECKING:
        service: ShellyIoHost

    @staticmethod
    def _cached_optional_text(value: object) -> str | None:
        return None if value is None else str(value)

    def _runtime_cached_charger_state(
        self,
        *,
        now: float | None = None,
        max_age_seconds: float | None = None,
    ) -> ChargerState | None:
        captured_at = self._cached_charger_state_timestamp(now=now, max_age_seconds=max_age_seconds)
        if captured_at is None:
            return None
        state = self._cached_charger_state_snapshot()
        if not self._charger_state_has_cached_data(state):
            return None
        return state

    def _cached_charger_state_timestamp(
        self,
        *,
        now: float | None = None,
        max_age_seconds: float | None = None,
    ) -> float | None:
        captured_at = finite_float_or_none(getattr(self.service, "_last_charger_state_at", None))
        if captured_at is None:
            return None
        if max_age_seconds is None:
            return captured_at
        current = self.service._time_now() if now is None else float(now)
        if (current - captured_at) > max(0.0, float(max_age_seconds)):
            return None
        return captured_at

    def _cached_charger_state_snapshot(self) -> ChargerState:
        svc = self.service
        enabled = getattr(svc, "_last_charger_state_enabled", None)
        phase_selection_raw = getattr(svc, "_last_charger_state_phase_selection", None)
        return ChargerState(
            enabled=None if enabled is None else bool(enabled),
            current_amps=finite_float_or_none(getattr(svc, "_last_charger_state_current_amps", None)),
            phase_selection=(
                None if phase_selection_raw is None else normalize_phase_value(phase_selection_raw, "P1")
            ),
            actual_current_amps=finite_float_or_none(getattr(svc, "_last_charger_state_actual_current_amps", None)),
            power_w=finite_float_or_none(getattr(svc, "_last_charger_state_power_w", None)),
            energy_kwh=finite_float_or_none(getattr(svc, "_last_charger_state_energy_kwh", None)),
            status_text=self._cached_optional_text(getattr(svc, "_last_charger_state_status", None)),
            fault_text=self._cached_optional_text(getattr(svc, "_last_charger_state_fault", None)),
        )

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
