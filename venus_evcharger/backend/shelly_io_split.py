# SPDX-License-Identifier: GPL-3.0-or-later
"""Split-backend PM synthesis helpers for Shelly I/O support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from venus_evcharger.backend.errors import BACKEND_OPTIONAL_CAPABILITY_ERRORS
from venus_evcharger.backend.models import ChargerState, MeterReading, PhaseSelection
from venus_evcharger.backend.shelly_io_types import (
    JsonObject,
    ShellyIoHost,
    ShellyPmStatus,
    _EnableBackendLike,
    _MeterBackendLike,
    _SwitchStateBackendLike,
    is_switch_state_backend,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    normalize_phase_value,
)
from venus_evcharger.core.contracts import finite_float_or_none


class ShellyIoSplit:
    """Synthesize PM status from split meter, switch, and charger backends."""

    if TYPE_CHECKING:
        service: ShellyIoHost

        def _runtime_now(self) -> float: ...

        def _split_switch_backend(self) -> _EnableBackendLike | None: ...

        def _split_meter_backend(self) -> _MeterBackendLike | None: ...

        def _split_switch_supported_phase_selections(self) -> tuple[PhaseSelection, ...]: ...

        def _store_runtime_switch_snapshot(self, switch_state: object | None, now: float | None = None) -> None: ...

        @staticmethod
        def _optional_bool(value: object) -> bool | None: ...

        @classmethod
        def _resolved_pm_charger_current(cls, state: ChargerState) -> float | None: ...

        def _estimated_charger_power_w(
            self,
            current_a: float | None,
            phase_selection: PhaseSelection,
        ) -> float | None: ...

        def _sync_estimated_charger_energy_cache(self, energy_kwh: float, power_w: float, now: float) -> None: ...

        def _integrated_estimated_charger_energy_kwh(self, power_w: float, now: float) -> float: ...

        def _remember_charger_estimate(self, source: str, now: float | None = None) -> None: ...

        def _clear_charger_estimate(self) -> None: ...

        def _estimated_phase_voltage_v(self, selection: PhaseSelection) -> float: ...

        def _remember_phase_selection_state(
            self,
            *,
            active: object | None = None,
            requested: object | None = None,
            supported: object | None = None,
        ) -> None: ...

        def _runtime_cached_charger_state(
            self,
            *,
            now: float | None = None,
            max_age_seconds: float | None = None,
        ) -> ChargerState | None: ...

    def _split_switch_state_backend(self) -> _SwitchStateBackendLike | None:
        backend = self._split_switch_backend()
        return backend if is_switch_state_backend(backend) else None

    def _split_switch_state(self) -> object | None:
        backend = self._split_switch_state_backend()
        if backend is None:
            self._store_runtime_switch_snapshot(None)
            return None
        switch_state = backend.read_switch_state()
        self._store_runtime_switch_snapshot(switch_state)
        return switch_state

    @staticmethod
    def _pm_status_from_meter_reading(
        reading: MeterReading,
        relay_on: bool | None = None,
    ) -> ShellyPmStatus:
        pm_status: ShellyPmStatus = {
            "apower": float(reading.power_w),
            "aenergy": {"total": float(reading.energy_kwh) * 1000.0},
        }
        if reading.voltage_v is not None:
            pm_status["voltage"] = float(reading.voltage_v)
        if reading.current_a is not None:
            pm_status["current"] = float(reading.current_a)
        resolved_relay = reading.relay_on if relay_on is None else relay_on
        ShellyIoSplit._apply_meter_output(pm_status, resolved_relay)
        ShellyIoSplit._apply_meter_phase_fields(pm_status, reading)
        return pm_status

    @staticmethod
    def _apply_meter_output(pm_status: ShellyPmStatus, resolved_relay: bool | None) -> None:
        if resolved_relay is not None:
            pm_status["output"] = bool(resolved_relay)

    @staticmethod
    def _apply_meter_phase_fields(pm_status: ShellyPmStatus, reading: MeterReading) -> None:
        if reading.phase_powers_w is not None:
            pm_status["_phase_powers_w"] = _phase_triplet(reading.phase_powers_w)
        if reading.phase_currents_a is not None:
            pm_status["_phase_currents_a"] = _phase_triplet(reading.phase_currents_a)
        pm_status["_phase_selection"] = str(reading.phase_selection)

    def _relay_state_from_split_switch(self, fallback: bool | None) -> bool | None:
        try:
            state = self._split_switch_state()
        except BACKEND_OPTIONAL_CAPABILITY_ERRORS:
            return fallback
        if state is None:
            return fallback
        enabled = getattr(state, "enabled", fallback)
        return fallback if enabled is None else bool(enabled)

    def _pm_status_from_charger_state(
        self,
        state: ChargerState,
        *,
        relay_on: bool | None,
        active_phase_selection: object | None = None,
    ) -> ShellyPmStatus:
        svc = self.service
        current_time = self._runtime_now()
        phase_selection = self._resolved_pm_phase_selection(state, active_phase_selection)
        current_a = self._resolved_pm_charger_current(state)
        power_w, power_estimated = self._resolved_pm_power(state, current_a, phase_selection)
        energy_kwh, energy_estimated = self._resolved_pm_energy(state, power_w, current_time)
        voltage_v = self._resolved_pm_voltage(svc, phase_selection, power_estimated, energy_estimated)
        self._sync_pm_estimate_marker(power_estimated, energy_estimated, current_time)
        pm_status = self._charger_pm_status_base(power_w, energy_kwh, phase_selection)
        self._apply_optional_pm_output(pm_status, relay_on)
        self._apply_optional_pm_current(pm_status, current_a)
        self._apply_optional_pm_voltage(pm_status, voltage_v)
        self._apply_phase_projection(pm_status, current_a, power_w, phase_selection, getattr(svc, "phase", "L1"))
        return pm_status

    def _resolved_pm_power(
        self,
        state: ChargerState,
        current_a: float | None,
        phase_selection: PhaseSelection,
    ) -> tuple[float, bool]:
        power_w = finite_float_or_none(state.power_w)
        if power_w is not None:
            return power_w, False
        estimated_power = self._estimated_charger_power_w(current_a, phase_selection)
        return (0.0 if estimated_power is None else estimated_power), estimated_power is not None

    def _resolved_pm_energy(self, state: ChargerState, power_w: float, current_time: float) -> tuple[float, bool]:
        energy_kwh = finite_float_or_none(state.energy_kwh)
        if energy_kwh is not None:
            self._sync_estimated_charger_energy_cache(energy_kwh, power_w, current_time)
            return energy_kwh, False
        return self._integrated_estimated_charger_energy_kwh(power_w, current_time), True

    def _resolved_pm_voltage(
        self,
        svc: ShellyIoHost,
        phase_selection: PhaseSelection,
        power_estimated: bool,
        energy_estimated: bool,
    ) -> float | None:
        voltage_v = finite_float_or_none(getattr(svc, "_last_voltage", None))
        if voltage_v is not None or not (power_estimated or energy_estimated):
            return voltage_v
        return float(self._estimated_phase_voltage_v(phase_selection))

    def _sync_pm_estimate_marker(self, power_estimated: bool, energy_estimated: bool, current_time: float) -> None:
        if power_estimated or energy_estimated:
            source = "current-voltage-phase" if power_estimated else "power-time"
            self._remember_charger_estimate(source, current_time)
            return
        self._clear_charger_estimate()

    def _apply_phase_projection(
        self,
        pm_status: ShellyPmStatus,
        current_a: float | None,
        power_w: float,
        phase_selection: PhaseSelection,
        display_phase: object,
    ) -> None:
        phase_currents = _phase_currents_for_selection(current_a, phase_selection, display_phase)
        if phase_currents is not None:
            pm_status["_phase_currents_a"] = phase_currents
        pm_status["_phase_powers_w"] = _phase_powers_for_selection(power_w, phase_selection, display_phase)

    @staticmethod
    def _charger_pm_status_base(
        power_w: float,
        energy_kwh: float,
        phase_selection: PhaseSelection,
    ) -> ShellyPmStatus:
        return {
            "apower": power_w,
            "aenergy": {"total": energy_kwh * 1000.0},
            "_phase_selection": phase_selection,
        }

    @staticmethod
    def _apply_optional_pm_output(pm_status: ShellyPmStatus, value: bool | None) -> None:
        if value is not None:
            pm_status["output"] = bool(value)

    @staticmethod
    def _apply_optional_pm_current(pm_status: ShellyPmStatus, value: float | None) -> None:
        if value is not None:
            pm_status["current"] = float(value)

    @staticmethod
    def _apply_optional_pm_voltage(pm_status: ShellyPmStatus, value: float | None) -> None:
        if value is not None:
            pm_status["voltage"] = float(value)

    def _resolved_pm_phase_selection(
        self,
        state: ChargerState,
        active_phase_selection: object | None,
    ) -> PhaseSelection:
        svc = self.service
        raw_selection = (
            active_phase_selection
            if active_phase_selection is not None
            else (
                state.phase_selection if state.phase_selection is not None else getattr(svc, "active_phase_selection", "P1")
            )
        )
        return normalize_phase_value(raw_selection, "P1")

    @staticmethod
    def _resolved_charger_current(state: ChargerState) -> float | None:
        if state.actual_current_amps is not None:
            return float(state.actual_current_amps)
        if state.current_amps is not None:
            return float(state.current_amps)
        return None

    def _safe_split_switch_state(self) -> object | None:
        try:
            return self._split_switch_state()
        except BACKEND_OPTIONAL_CAPABILITY_ERRORS:
            self._store_runtime_switch_snapshot(None)
            return None

    def _runtime_cached_charger_state_for_split(self, now: float | None) -> ChargerState | None:
        max_age_seconds = float(getattr(self.service, "auto_shelly_soft_fail_seconds", 0.0) or 0.0)
        return self._runtime_cached_charger_state(now=now, max_age_seconds=max_age_seconds)

    def _resolved_switch_overrides(
        self,
        switch_state: object | None,
        relay_on: bool | None,
        phase_selection: object | None,
    ) -> tuple[bool | None, object | None]:
        if switch_state is None:
            return relay_on, phase_selection
        enabled = getattr(switch_state, "enabled", relay_on)
        overridden_relay = relay_on if enabled is None else bool(enabled)
        overridden_phase = getattr(switch_state, "phase_selection", phase_selection)
        return overridden_relay, overridden_phase

    def _read_split_pm_status_without_meter(
        self,
        switch_state: object | None,
        supported_phase_selections: tuple[PhaseSelection, ...],
        charger_state: ChargerState | None,
        now: float | None,
    ) -> JsonObject:
        svc = self.service
        recent_charger_state = charger_state or self._runtime_cached_charger_state_for_split(now)
        if recent_charger_state is None:
            raise RuntimeError("Split mode without meter backend requires fresh charger readback")
        relay_on, active_phase_selection = self._resolved_switch_overrides(
            switch_state,
            recent_charger_state.enabled,
            recent_charger_state.phase_selection,
        )
        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=getattr(
                svc,
                "requested_phase_selection",
                active_phase_selection if active_phase_selection is not None else "P1",
            ),
            active=active_phase_selection,
        )
        return dict(
            self._pm_status_from_charger_state(
                recent_charger_state,
                relay_on=relay_on,
                active_phase_selection=active_phase_selection,
            )
        )

    def _read_split_pm_status_with_meter(
        self,
        backend: _MeterBackendLike,
        switch_state: object | None,
        supported_phase_selections: tuple[PhaseSelection, ...],
    ) -> JsonObject:
        reading = backend.read_meter()
        relay_on, active_phase_selection = self._resolved_switch_overrides(
            switch_state,
            reading.relay_on,
            reading.phase_selection,
        )
        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=getattr(self.service, "requested_phase_selection", reading.phase_selection),
            active=active_phase_selection,
        )
        return dict(self._pm_status_from_meter_reading(reading, relay_on=relay_on))

    def _read_split_pm_status(
        self,
        charger_state: ChargerState | None = None,
        *,
        now: float | None = None,
    ) -> JsonObject:
        backend = self._split_meter_backend()
        supported_phase_selections = self._split_switch_supported_phase_selections()
        switch_state = self._safe_split_switch_state()
        if backend is None:
            return self._read_split_pm_status_without_meter(
                switch_state,
                supported_phase_selections,
                charger_state,
                now,
            )
        return self._read_split_pm_status_with_meter(
            backend,
            switch_state,
            supported_phase_selections,
        )


def _phase_triplet(values: tuple[float, float, float]) -> tuple[float, float, float]:
    """Return one normalized three-phase float vector."""
    first, second, third = values
    return float(first), float(second), float(third)
