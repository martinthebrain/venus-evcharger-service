# SPDX-License-Identifier: GPL-3.0-or-later
"""Build transport-neutral energy snapshots from adapter-owned cache state."""

from __future__ import annotations

import math
from collections.abc import Mapping

from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, EnergyValueStatus, MeasuredValue


def energy_inputs_snapshot(
    values: Mapping[str, Mapping[str, object]],
    discovery: DbusEnergyDiscoveryManager,
    *,
    sequence: int,
    captured_at: float,
    captured_monotonic: float,
) -> EnergyInputsSnapshot:
    """Return one coherent semantic view without exposing DBus identities."""
    native_battery_power = _measurement(
        values.get("battery_net_power_w"),
        discovery.source_ids("battery"),
    )
    return EnergyInputsSnapshot(
        sequence=max(0, int(sequence)),
        captured_at=float(captured_at),
        captured_monotonic=float(captured_monotonic),
        topology_generation=discovery.generation,
        grid_power_w=_measurement(values.get("grid_power_w"), discovery.source_ids("grid")),
        pv_power_w=_measurement(
            values.get("pv_power_w"),
            (*discovery.source_ids("pv_ac"), *discovery.source_ids("pv_dc")),
        ),
        battery_soc=_measurement(values.get("battery_soc"), discovery.source_ids("battery")),
        battery_net_power_w=_semantic_battery_power(native_battery_power),
    )


def _semantic_battery_power(measurement: MeasuredValue) -> MeasuredValue:
    """Normalize Victron battery power to positive discharge and negative charge."""
    value = measurement.value
    return MeasuredValue(
        value=None if value is None else -float(value),
        observed_at=measurement.observed_at,
        observed_monotonic=measurement.observed_monotonic,
        status=measurement.status,
        confidence=measurement.confidence,
        source_ids=measurement.source_ids,
        reason_code=measurement.reason_code,
    )


def _measurement(entry: Mapping[str, object] | None, source_ids: tuple[str, ...]) -> MeasuredValue:
    if entry is None:
        return _unknown_measurement(source_ids)
    value = _numeric_value(entry.get("value"))
    status = _measurement_status(entry.get("status"), value)
    return MeasuredValue(
        value=value,
        observed_at=_entry_timestamp(entry, "confirmed_at", "updated_at"),
        observed_monotonic=_entry_timestamp(
            entry,
            "confirmed_monotonic",
            "updated_monotonic",
        ),
        status=status,
        confidence=_confidence(entry.get("confidence")),
        source_ids=source_ids,
        reason_code=_reason_code(status, value, entry.get("reason_code")),
    )


def _unknown_measurement(source_ids: tuple[str, ...]) -> MeasuredValue:
    """Return the canonical representation for an unobserved semantic field."""
    return MeasuredValue(
        None,
        0.0,
        "unknown",
        0.0,
        source_ids,
        "not-observed",
        observed_monotonic=0.0,
    )


def _measurement_status(raw_status: object, value: float | None) -> EnergyValueStatus:
    """Prevent fresh/stale claims when their numeric value is unavailable."""
    status = _value_status(raw_status)
    if value is None and status in {"fresh", "stale"}:
        return "unavailable"
    return status


def _entry_timestamp(
    entry: Mapping[str, object],
    confirmed_key: str,
    updated_key: str,
) -> float:
    """Prefer a confirmation timestamp and fall back to the last update."""
    return _non_negative_number(entry.get(confirmed_key) or entry.get(updated_key))


def _numeric_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _non_negative_number(value: object) -> float:
    numeric = _numeric_value(value)
    return max(0.0, numeric) if numeric is not None else 0.0


def _confidence(value: object) -> float:
    numeric = _numeric_value(value)
    return min(1.0, max(0.0, numeric)) if numeric is not None else 0.0


def _value_status(value: object) -> EnergyValueStatus:
    if value == "fresh":
        return "fresh"
    if value == "stale":
        return "stale"
    if value == "unavailable":
        return "unavailable"
    if value == "error":
        return "error"
    return "unknown"


def _reason_code(
    status: EnergyValueStatus,
    value: float | None,
    explicit: object,
) -> str:
    if isinstance(explicit, str) and explicit:
        return explicit
    if value is None:
        return "not-observed" if status == "unknown" else "source-unavailable"
    return {"stale": "observation-stale", "error": "source-error"}.get(status, "")
