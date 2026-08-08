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
) -> EnergyInputsSnapshot:
    """Return one coherent semantic view without exposing DBus identities."""
    return EnergyInputsSnapshot(
        sequence=max(0, int(sequence)),
        captured_at=float(captured_at),
        topology_generation=discovery.generation,
        grid_power_w=_measurement(values.get("grid_power_w"), discovery.source_ids("grid")),
        pv_power_w=_measurement(
            values.get("pv_power_w"),
            (*discovery.source_ids("pv_ac"), *discovery.source_ids("pv_dc")),
        ),
        battery_soc=_measurement(values.get("battery_soc"), discovery.source_ids("battery")),
    )


def _measurement(entry: Mapping[str, object] | None, source_ids: tuple[str, ...]) -> MeasuredValue:
    if entry is None:
        return MeasuredValue(None, 0.0, "unknown", 0.0, source_ids, "not-observed")
    status = _value_status(entry.get("status"))
    value = _numeric_value(entry.get("value"))
    if value is None and status in {"fresh", "stale"}:
        status = "unavailable"
    return MeasuredValue(
        value=value,
        observed_at=_non_negative_number(entry.get("confirmed_at") or entry.get("updated_at")),
        status=status,
        confidence=_confidence(entry.get("confidence")),
        source_ids=source_ids,
        reason_code=_reason_code(status, value, entry.get("reason_code")),
    )


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
    explicit: object = "",
) -> str:
    if isinstance(explicit, str) and explicit:
        return explicit
    if value is None:
        return "not-observed" if status == "unknown" else "source-unavailable"
    return {"stale": "observation-stale", "error": "source-error"}.get(status, "")
