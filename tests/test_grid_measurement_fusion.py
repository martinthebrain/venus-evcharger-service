# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math
import unittest
from typing import Any

from venus_evcharger.energy.grid_fusion import GridMeasurementFusion
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig, GridFusionResult, GridMeasurement
from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement
from venus_evcharger.inputs.helper import grid_fusion_snapshot as snapshot_adapter
from venus_evcharger.inputs.helper.grid_fusion_snapshot import apply_grid_fusion


def _config(**overrides: object) -> GridFusionConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "primary_source_id": "huawei",
        "backup_source_id": "victron",
        "primary_max_age_seconds": 5.0,
        "backup_max_age_seconds": 5.0,
        "minimum_confidence": 0.5,
        "failover_samples": 2,
        "recovery_samples": 2,
        "failover_hold_seconds": 5.0,
        "mismatch_absolute_watts": 300.0,
        "mismatch_relative": 0.1,
        "mismatch_samples": 2,
        "future_tolerance_seconds": 1.0,
    }
    values.update(overrides)
    return GridFusionConfig(**values)


def _measurement(
    source_id: str,
    power_w: float | None,
    captured_at: float | None = 100.0,
    *,
    online: bool = True,
    confidence: float = 1.0,
) -> GridMeasurement:
    return GridMeasurement(
        source_id,
        TimestampedMeasurement.from_optional(power_w, captured_at, captured_at),
        online,
        confidence,
    )


class GridMeasurementContractTests(unittest.TestCase):
    def test_measurement_usability_checks_shape_time_and_confidence(self) -> None:
        usable = _measurement("huawei", -500.0)
        kwargs = {
            "max_age_seconds": 5.0,
            "minimum_confidence": 0.5,
            "future_tolerance_seconds": 1.0,
        }
        self.assertTrue(usable.is_usable(100.0, **kwargs))
        self.assertTrue(_measurement("huawei", 0.0, 101.0).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", None).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, None).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, online=False).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", math.inf).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, math.nan).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, confidence=math.inf).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, 94.9).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, 101.1).is_usable(100.0, **kwargs))
        self.assertFalse(_measurement("huawei", 1.0, confidence=0.49).is_usable(100.0, **kwargs))

    def test_measurement_age_is_bounded_and_handles_missing_or_non_finite_time(self) -> None:
        self.assertEqual(_measurement("huawei", 1.0, 99.0).age_seconds(100.0), 1.0)
        self.assertEqual(_measurement("huawei", 1.0, 101.0).age_seconds(100.0), 0.0)
        self.assertIsNone(_measurement("huawei", 1.0, None).age_seconds(100.0))
        self.assertIsNone(_measurement("huawei", 1.0, math.inf).age_seconds(100.0))

    def test_config_rejects_invalid_contracts(self) -> None:
        invalid = (
            ({"primary_source_id": ""}, "Grid fusion requires a primary source id"),
            ({"backup_source_id": ""}, "Grid fusion requires a backup source id"),
            ({"failover_samples": 0}, "Grid fusion sample thresholds must be positive"),
            ({"recovery_samples": 0}, "Grid fusion sample thresholds must be positive"),
            ({"mismatch_samples": 0}, "Grid fusion sample thresholds must be positive"),
            ({"primary_max_age_seconds": -1.0}, "Grid fusion freshness limits must be non-negative"),
            ({"backup_max_age_seconds": -1.0}, "Grid fusion freshness limits must be non-negative"),
            ({"minimum_confidence": -0.1}, "Grid fusion minimum confidence must be between zero and one"),
            ({"minimum_confidence": 1.1}, "Grid fusion minimum confidence must be between zero and one"),
            ({"failover_hold_seconds": -1.0}, "Grid fusion time tolerances must be non-negative"),
            ({"future_tolerance_seconds": -1.0}, "Grid fusion time tolerances must be non-negative"),
            ({"mismatch_absolute_watts": -1.0}, "Grid fusion mismatch tolerances must be non-negative"),
            ({"mismatch_relative": -0.1}, "Grid fusion mismatch tolerances must be non-negative"),
        )
        for override, message in invalid:
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, f"^{message}$"):
                _config(**override)
        self.assertEqual(GridFusionConfig().primary_source_id, "")

    def test_config_accepts_every_inclusive_contract_boundary(self) -> None:
        lower = _config(
            primary_max_age_seconds=0.0,
            backup_max_age_seconds=0.0,
            minimum_confidence=0.0,
            failover_samples=1,
            recovery_samples=1,
            failover_hold_seconds=0.0,
            mismatch_absolute_watts=0.0,
            mismatch_relative=0.0,
            mismatch_samples=1,
            future_tolerance_seconds=0.0,
        )
        self.assertEqual(lower.minimum_confidence, 0.0)
        self.assertEqual(lower.failover_samples, 1)
        upper = _config(minimum_confidence=1.0)
        self.assertEqual(upper.minimum_confidence, 1.0)


