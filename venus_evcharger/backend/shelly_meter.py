# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly-backed normalized meter backend."""

from __future__ import annotations

from typing import Mapping

from .models import MeterReading, PhaseSelection, normalize_phase_selection
from .shelly_support import (
    ShellyBackendBase,
    phase_currents_for_selection,
    phase_powers_for_selection,
    validate_shelly_profile_role,
)
from venus_evcharger.core.contracts import finite_float_or_none


def _payload_value(payload: Mapping[str, object], path: str) -> object | None:
    """Return one dotted payload value when present."""
    current: object = payload
    for part in str(path).split("."):
        token = part.strip()
        if not token or not isinstance(current, Mapping) or token not in current:
            return None
        current = current[token]
    return current


def _payload_float(payload: Mapping[str, object], *paths: str) -> float | None:
    """Return the first finite float found at the given payload paths."""
    for path in paths:
        value = finite_float_or_none(_payload_value(payload, path))
        if value is not None:
            return float(value)
    return None


def _phase_values(
    payload: Mapping[str, object],
    *,
    suffixes: tuple[str, ...],
) -> tuple[float, float, float] | None:
    """Return per-phase values when the payload exposes them explicitly."""
    values = (
        _phase_value(payload, "a", suffixes),
        _phase_value(payload, "b", suffixes),
        _phase_value(payload, "c", suffixes),
    )
    if not _phase_values_found(values):
        return None
    return float(values[0] or 0.0), float(values[1] or 0.0), float(values[2] or 0.0)


def _phase_value(payload: Mapping[str, object], prefix: str, suffixes: tuple[str, ...]) -> float | None:
    """Return one per-phase value from a Shelly meter payload."""
    return _payload_float(payload, *(f"{prefix}_{suffix}" for suffix in suffixes))


def _phase_values_found(values: tuple[float | None, float | None, float | None]) -> bool:
    """Return whether any per-phase payload value is present."""
    return any(value is not None for value in values)


def _average_nonzero(values: tuple[float, float, float] | None) -> float | None:
    """Return the average of present non-zero values."""
    if values is None:
        return None
    present = [float(value) for value in values if float(value) > 0.0]
    if not present:
        return None
    return sum(present) / float(len(present))


class ShellyMeterBackend(ShellyBackendBase):
    """Read normalized power, current, voltage, and energy from one Shelly target."""

    def __init__(self, service: object, config_path: str = "") -> None:
        super().__init__(service, config_path=config_path)
        validate_shelly_profile_role(self.settings.profile_name, "meter")

    def read_meter(self) -> MeterReading:
        """Return one normalized Shelly meter reading."""
        pm_status = self._pm_status()
        relay_on = bool(pm_status["output"]) if "output" in pm_status else None
        phase_powers, phase_currents, phase_voltages, phase_energies_wh = self._phase_measurements(pm_status)
        power_w = self._total_power_w(pm_status, phase_powers)
        current_a = self._total_current_a(pm_status, phase_currents)
        voltage_v = self._voltage_v(pm_status, phase_voltages)
        energy_kwh = self._energy_kwh(pm_status, phase_energies_wh)
        selection = self.settings.phase_selection
        normalized_phase_powers = self._normalized_phase_powers(selection, phase_powers, power_w)
        normalized_phase_currents = self._normalized_phase_currents(selection, phase_currents, current_a)
        return MeterReading(
            relay_on=relay_on,
            power_w=float(power_w or 0.0),
            voltage_v=voltage_v,
            current_a=current_a,
            energy_kwh=float(energy_kwh),
            phase_selection=selection,
            phase_powers_w=normalized_phase_powers,
            phase_currents_a=normalized_phase_currents,
        )

    @staticmethod
    def _phase_measurements(
        pm_status: Mapping[str, object],
    ) -> tuple[
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
    ]:
        """Return explicit per-phase Shelly meter measurements from one payload."""
        return (
            _phase_values(pm_status, suffixes=("act_power", "apower", "power")),
            _phase_values(pm_status, suffixes=("current",)),
            _phase_values(pm_status, suffixes=("voltage",)),
            _phase_values(pm_status, suffixes=("total_act_energy", "total_energy")),
        )

    @staticmethod
    def _total_power_w(pm_status: Mapping[str, object], phase_powers: tuple[float, float, float] | None) -> float | None:
        """Return total Shelly power from direct or per-phase readings."""
        power_w = _payload_float(pm_status, "apower", "total_act_power", "act_power", "power")
        return sum(phase_powers) if power_w is None and phase_powers is not None else power_w

    @staticmethod
    def _total_current_a(
        pm_status: Mapping[str, object],
        phase_currents: tuple[float, float, float] | None,
    ) -> float | None:
        """Return total Shelly current from direct or per-phase readings."""
        current_a = _payload_float(pm_status, "current", "total_current")
        return sum(phase_currents) if current_a is None and phase_currents is not None else current_a

    @staticmethod
    def _voltage_v(pm_status: Mapping[str, object], phase_voltages: tuple[float, float, float] | None) -> float | None:
        """Return representative Shelly voltage from direct or averaged phase readings."""
        voltage_v = _payload_float(pm_status, "voltage")
        return _average_nonzero(phase_voltages) if voltage_v is None else voltage_v

    @staticmethod
    def _energy_kwh(
        pm_status: Mapping[str, object],
        phase_energies_wh: tuple[float, float, float] | None,
    ) -> float:
        """Return total Shelly energy in kWh from direct or per-phase counters."""
        total_wh = _payload_float(pm_status, "aenergy.total", "total_act_energy", "total_energy")
        if total_wh is None and phase_energies_wh is not None:
            total_wh = sum(phase_energies_wh)
        return 0.0 if total_wh is None else float(total_wh) / 1000.0

    def _normalized_phase_powers(
        self,
        selection: object,
        phase_powers: tuple[float, float, float] | None,
        power_w: float | None,
    ) -> tuple[float, float, float]:
        """Return explicit or derived normalized phase powers."""
        if phase_powers is not None:
            return phase_powers
        normalized_selection = self._meter_phase_selection(selection)
        return phase_powers_for_selection(power_w or 0.0, normalized_selection, self._single_phase_line())

    def _normalized_phase_currents(
        self,
        selection: object,
        phase_currents: tuple[float, float, float] | None,
        current_a: float | None,
    ) -> tuple[float, float, float] | None:
        """Return explicit or derived normalized phase currents."""
        if phase_currents is not None:
            return phase_currents
        normalized_selection = self._meter_phase_selection(selection)
        return phase_currents_for_selection(current_a, normalized_selection, self._single_phase_line())

    @staticmethod
    def _meter_phase_selection(selection: object) -> PhaseSelection:
        """Return one normalized phase selection for derived meter projections."""
        return normalize_phase_selection(selection)

    def _single_phase_line(self) -> str:
        """Return the configured single measured line for derived phase vectors."""
        raw_line = getattr(self.service, "phase", None)
        if raw_line is None:
            return "L1"
        line = str(raw_line).strip().upper()
        if line == "L2":
            return "L2"
        if line == "L3":
            return "L3"
        return "L1"
