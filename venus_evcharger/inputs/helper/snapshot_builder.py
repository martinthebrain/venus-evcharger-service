# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed projection of source measurements into helper snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement
from venus_evcharger.inputs.helper.contracts import Snapshot
from venus_evcharger.inputs.helper.snapshot_defaults import BATTERY_SNAPSHOT_FIELDS


@dataclass(frozen=True, slots=True)
class SnapshotTarget:
    """Name the atomic value and timestamp fields owned by one source."""

    name: str
    value_key: str
    captured_key: str
    monotonic_key: str


PV_TARGET = SnapshotTarget(
    "pv",
    "pv_power",
    "pv_captured_at",
    "pv_observed_monotonic",
)
BATTERY_TARGET = SnapshotTarget(
    "battery",
    "battery_soc",
    "battery_captured_at",
    "battery_observed_monotonic",
)
GRID_GATEWAY_TARGET = SnapshotTarget(
    "grid",
    "grid_gateway_power",
    "grid_gateway_captured_at",
    "grid_gateway_observed_monotonic",
)
GRID_RESULT_TARGET = SnapshotTarget(
    "grid",
    "grid_power",
    "grid_captured_at",
    "grid_observed_monotonic",
)


@dataclass(frozen=True, slots=True)
class SourceSample:
    """Carry one source payload and its observation clocks together."""

    value: object
    target: SnapshotTarget
    captured_at: float
    observed_monotonic: float


class SnapshotBuilder:
    """Apply typed source observations to one mutable snapshot."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    def apply_source(
        self,
        target: SnapshotTarget,
        value: object,
        captured_at: float,
        observed_monotonic: float,
    ) -> None:
        """Normalize and project one source payload into its owned fields."""
        if target == BATTERY_TARGET and isinstance(value, Mapping):
            self._apply_battery(value, captured_at, observed_monotonic)
            return
        measurement = TimestampedMeasurement.from_optional(
            _optional_number(value),
            captured_at,
            observed_monotonic,
        )
        self.write_measurement(target, measurement)
        self.set_source_status(target.name, measurement.available)

    def write_measurement(
        self,
        target: SnapshotTarget,
        measurement: TimestampedMeasurement[float],
    ) -> None:
        """Write a complete measurement triple without partial states."""
        self.snapshot[target.value_key] = measurement.value
        self.snapshot[target.captured_key] = measurement.captured_at
        self.snapshot[target.monotonic_key] = measurement.observed_monotonic

    def set_source_status(self, source_name: str, available: bool) -> None:
        """Update source and helper lifecycle status after one projection."""
        self.snapshot[f"{source_name}_status"] = "ok" if available else "missing"
        self.snapshot["helper_state"] = "running"
        self.snapshot["helper_status"] = "running"

    def _apply_battery(
        self,
        value: Mapping[object, object],
        captured_at: float,
        observed_monotonic: float,
    ) -> None:
        measurement = TimestampedMeasurement.from_optional(
            _optional_number(value.get("battery_soc")),
            captured_at,
            observed_monotonic,
        )
        self.write_measurement(BATTERY_TARGET, measurement)
        for field_name in BATTERY_SNAPSHOT_FIELDS[1:]:
            self.snapshot[field_name] = value.get(field_name)
        self.set_source_status("battery", measurement.available)


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


__all__ = [
    "BATTERY_TARGET",
    "GRID_GATEWAY_TARGET",
    "GRID_RESULT_TARGET",
    "PV_TARGET",
    "SnapshotBuilder",
    "SnapshotTarget",
    "SourceSample",
]