class GridMeasurementFusionTests(unittest.TestCase):
    def test_disabled_fusion_is_exact_gateway_passthrough(self) -> None:
        fusion = GridMeasurementFusion(GridFusionConfig())
        result = fusion.resolve(_measurement("", -900.0, 99.0), _measurement("victron", -400.0, 98.0), 100.0)
        self.assertEqual(
            result,
            GridFusionResult(
                measurement=TimestampedMeasurement.observed(
                    -400.0,
                    captured_at=98.0,
                    observed_monotonic=98.0,
                ),
                selected_source_id="victron",
                state="backup",
                confidence=1.0,
                primary_valid=True,
                backup_valid=True,
                primary_age_seconds=1.0,
                backup_age_seconds=2.0,
                difference_watts=500.0,
                tolerance_watts=300.0,
                primary_invalid_samples=0,
                primary_recovery_samples=0,
                mismatch_samples=1,
            ),
        )
        missing = fusion.resolve(_measurement("", None), _measurement("victron", None), 100.0)
        self.assertEqual((missing.power_w, missing.selected_source_id, missing.state, missing.confidence), (None, "", "unavailable", 0.0))

    def test_primary_and_backup_apply_their_own_freshness_limits(self) -> None:
        primary_strict = GridMeasurementFusion(_config(primary_max_age_seconds=1.0, backup_max_age_seconds=10.0))
        result = primary_strict.resolve(_measurement("huawei", 10.0, 95.0), _measurement("victron", 20.0, 95.0), 100.0)
        self.assertEqual((result.primary_valid, result.backup_valid, result.power_w), (False, True, 20.0))

        backup_strict = GridMeasurementFusion(_config(primary_max_age_seconds=10.0, backup_max_age_seconds=1.0))
        result = backup_strict.resolve(_measurement("huawei", 10.0, 95.0), _measurement("victron", 20.0, 95.0), 100.0)
        self.assertEqual((result.primary_valid, result.backup_valid, result.power_w), (True, False, 10.0))

    def test_initial_selection_prefers_primary_then_backup(self) -> None:
        fusion = GridMeasurementFusion(_config())
        primary = fusion.resolve(_measurement("huawei", -500.0), _measurement("victron", -450.0), 100.0)
        self.assertEqual((primary.state, primary.selected_source_id), ("primary", "huawei"))
        refreshed = fusion.resolve(_measurement("huawei", -510.0), _measurement("victron", -450.0), 100.0)
        self.assertEqual((refreshed.state, refreshed.power_w, refreshed.primary_invalid_samples), ("primary", -510.0, 0))

        backup_fusion = GridMeasurementFusion(_config())
        backup = backup_fusion.resolve(_measurement("huawei", None), _measurement("victron", 100.0), 100.0)
        self.assertEqual((backup.state, backup.selected_source_id), ("backup", "victron"))

        unavailable = GridMeasurementFusion(_config()).resolve(
            _measurement("huawei", None),
            _measurement("victron", None),
            100.0,
        )
        self.assertEqual(unavailable.state, "unavailable")

    def test_primary_outage_holds_then_fails_over_and_recovers_hysteretically(self) -> None:
        fusion = GridMeasurementFusion(_config())
        backup = _measurement("victron", -300.0)
        self.assertEqual(fusion.resolve(_measurement("huawei", -600.0), backup, 100.0).state, "primary")

        held = fusion.resolve(_measurement("huawei", None), backup, 101.0)
        self.assertEqual((held.state, held.power_w, held.primary_invalid_samples), ("primary-held", -600.0, 1))

        failed_over = fusion.resolve(_measurement("huawei", None), backup, 102.0)
        self.assertEqual(
            (failed_over.state, failed_over.selected_source_id, failed_over.primary_invalid_samples),
            ("backup", "victron", 0),
        )

        recovering = fusion.resolve(_measurement("huawei", -550.0, 103.0), backup, 103.0)
        self.assertEqual((recovering.state, recovering.power_w, recovering.primary_recovery_samples), ("backup-recovery", -300.0, 1))

        recovered = fusion.resolve(_measurement("huawei", -500.0, 104.0), backup, 104.0)
        self.assertEqual(
            (recovered.state, recovered.selected_source_id, recovered.primary_invalid_samples, recovered.primary_recovery_samples),
            ("primary", "huawei", 0, 0),
        )

    def test_valid_primary_resets_a_single_invalid_sample(self) -> None:
        fusion = GridMeasurementFusion(_config(failover_samples=3))
        backup = _measurement("victron", 100.0)
        fusion.resolve(_measurement("huawei", 50.0), backup, 100.0)
        invalid = fusion.resolve(_measurement("huawei", None), backup, 101.0)
        valid = fusion.resolve(_measurement("huawei", 55.0, 102.0), backup, 102.0)
        self.assertEqual((invalid.primary_invalid_samples, valid.primary_invalid_samples, valid.power_w), (1, 0, 55.0))

    def test_backup_loss_uses_valid_primary_during_recovery(self) -> None:
        fusion = GridMeasurementFusion(_config(recovery_samples=3))
        fusion.resolve(_measurement("huawei", None), _measurement("victron", 100.0), 100.0)
        emergency = fusion.resolve(_measurement("huawei", 50.0, 101.0), _measurement("victron", None), 101.0)
        self.assertEqual((emergency.state, emergency.power_w), ("primary-emergency", 50.0))
        unavailable = fusion.resolve(_measurement("huawei", None), _measurement("victron", None), 102.0)
        self.assertEqual(unavailable.state, "unavailable")

    def test_expired_hold_without_backup_becomes_unavailable(self) -> None:
        fusion = GridMeasurementFusion(_config(failover_samples=3, failover_hold_seconds=1.0))
        fusion.resolve(_measurement("huawei", 40.0, 90.0), _measurement("victron", None), 90.0)
        result = fusion.resolve(_measurement("huawei", None), _measurement("victron", None), 97.0)
        self.assertEqual(result.state, "unavailable")

    def test_primary_hold_includes_its_exact_time_boundary(self) -> None:
        fusion = GridMeasurementFusion(_config(failover_samples=3, primary_max_age_seconds=5.0, failover_hold_seconds=2.0))
        fusion.resolve(_measurement("huawei", 40.0, 100.0), _measurement("victron", None), 100.0)
        boundary = fusion.resolve(_measurement("huawei", None), _measurement("victron", None), 107.0)
        self.assertEqual((boundary.state, boundary.power_w), ("primary-held", 40.0))

    def test_persistent_disagreement_uses_conservative_grid_value(self) -> None:
        fusion = GridMeasurementFusion(_config(mismatch_absolute_watts=100.0, mismatch_relative=0.0))
        primary = _measurement("huawei", -1000.0)
        backup = _measurement("victron", -200.0)

        first = fusion.resolve(primary, backup, 100.0)
        self.assertEqual((first.state, first.mismatch_samples), ("primary", 1))
        limited = fusion.resolve(primary, backup, 100.0)
        self.assertEqual((limited.state, limited.power_w, limited.selected_source_id), ("disagreement", -200.0, "conservative"))
        self.assertEqual(limited.confidence, 0.5)
        self.assertEqual((limited.difference_watts, limited.tolerance_watts), (800.0, 100.0))

        reset = fusion.resolve(_measurement("huawei", -220.0), backup, 100.0)
        self.assertEqual((reset.state, reset.mismatch_samples), ("primary", 0))

    def test_relative_plausibility_threshold_and_missing_peer(self) -> None:
        fusion = GridMeasurementFusion(_config(mismatch_absolute_watts=10.0, mismatch_relative=0.2))
        result = fusion.resolve(_measurement("huawei", 1000.0), _measurement("victron", 850.0), 100.0)
        self.assertEqual((result.difference_watts, result.tolerance_watts, result.mismatch_samples), (150.0, 200.0, 0))
        missing = fusion.resolve(_measurement("huawei", 1000.0), _measurement("victron", None), 100.0)
        self.assertIsNone(missing.difference_watts)
        self.assertIsNone(missing.tolerance_watts)
        self.assertEqual(missing.mismatch_samples, 0)

    def test_exact_plausibility_tolerance_is_not_a_mismatch(self) -> None:
        fusion = GridMeasurementFusion(_config(mismatch_absolute_watts=100.0, mismatch_relative=0.0))
        result = fusion.resolve(_measurement("huawei", 500.0), _measurement("victron", 400.0), 100.0)
        self.assertEqual((result.difference_watts, result.tolerance_watts, result.mismatch_samples), (100.0, 100.0, 0))

    def test_conservative_result_uses_older_capture_and_lower_confidence(self) -> None:
        fusion = GridMeasurementFusion(
            _config(
                minimum_confidence=0.1,
                mismatch_samples=1,
                mismatch_absolute_watts=10.0,
                mismatch_relative=0.0,
            )
        )
        result = fusion.resolve(
            _measurement("huawei", -900.0, 99.0, confidence=0.2),
            _measurement("victron", -100.0, 98.0, confidence=0.3),
            100.0,
        )
        self.assertEqual(result.power_w, -100.0)
        self.assertEqual(result.captured_at, 98.0)
        self.assertEqual(result.selected_source_id, "conservative")
        self.assertEqual(result.state, "disagreement")
        self.assertEqual(result.confidence, 0.2)
        self.assertEqual((result.primary_age_seconds, result.backup_age_seconds), (1.0, 2.0))

        swapped = GridMeasurementFusion(fusion.config).resolve(
            _measurement("huawei", -900.0, 99.0, confidence=0.3),
            _measurement("victron", -100.0, 98.0, confidence=0.2),
            100.0,
        )
        self.assertEqual(swapped.confidence, 0.2)

    def test_invalid_primary_resets_recovery_progress_while_backup_remains_active(self) -> None:
        fusion = GridMeasurementFusion(_config(recovery_samples=3))
        fusion.resolve(_measurement("huawei", None), _measurement("victron", 100.0), 100.0)
        first = fusion.resolve(_measurement("huawei", 50.0, 101.0), _measurement("victron", 100.0), 101.0)
        reset = fusion.resolve(_measurement("huawei", None), _measurement("victron", 100.0), 102.0)
        self.assertEqual((first.primary_recovery_samples, reset.primary_recovery_samples, reset.state), (1, 0, "backup"))

    def test_internal_fusion_invariants_reject_incomplete_valid_measurements(self) -> None:
        fusion = GridMeasurementFusion(_config())
        with self.assertRaises(AssertionError):
            fusion._plausibility(_measurement("huawei", None), _measurement("victron", 1.0), True, True)
        with self.assertRaises(AssertionError):
            fusion._plausibility(_measurement("huawei", 1.0), _measurement("victron", None), True, True)
        with self.assertRaises(AssertionError):
            fusion._conservative_measurement(_measurement("huawei", None), _measurement("victron", 1.0))
        with self.assertRaises(AssertionError):
            fusion._conservative_measurement(_measurement("huawei", 1.0), _measurement("victron", None))
        with self.assertRaises(AssertionError):
            fusion._conservative_measurement(_measurement("huawei", 1.0, None), _measurement("victron", 2.0))
        with self.assertRaises(AssertionError):
            fusion._conservative_measurement(_measurement("huawei", 1.0), _measurement("victron", 2.0, None))


