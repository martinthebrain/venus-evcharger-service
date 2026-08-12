# SPDX-License-Identifier: GPL-3.0-or-later
"""Monotonic timestamp contracts for grid measurement fusion."""

from __future__ import annotations

import unittest

from venus_evcharger.energy.grid_fusion import GridMeasurementFusion
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig, GridMeasurement
from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement
from venus_evcharger.inputs.helper.grid_fusion_snapshot import apply_grid_fusion


def _fusion(**overrides: object) -> GridMeasurementFusion:
    values = {
        "enabled": True,
        "primary_source_id": "primary",
        "backup_source_id": "gateway",
        "primary_max_age_seconds": 10.0,
        "backup_max_age_seconds": 10.0,
        "minimum_confidence": 0.5,
        "failover_samples": 2,
        "recovery_samples": 2,
        "failover_hold_seconds": 5.0,
        "mismatch_absolute_watts": 300.0,
        "mismatch_relative": 0.15,
        "mismatch_samples": 3,
        "future_tolerance_seconds": 1.0,
    }
    values.update(overrides)
    return GridMeasurementFusion(GridFusionConfig(**values))


def _measurement(
    source_id: str,
    power_w: float | None,
    captured_at: float | None,
    observed_monotonic: float | None,
) -> GridMeasurement:
    return GridMeasurement(
        source_id=source_id,
        measurement=TimestampedMeasurement.from_optional(
            power_w,
            captured_at,
            observed_monotonic,
        ),
    )


class GridFusionTimestampContractTests(unittest.TestCase):
    def test_snapshot_boundary_keeps_complete_selected_timestamp_triple(self) -> None:
        snapshot: dict[str, object] = {
            "grid_gateway_power": 400.0,
            "grid_gateway_captured_at": 90.0,
            "grid_gateway_observed_monotonic": 500.0,
            "battery_sources": [],
        }

        apply_grid_fusion(_fusion(backup_max_age_seconds=10.0), snapshot, 500.01)

        self.assertEqual(
            (
                snapshot["grid_power"],
                snapshot["grid_captured_at"],
                snapshot["grid_observed_monotonic"],
            ),
            (400.0, 90.0, 500.0),
        )

    def test_epoch_clock_adjustment_does_not_change_freshness(self) -> None:
        fresh_after_epoch_rollback = _measurement("gateway", 120.0, 10.0, 99.0)
        future_epoch_but_stale_monotonic = _measurement("gateway", 120.0, 10_000.0, 80.0)
        missing = _measurement("primary", None, None, None)

        fresh = _fusion().resolve(missing, fresh_after_epoch_rollback, 100.0)
        stale = _fusion().resolve(missing, future_epoch_but_stale_monotonic, 100.0)

        self.assertEqual((fresh.state, fresh.power_w, fresh.backup_age_seconds), ("backup", 120.0, 1.0))
        self.assertEqual((stale.state, stale.power_w, stale.backup_age_seconds), ("unavailable", None, 20.0))

    def test_unavailable_result_clears_all_selected_measurement_fields(self) -> None:
        snapshot: dict[str, object] = {
            "grid_gateway_power": 400.0,
            "grid_gateway_captured_at": 90.0,
            "grid_gateway_observed_monotonic": None,
            "battery_sources": [],
        }

        apply_grid_fusion(_fusion(), snapshot, 100.0)

        self.assertEqual(
            (
                snapshot["grid_power"],
                snapshot["grid_captured_at"],
                snapshot["grid_observed_monotonic"],
            ),
            (None, None, None),
        )

    def test_conservative_result_carries_oldest_epoch_and_monotonic_timestamp(self) -> None:
        fusion = _fusion(mismatch_samples=1, mismatch_absolute_watts=10.0, mismatch_relative=0.0)
        primary = _measurement("primary", -900.0, 1_000.0, 99.0)
        backup = _measurement("gateway", -100.0, 900.0, 98.0)

        result = fusion.resolve(primary, backup, 100.0)

        self.assertEqual(result.state, "disagreement")
        self.assertEqual((result.captured_at, result.observed_monotonic), (900.0, 98.0))

    def test_selected_timestamp_does_not_refresh_stale_gateway_measurement(self) -> None:
        snapshot: dict[str, object] = {
            "grid_gateway_power": 400.0,
            "grid_gateway_captured_at": 80.0,
            "grid_gateway_observed_monotonic": 80.0,
            "battery_sources": [
                {
                    "source_id": "primary",
                    "grid_interaction_w": 200.0,
                    "captured_at": 99.0,
                    "observed_monotonic": 99.0,
                    "online": True,
                    "confidence": 1.0,
                }
            ],
        }

        apply_grid_fusion(_fusion(), snapshot, 100.0)
        self.assertEqual(snapshot["grid_observed_monotonic"], 99.0)
        self.assertEqual(snapshot["grid_gateway_observed_monotonic"], 80.0)

        snapshot["battery_sources"] = []
        apply_grid_fusion(_fusion(), snapshot, 105.0)
        self.assertEqual(snapshot["grid_fusion_state"], "unavailable")
        self.assertEqual(snapshot["grid_power"], None)


if __name__ == "__main__":
    unittest.main()
