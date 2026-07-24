# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated OpenDTU inverter payload selection and aggregation."""

from __future__ import annotations

from typing import TypeGuard

from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import _sum_optional
from .models import EnergySourceDefinition
from .profiles import resolve_energy_source_profile


def opendtu_json_object(value: object) -> dict[str, object] | None:
    """Normalize one raw JSON object without accepting other containers."""
    if not _is_raw_json_object(value):
        return None
    return {str(key): item for key, item in value.items()}


def _is_raw_json_object(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_json_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def opendtu_online_inverters(
    inverters: tuple[dict[str, object], ...],
    max_data_age_seconds: float,
) -> tuple[dict[str, object], ...]:
    """Return only reachable inverters whose reported data is fresh."""
    return tuple(
        inverter
        for inverter in inverters
        if opendtu_inverter_online(inverter, max_data_age_seconds)
    )


def opendtu_snapshot_confidence(
    inverters: tuple[dict[str, object], ...],
    max_data_age_seconds: float,
    plausible_idle: bool,
) -> tuple[bool, float]:
    """Derive online state and confidence from the selected physical members."""
    filtered_count = len(inverters)
    reachable_count = len(
        opendtu_online_inverters(inverters, max_data_age_seconds)
    )
    online = bool(filtered_count) and (bool(reachable_count) or plausible_idle)
    confidence = (
        0.0
        if filtered_count <= 0
        else float(reachable_count) / float(filtered_count)
    )
    if plausible_idle:
        confidence = max(confidence, 1.0)
    return online, confidence


def opendtu_unique_raw_inverters(
    payload: dict[str, object],
    serial_filter: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    """Select uniquely identified members; duplicate serials fail closed."""
    raw_inverters = payload.get("inverters")
    if not _is_json_list(raw_inverters):
        return ()
    filtered = opendtu_filtered_raw_inverters(raw_inverters, serial_filter)
    serial_counts: dict[str, int] = {}
    for inverter in filtered:
        serial = opendtu_serial(inverter)
        serial_counts[serial] = serial_counts.get(serial, 0) + 1
    return tuple(
        inverter
        for inverter in filtered
        if serial_counts[opendtu_serial(inverter)] == 1
    )


def opendtu_filtered_raw_inverters(
    raw_inverters: list[object],
    serial_filter: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    """Normalize and filter raw members by explicit serial selection."""
    filtered: list[dict[str, object]] = []
    for raw_inverter in raw_inverters:
        inverter = opendtu_json_object(raw_inverter)
        if inverter is not None and opendtu_filtered_raw_inverter(
            inverter,
            serial_filter,
        ):
            filtered.append(inverter)
    return tuple(filtered)


def opendtu_filtered_raw_inverter(
    raw_inverter: object,
    serial_filter: tuple[str, ...],
) -> bool:
    """Return whether one member has a usable and selected serial."""
    inverter = opendtu_json_object(raw_inverter)
    return (
        inverter is not None
        and bool(opendtu_serial(inverter))
        and opendtu_matches_serial_filter(inverter, serial_filter)
    )


def opendtu_matches_serial_filter(
    inverter: dict[str, object],
    serial_filter: tuple[str, ...],
) -> bool:
    serial = opendtu_serial(inverter)
    return not serial_filter or serial in serial_filter


def opendtu_inverter_has_measurements(inverter: dict[str, object]) -> bool:
    return "AC" in inverter or opendtu_unreachable_idle_stub(inverter)


def opendtu_serial(inverter: dict[str, object]) -> str:
    value = inverter.get("serial")
    return "" if value is None else str(value).strip()


def opendtu_detail_inverter(
    payload: dict[str, object],
    expected_serial: str,
) -> dict[str, object]:
    """Require exactly one detail member matching the requested serial."""
    raw_inverters = payload.get("inverters")
    if not _is_json_list(raw_inverters):
        raise ValueError(
            f"OpenDTU detail response is missing inverter {expected_serial}"
        )
    matches = _matching_detail_inverters(raw_inverters, expected_serial)
    if len(matches) != 1:
        raise ValueError(
            "OpenDTU detail response does not uniquely identify inverter "
            f"{expected_serial}"
        )
    return matches[0]


def _matching_detail_inverters(
    raw_inverters: list[object],
    expected_serial: str,
) -> tuple[dict[str, object], ...]:
    return tuple(
        inverter
        for inverter in map(opendtu_json_object, raw_inverters)
        if inverter is not None and opendtu_serial(inverter) == expected_serial
    )


def opendtu_summed_ac_power(
    inverters: tuple[dict[str, object], ...],
) -> float | None:
    return _sum_optional(opendtu_ac_power(inverter) for inverter in inverters)


def opendtu_total_dc_power(
    inverters: tuple[dict[str, object], ...],
) -> float | None:
    return _sum_optional(opendtu_dc_power(inverter) for inverter in inverters)


def opendtu_any_producing(
    inverters: tuple[dict[str, object], ...],
) -> bool:
    return any(bool(inverter.get("producing")) for inverter in inverters)


def opendtu_plausible_idle_snapshot(
    payload: dict[str, object],
    inverters: tuple[dict[str, object], ...],
    *,
    ac_power_w: float | None,
    pv_input_power_w: float | None,
    max_data_age_seconds: float,
    allow_unreachable_idle: bool,
) -> bool:
    """Recognize night-time unreachable stubs without accepting radio faults."""
    checks = (
        allow_unreachable_idle,
        bool(inverters),
        not opendtu_any_producing(inverters),
        not _opendtu_has_online_inverter(inverters, max_data_age_seconds),
        _opendtu_zeroish_power(ac_power_w),
        _opendtu_zeroish_power(pv_input_power_w),
        not _opendtu_has_radio_problem(payload),
        all(opendtu_unreachable_idle_stub(item) for item in inverters),
    )
    return all(checks)


def energy_source_allows_unreachable_idle(
    source: EnergySourceDefinition,
) -> bool:
    profile = resolve_energy_source_profile(source.profile_name)
    if profile is not None:
        return profile.idle_unreachable_policy == "allow_plausible_idle"
    return source.role == "inverter"


def opendtu_inverter_online(
    inverter: dict[str, object],
    max_data_age_seconds: float,
) -> bool:
    reachable = bool(inverter.get("reachable"))
    if not reachable:
        return False
    data_age = finite_float_or_none(inverter.get("data_age"))
    if data_age is None:
        return False
    normalized_age = float(data_age)
    return 0.0 <= normalized_age <= float(max_data_age_seconds)


def opendtu_ac_power(inverter: dict[str, object]) -> float | None:
    ac = opendtu_json_object(inverter.get("AC"))
    if ac is None:
        return None
    phase = opendtu_json_object(ac.get("0"))
    if phase is None:
        return None
    return opendtu_metric_value(phase, "Power")


def opendtu_dc_power(inverter: dict[str, object]) -> float | None:
    dc = opendtu_json_object(inverter.get("DC"))
    if dc is None:
        return None
    values: list[float] = []
    for raw_channel in dc.values():
        channel = opendtu_json_object(raw_channel)
        if channel is None:
            continue
        power = opendtu_metric_value(channel, "Power")
        if power is not None:
            values.append(power)
    return _sum_optional(values)


def opendtu_metric_value(
    container: dict[str, object],
    key: str,
) -> float | None:
    raw_metric = opendtu_json_object(container.get(key))
    if raw_metric is None:
        return None
    return finite_float_or_none(raw_metric.get("v"))


def opendtu_unreachable_idle_stub(inverter: dict[str, object]) -> bool:
    return not bool(inverter.get("reachable")) and not bool(
        inverter.get("producing")
    )


def _opendtu_has_online_inverter(
    inverters: tuple[dict[str, object], ...],
    max_data_age_seconds: float,
) -> bool:
    return any(
        opendtu_inverter_online(inverter, max_data_age_seconds)
        for inverter in inverters
    )


def _opendtu_has_radio_problem(payload: dict[str, object]) -> bool:
    hints = opendtu_json_object(payload.get("hints"))
    return hints is not None and bool(hints.get("radio_problem"))


def _opendtu_zeroish_power(value: float | None) -> bool:
    return value is None or abs(float(value)) <= 0.5


__all__ = [
    "energy_source_allows_unreachable_idle",
    "opendtu_ac_power",
    "opendtu_any_producing",
    "opendtu_dc_power",
    "opendtu_detail_inverter",
    "opendtu_filtered_raw_inverter",
    "opendtu_filtered_raw_inverters",
    "opendtu_inverter_has_measurements",
    "opendtu_inverter_online",
    "opendtu_json_object",
    "opendtu_matches_serial_filter",
    "opendtu_metric_value",
    "opendtu_online_inverters",
    "opendtu_plausible_idle_snapshot",
    "opendtu_serial",
    "opendtu_snapshot_confidence",
    "opendtu_summed_ac_power",
    "opendtu_total_dc_power",
    "opendtu_unique_raw_inverters",
    "opendtu_unreachable_idle_stub",
]