class GridFusionSnapshotAdapterTests(unittest.TestCase):
    def test_snapshot_adapter_selects_named_primary_and_exposes_diagnostics(self) -> None:
        fusion = GridMeasurementFusion(_config())
        snapshot: dict[str, object] = {
            "grid_gateway_power": -300.0,
            "grid_gateway_captured_at": 100.0,
            "grid_gateway_observed_monotonic": 100.0,
            "battery_sources": [
                {"source_id": "other", "grid_interaction_w": 900.0, "captured_at": 100.0, "online": True},
                {
                    "source_id": "huawei",
                    "grid_interaction_w": -500.0,
                    "captured_at": 100.0,
                    "observed_monotonic": 99.0,
                    "online": True,
                    "confidence": 0.8,
                },
            ],
        }
        apply_grid_fusion(fusion, snapshot, 100.0)
        expected = {
            "grid_fusion_enabled": True,
            "grid_fusion_primary_source_id": "huawei",
            "grid_fusion_backup_source_id": "victron",
            "grid_primary_power": -500.0,
            "grid_primary_captured_at": 100.0,
            "grid_power": -500.0,
            "grid_captured_at": 100.0,
            "grid_observed_monotonic": 99.0,
            "grid_status": "primary",
            "grid_selected_source_id": "huawei",
            "grid_fusion_state": "primary",
            "grid_fusion_confidence": 0.8,
            "grid_fusion_primary_valid": True,
            "grid_fusion_backup_valid": True,
            "grid_fusion_primary_age_seconds": 1.0,
            "grid_fusion_backup_age_seconds": 0.0,
            "grid_fusion_difference_watts": 200.0,
            "grid_fusion_tolerance_watts": 300.0,
            "grid_fusion_primary_invalid_samples": 0,
            "grid_fusion_primary_recovery_samples": 0,
            "grid_fusion_mismatch_samples": 0,
        }
        self.assertEqual({key: snapshot[key] for key in expected}, expected)

    def test_snapshot_adapter_handles_missing_sources_and_invalid_shapes(self) -> None:
        fusion = GridMeasurementFusion(_config())
        snapshot: dict[str, object] = {
            "grid_gateway_power": True,
            "grid_gateway_captured_at": "bad",
            "battery_sources": {"not": "a-list"},
        }
        apply_grid_fusion(fusion, snapshot, 100.0)
        self.assertEqual(snapshot["grid_power"], None)
        self.assertEqual(snapshot["grid_captured_at"], None)
        self.assertEqual(snapshot["grid_status"], "unavailable")
        self.assertEqual(snapshot["grid_selected_source_id"], "")
        self.assertEqual(snapshot["grid_fusion_confidence"], 0.0)
        self.assertEqual(snapshot["grid_fusion_primary_valid"], False)
        self.assertEqual(snapshot["grid_fusion_backup_valid"], False)
        self.assertEqual(snapshot["grid_primary_power"], None)
        self.assertEqual(snapshot["grid_primary_captured_at"], None)

        snapshot = {
            "grid_gateway_power": 20.0,
            "grid_gateway_captured_at": 100.0,
            "grid_gateway_observed_monotonic": 100.0,
            "battery_sources": [None, {"source_id": "huawei", "grid_interaction_w": False, "online": True}],
        }
        apply_grid_fusion(fusion, snapshot, 100.0)
        self.assertEqual(snapshot["grid_power"], 20.0)

    def test_snapshot_adapter_normalizes_source_identity_and_confidence(self) -> None:
        fusion = GridMeasurementFusion(_config(minimum_confidence=0.0))
        high: dict[str, object] = {
            "grid_gateway_power": None,
            "grid_gateway_captured_at": None,
            "battery_sources": [
                {
                    "source_id": " huawei ",
                    "grid_interaction_w": 75,
                    "captured_at": 100,
                    "observed_monotonic": 100,
                    "online": True,
                    "confidence": 4.0,
                }
            ],
        }
        apply_grid_fusion(fusion, high, 100.0)
        self.assertEqual(high["grid_primary_power"], 75.0)
        self.assertEqual(high["grid_fusion_confidence"], 1.0)
        self.assertEqual(high["grid_fusion_primary_valid"], True)

        low: dict[str, object] = {
            "grid_gateway_power": None,
            "grid_gateway_captured_at": None,
            "battery_sources": [
                {
                    "source_id": "huawei",
                    "grid_interaction_w": 75.0,
                    "captured_at": 100.0,
                    "observed_monotonic": 100.0,
                    "online": True,
                    "confidence": -2.0,
                }
            ],
        }
        apply_grid_fusion(GridMeasurementFusion(_config(minimum_confidence=0.0)), low, 100.0)
        self.assertEqual(low["grid_fusion_confidence"], 0.0)

    def test_snapshot_adapter_requires_online_primary_and_backup_timestamp(self) -> None:
        primary_offline: dict[str, object] = {
            "grid_gateway_power": 30.0,
            "grid_gateway_captured_at": 100.0,
            "grid_gateway_observed_monotonic": 100.0,
            "battery_sources": [
                {
                    "source_id": "huawei",
                    "grid_interaction_w": 80.0,
                    "captured_at": 100.0,
                    "confidence": "unknown",
                }
            ],
        }
        apply_grid_fusion(GridMeasurementFusion(_config()), primary_offline, 100.0)
        self.assertEqual(primary_offline["grid_fusion_primary_valid"], False)
        self.assertEqual(primary_offline["grid_power"], 30.0)

        backup_without_time: dict[str, object] = {
            "grid_gateway_power": 30.0,
            "grid_gateway_captured_at": None,
            "battery_sources": [],
        }
        apply_grid_fusion(GridMeasurementFusion(_config()), backup_without_time, 100.0)
        self.assertEqual(backup_without_time["grid_fusion_backup_valid"], False)
        self.assertEqual(backup_without_time["grid_power"], None)

    def test_disabled_snapshot_adapter_preserves_legacy_status_contract(self) -> None:
        fusion = GridMeasurementFusion(GridFusionConfig())
        snapshot: dict[str, object] = {
            "grid_gateway_power": 25.0,
            "grid_gateway_captured_at": 100.0,
            "grid_gateway_observed_monotonic": 100.0,
            "battery_sources": [],
        }
        apply_grid_fusion(fusion, snapshot, 100.0)
        self.assertEqual((snapshot["grid_power"], snapshot["grid_status"]), (25.0, "ok"))

        missing: dict[str, object] = {
            "grid_gateway_power": None,
            "grid_gateway_captured_at": None,
            "battery_sources": [],
        }
        apply_grid_fusion(GridMeasurementFusion(GridFusionConfig()), missing, 100.0)
        self.assertEqual((missing["grid_power"], missing["grid_status"]), (None, "missing"))

    def test_adapter_parser_contracts_preserve_missing_and_gateway_metadata(self) -> None:
        fusion = GridMeasurementFusion(_config())
        self.assertEqual(
            snapshot_adapter._primary_measurement(fusion, {"battery_sources": []}),
            GridMeasurement("huawei", TimestampedMeasurement.unavailable(), False, 0.0),
        )
        self.assertEqual(
            snapshot_adapter._primary_measurement(
                fusion,
                {
                    "battery_sources": [
                        {
                            "source_id": "huawei",
                            "grid_interaction_w": 12,
                            "captured_at": 99,
                            "observed_monotonic": 199,
                            "online": 1,
                            "confidence": None,
                        }
                    ]
                },
            ),
            GridMeasurement(
                "huawei",
                TimestampedMeasurement.observed(
                    12.0,
                    captured_at=99.0,
                    observed_monotonic=199.0,
                ),
                False,
                0.0,
            ),
        )
        self.assertEqual(
            snapshot_adapter._backup_measurement(
                fusion,
                {
                    "grid_gateway_power": 23,
                    "grid_gateway_captured_at": 98,
                    "grid_gateway_observed_monotonic": 198,
                },
            ),
            GridMeasurement(
                "victron",
                TimestampedMeasurement.observed(
                    23.0,
                    captured_at=98.0,
                    observed_monotonic=198.0,
                ),
                True,
                1.0,
            ),
        )
        self.assertEqual(
            snapshot_adapter._backup_measurement(fusion, {}),
            GridMeasurement("victron", TimestampedMeasurement.unavailable(), False, 0.0),
        )

    def test_adapter_source_identity_and_confidence_contracts(self) -> None:
        absent_ids: dict[str, object] = {"battery_sources": [{}, {"source_id": 7}, None]}
        self.assertIsNone(snapshot_adapter._source_payload(absent_ids, "huawei"))
        payload = {"source_id": " huawei ", "online": True}
        self.assertIs(snapshot_adapter._source_payload({"battery_sources": [payload]}, "huawei"), payload)
        self.assertIsNone(snapshot_adapter._source_payload({"battery_sources": {}}, "huawei"))
        self.assertEqual(snapshot_adapter._confidence(None), 0.0)
        self.assertEqual(snapshot_adapter._confidence(False), 0.0)
        self.assertEqual(snapshot_adapter._confidence(0.25), 0.25)


if __name__ == "__main__":
    unittest.main()
