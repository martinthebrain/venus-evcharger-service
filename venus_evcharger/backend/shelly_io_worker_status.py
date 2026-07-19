# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed Shelly PM status normalization helpers for the I/O worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeGuard

from venus_evcharger.backend.shelly_io_types import (
    ShellyEnergyData,
    ShellyPmStatus,
    is_object_dict,
    is_object_sequence,
)


def normalized_energy_payload(value: object) -> ShellyEnergyData:
    payload: ShellyEnergyData = {}
    if is_object_dict(value):
        total = value.get("total")
        if _numeric_value(total):
            payload["total"] = float(total)
    payload.setdefault("total", 0.0)
    return payload


def local_pm_status_payload(raw_status: Mapping[str, object]) -> ShellyPmStatus:
    """Return a typed local PM payload seeded from the last known status."""
    pm_status: ShellyPmStatus = {}
    _copy_known_status_scalars(raw_status, pm_status)
    _copy_known_status_energy(raw_status, pm_status)
    _copy_known_status_phase_fields(raw_status, pm_status)
    return pm_status


def _copy_known_status_scalars(raw_status: Mapping[str, object], pm_status: ShellyPmStatus) -> None:
    output = raw_status.get("output")
    if output is not None:
        pm_status["output"] = bool(output)
    _copy_float_field(raw_status, pm_status, "apower")
    _copy_float_field(raw_status, pm_status, "current")
    _copy_float_field(raw_status, pm_status, "voltage")
    confirmed = raw_status.get("_pm_confirmed")
    if confirmed is not None:
        pm_status["_pm_confirmed"] = bool(confirmed)


def _copy_known_status_energy(raw_status: Mapping[str, object], pm_status: ShellyPmStatus) -> None:
    energy = raw_status.get("aenergy")
    if is_object_dict(energy):
        pm_status["aenergy"] = normalized_energy_payload(energy)


def _copy_known_status_phase_fields(raw_status: Mapping[str, object], pm_status: ShellyPmStatus) -> None:
    phase_selection = raw_status.get("_phase_selection")
    if phase_selection is not None:
        pm_status["_phase_selection"] = str(phase_selection)
    powers = _phase_tuple(raw_status.get("_phase_powers_w"))
    if powers is not None:
        pm_status["_phase_powers_w"] = powers
    currents = _phase_tuple(raw_status.get("_phase_currents_a"))
    if currents is not None:
        pm_status["_phase_currents_a"] = currents


def _copy_float_field(raw_status: Mapping[str, object], pm_status: ShellyPmStatus, key: str) -> None:
    value = raw_status.get(key)
    setter = _FLOAT_FIELD_SETTERS.get(key)
    if setter is not None and _numeric_value(value):
        setter(pm_status, float(value))


def _set_apower(pm_status: ShellyPmStatus, value: float) -> None:
    pm_status["apower"] = value


def _set_current(pm_status: ShellyPmStatus, value: float) -> None:
    pm_status["current"] = value


def _set_voltage(pm_status: ShellyPmStatus, value: float) -> None:
    pm_status["voltage"] = value


def _phase_tuple(value: object) -> tuple[float, float, float] | None:
    items = _phase_tuple_items(value)
    if items is None:
        return None
    first, second, third = items
    return float(first), float(second), float(third)


def _phase_tuple_items(value: object) -> tuple[int | float, int | float, int | float] | None:
    candidate = _phase_tuple_candidate(value)
    if candidate is None or not _numeric_triplet(candidate):
        return None
    return candidate


def _phase_tuple_candidate(value: object) -> tuple[object, object, object] | None:
    if not is_object_sequence(value):
        return None
    if len(value) != 3:
        return None
    first, second, third = value
    return first, second, third


def _numeric_triplet(value: tuple[object, object, object]) -> TypeGuard[tuple[int | float, int | float, int | float]]:
    return all(_numeric_value(item) for item in value)


def _numeric_value(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_FLOAT_FIELD_SETTERS: dict[str, Callable[[ShellyPmStatus, float], None]] = {
    "apower": _set_apower,
    "current": _set_current,
    "voltage": _set_voltage,
}
