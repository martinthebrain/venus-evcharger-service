# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapt auto-input snapshot fields to the grid-fusion domain contract."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.energy.grid_fusion import GridMeasurementFusion
from venus_evcharger.energy.grid_fusion_contracts import GridMeasurement
from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement
from venus_evcharger.inputs.helper.snapshot_builder import GRID_RESULT_TARGET, SnapshotBuilder


def apply_grid_fusion(
    fusion: GridMeasurementFusion,
    snapshot: dict[str, object],
    monotonic_at: float,
) -> None:
    primary = _primary_measurement(fusion, snapshot)
    backup = _backup_measurement(fusion, snapshot)
    result = fusion.resolve(primary, backup, monotonic_at)
    source_status = result.state if fusion.config.enabled else ("ok" if result.power_w is not None else "missing")
    builder = SnapshotBuilder(snapshot)
    builder.write_measurement(GRID_RESULT_TARGET, result.measurement)
    snapshot.update(
        {
            "grid_fusion_enabled": fusion.config.enabled,
            "grid_fusion_primary_source_id": fusion.config.primary_source_id,
            "grid_fusion_backup_source_id": fusion.config.backup_source_id,
            "grid_primary_power": primary.power_w,
            "grid_primary_captured_at": primary.captured_at,
            "grid_status": source_status,
            "grid_selected_source_id": result.selected_source_id,
            "grid_fusion_state": result.state,
            "grid_fusion_confidence": result.confidence,
            "grid_fusion_primary_valid": result.primary_valid,
            "grid_fusion_backup_valid": result.backup_valid,
            "grid_fusion_primary_age_seconds": result.primary_age_seconds,
            "grid_fusion_backup_age_seconds": result.backup_age_seconds,
            "grid_fusion_difference_watts": result.difference_watts,
            "grid_fusion_tolerance_watts": result.tolerance_watts,
            "grid_fusion_primary_invalid_samples": result.primary_invalid_samples,
            "grid_fusion_primary_recovery_samples": result.primary_recovery_samples,
            "grid_fusion_mismatch_samples": result.mismatch_samples,
        }
    )


def _primary_measurement(fusion: GridMeasurementFusion, snapshot: Mapping[str, object]) -> GridMeasurement:
    source_id = fusion.config.primary_source_id
    source = _source_payload(snapshot, source_id)
    if source is None:
        return GridMeasurement(
            source_id=source_id,
            measurement=TimestampedMeasurement.unavailable(),
            online=False,
            confidence=0.0,
        )
    return GridMeasurement(
        source_id=source_id,
        measurement=_numeric_measurement(
            source.get("grid_interaction_w"),
            source.get("captured_at"),
            source.get("observed_monotonic"),
        ),
        online=source.get("online") is True,
        confidence=_confidence(source.get("confidence")),
    )


def _backup_measurement(fusion: GridMeasurementFusion, snapshot: Mapping[str, object]) -> GridMeasurement:
    power = _optional_number(snapshot.get("grid_gateway_power"))
    return GridMeasurement(
        source_id=fusion.config.backup_source_id,
        measurement=_numeric_measurement(
            power,
            snapshot.get("grid_gateway_captured_at"),
            snapshot.get("grid_gateway_observed_monotonic"),
        ),
        online=power is not None,
        confidence=1.0 if power is not None else 0.0,
    )


def _source_payload(snapshot: Mapping[str, object], source_id: str) -> Mapping[object, object] | None:
    raw_sources: object = snapshot.get("battery_sources")
    if not isinstance(raw_sources, list):
        return None
    for raw_source in raw_sources:
        if isinstance(raw_source, Mapping) and _payload_source_id(raw_source) == source_id:
            return raw_source
    return None


def _payload_source_id(raw_source: Mapping[object, object]) -> str | None:
    value = raw_source.get("source_id")
    return value.strip() if isinstance(value, str) else None


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _confidence(value: object) -> float:
    numeric = _optional_number(value)
    return 0.0 if numeric is None else min(1.0, max(0.0, numeric))


def _numeric_measurement(
    value: object,
    captured_at: object,
    observed_monotonic: object,
) -> TimestampedMeasurement[float]:
    return TimestampedMeasurement.from_optional(
        _optional_number(value),
        _optional_number(captured_at),
        _optional_number(observed_monotonic),
    )
