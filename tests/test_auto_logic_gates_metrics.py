from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast

from venus_evcharger.auto.logic_gates_metrics import _AutoDecisionMetrics


def battery_activity(**overrides: float | int | str | None) -> dict[str, float | int | str | None]:
    activity: dict[str, float | int | str | None] = {
        "mode": "assist",
        "discharge_balance_policy_enabled": 1,
        "discharge_balance_warning_active": 0,
        "discharge_balance_warning_error_w": 21.0,
        "discharge_balance_warn_threshold_w": 22.0,
        "discharge_balance_bias_mode": "dynamic",
        "discharge_balance_bias_gate_active": 1,
        "discharge_balance_bias_start_error_w": 23.0,
        "discharge_balance_bias_penalty_w": 24.0,
        "discharge_balance_coordination_policy_enabled": 1,
        "discharge_balance_coordination_support_mode": "coordinated",
        "discharge_balance_coordination_feasibility": "possible",
        "discharge_balance_coordination_gate_active": 0,
        "discharge_balance_coordination_start_error_w": 25.0,
        "discharge_balance_coordination_penalty_w": 26.0,
        "discharge_balance_coordination_advisory_active": 1,
        "discharge_balance_coordination_advisory_reason": "reserve",
        "charge_power_w": 27.0,
        "discharge_power_w": 28.0,
        "charge_activity_ratio": 0.29,
        "discharge_activity_ratio": 0.31,
        "learning_profile_count": 3,
        "observed_max_charge_power_w": 32.0,
        "observed_max_discharge_power_w": 33.0,
        "typical_response_delay_seconds": 34.0,
        "support_bias": 35.0,
        "day_support_bias": 36.0,
        "night_support_bias": 37.0,
        "import_support_bias": 38.0,
        "export_bias": 39.0,
        "battery_first_export_bias": 40.0,
        "power_smoothing_ratio": 0.41,
        "reserve_band_floor_soc": 42.0,
        "reserve_band_ceiling_soc": 43.0,
        "reserve_band_width_soc": 44.0,
        "battery_headroom_charge_w": 45.0,
        "battery_headroom_discharge_w": 46.0,
        "expected_near_term_export_w": 47.0,
        "expected_near_term_import_w": 48.0,
        "surplus_penalty_w": 50.0,
    }
    activity.update(overrides)
    return activity


class MetricsHarness(_AutoDecisionMetrics):
    def __init__(self) -> None:
        self.service = SimpleNamespace(
            _last_auto_metrics={},
            _stop_smoothed_grid_power=None,
            _stop_smoothed_surplus_power=None,
        )
        self.average_values: dict[int, float | None] = {1: 1200.0, 2: -300.0}
        self.battery_activity = battery_activity()
        self.calls: list[tuple[Any, ...]] = []
        self.learned_charge_power: float | None = 1900.0
        self.learned_scale = 1.25
        self.learned_state = "stable"
        self.near_term_adjustment = 75.0
        self.smoothing_factor = 0.5
        self.thresholds = (1500.0, 900.0, "normal")
        self.volatility: float | None = 17.0

    def available_surplus_watts(self, pv_power: float, grid_power: float) -> float:
        self.calls.append(("available_surplus", pv_power, grid_power))
        return pv_power - grid_power

    def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> None:
        self.calls.append(("add_sample", now, surplus_power, grid_power))

    def average_auto_metric(self, index: int) -> float | None:
        self.calls.append(("average", index))
        return self.average_values[index]

    def _surplus_thresholds_for_soc(self, battery_soc: float) -> tuple[float, float, str]:
        self.calls.append(("thresholds", battery_soc))
        return self.thresholds

    def _adaptive_stop_alpha(self) -> tuple[float, str, float | None]:
        self.calls.append(("adaptive_alpha",))
        return self.smoothing_factor, "stable", self.volatility

    def _combined_battery_activity_context(self) -> dict[str, float | int | str | None]:
        self.calls.append(("battery_activity",))
        return dict(self.battery_activity)

    def _near_term_grid_adjustment(self, activity: dict[str, float | int | str | None]) -> float:
        self.calls.append(("near_term_adjustment", activity["mode"], activity["surplus_penalty_w"]))
        return self.near_term_adjustment

    def _active_learned_charge_power(self, now: float) -> float | None:
        self.calls.append(("active_learned_power", now))
        return self.learned_charge_power

    def _current_learned_charge_power_state(self, now: float) -> str:
        self.calls.append(("learned_state", now))
        return self.learned_state

    def _learned_charge_power_scale(self, now: float) -> float:
        self.calls.append(("learned_scale", now))
        return self.learned_scale

    def _required_float(self, value: object) -> float:
        self.calls.append(("required_float", value))
        return float(cast(Any, value))

    def _non_negative_optional_float(self, value: object) -> float | None:
        self.calls.append(("non_negative", value))
        try:
            numeric = float(cast(Any, value))
        except (TypeError, ValueError):
            return None
        return numeric if numeric >= 0.0 else None

    def _smooth_metric(self, previous: float | None, current: float, alpha: float) -> float:
        self.calls.append(("smooth", previous, current, alpha))
        return current if previous is None else (previous * (1.0 - alpha)) + (current * alpha)


class TestAutoLogicGatesMetrics(unittest.TestCase):
    def test_update_average_metrics_waits_until_both_averages_are_available(self) -> None:
        harness = MetricsHarness()
        harness.average_values = {1: None, 2: -300.0}

        self.assertEqual(harness._update_average_metrics(10.0, 2500.0, -400.0, 55.0, False), (None, None))
        self.assertEqual(harness.service._last_auto_metrics, {})
        self.assertEqual(
            harness.calls,
            [
                ("available_surplus", 2500.0, -400.0),
                ("add_sample", 10.0, 2900.0, -400.0),
                ("average", 1),
                ("average", 2),
            ],
        )

        harness = MetricsHarness()
        harness.average_values = {1: 1200.0, 2: None}

        self.assertEqual(harness._update_average_metrics(11.0, 2500.0, -400.0, 55.0, False), (None, None))
        self.assertEqual(harness.service._last_auto_metrics, {})

    def test_update_average_metrics_builds_snapshot_and_returns_battery_adjusted_surplus(self) -> None:
        harness = MetricsHarness()

        self.assertEqual(harness._update_average_metrics(20.0, 2600.0, -500.0, 56.0, False), (1225.0, -300.0))

        metrics = harness.service._last_auto_metrics
        self.assertEqual(metrics["surplus"], 1225.0)
        self.assertEqual(metrics["grid"], -300.0)
        self.assertEqual(metrics["raw_surplus"], 1200.0)
        self.assertEqual(metrics["raw_grid"], -300.0)
        self.assertEqual(metrics["decision_surplus_before_battery_penalty"], 1200.0)
        self.assertEqual(metrics["soc"], 56.0)
        self.assertEqual(metrics["profile"], "normal")
        self.assertEqual(metrics["start_threshold"], 1500.0)
        self.assertEqual(metrics["stop_threshold"], 900.0)
        self.assertEqual(metrics["learned_charge_power"], 1900.0)
        self.assertEqual(metrics["learned_charge_power_state"], "stable")
        self.assertEqual(metrics["threshold_scale"], 1.25)
        self.assertEqual(metrics["threshold_mode"], "adaptive")
        self.assertEqual(metrics["stop_alpha"], 0.5)
        self.assertEqual(metrics["stop_alpha_stage"], "stable")
        self.assertEqual(metrics["surplus_volatility"], 17.0)
        self.assertEqual(metrics["battery_surplus_penalty_w"], 50.0)
        self.assertEqual(metrics["battery_near_term_adjustment_w"], 75.0)
        self.assertEqual(metrics["battery_learning_profile_count"], 3)
        self.assertIn(("thresholds", 56.0), harness.calls)
        self.assertIn(("battery_activity",), harness.calls)
        self.assertIn(("active_learned_power", 20.0), harness.calls)

    def test_surplus_threshold_smoothing_and_learned_metrics_are_explicit(self) -> None:
        harness = MetricsHarness()
        harness.thresholds = (1600, 800, "high-soc")
        self.assertEqual(
            harness._surplus_threshold_metrics(91.0),
            {"start_threshold": 1600.0, "stop_threshold": 800.0, "profile": "high-soc"},
        )

        harness = MetricsHarness()
        harness.volatility = None
        smoothing = harness._smoothing_metrics(True, 1000.0, -200.0)
        self.assertEqual(
            smoothing,
            {
                "surplus": 1000.0,
                "grid": -200.0,
                "stop_alpha": 0.5,
                "stop_alpha_stage": "stable",
                "surplus_volatility": None,
            },
        )

        harness = MetricsHarness()
        self.assertEqual(
            harness._learned_threshold_metrics(33.0),
            {
                "learned_charge_power": 1900.0,
                "learned_charge_power_state": "stable",
                "threshold_scale": 1.25,
                "threshold_mode": "adaptive",
            },
        )
        self.assertEqual(
            harness.calls,
            [
                ("active_learned_power", 33.0),
                ("learned_state", 33.0),
                ("learned_scale", 33.0),
            ],
        )

        harness = MetricsHarness()
        harness.learned_charge_power = None
        harness.learned_state = "unknown"
        self.assertEqual(
            harness._learned_threshold_metrics(34.0),
            {
                "learned_charge_power": None,
                "learned_charge_power_state": "unknown",
                "threshold_scale": 1.25,
                "threshold_mode": "static",
            },
        )
        self.assertEqual(
            harness.calls,
            [
                ("active_learned_power", 34.0),
                ("learned_state", 34.0),
                ("learned_scale", 34.0),
            ],
        )

    def test_battery_adjusted_surplus_metrics_normalizes_penalty_and_learning_profile_count(self) -> None:
        harness = MetricsHarness()
        harness.battery_activity = battery_activity(surplus_penalty_w=80.0, learning_profile_count=4)
        harness.near_term_adjustment = 25.0

        metrics = harness._battery_adjusted_surplus_metrics(1000.0)

        self.assertEqual(metrics["decision_surplus"], 945.0)
        self.assertEqual(metrics["raw_decision_surplus"], 1000.0)
        self.assertEqual(metrics["surplus_penalty_w"], 80.0)
        self.assertEqual(metrics["near_term_adjustment_w"], 25.0)
        self.assertEqual(metrics["learning_profile_count"], 4)
        self.assertEqual(metrics["mode"], "assist")

        harness = MetricsHarness()
        harness.battery_activity = battery_activity(surplus_penalty_w=-1.0, learning_profile_count="4")
        harness.near_term_adjustment = -15.0

        metrics = harness._battery_adjusted_surplus_metrics(1000.0)

        self.assertEqual(metrics["decision_surplus"], 985.0)
        self.assertEqual(metrics["surplus_penalty_w"], 0.0)
        self.assertEqual(metrics["near_term_adjustment_w"], -15.0)
        self.assertEqual(metrics["learning_profile_count"], 0)

    def test_auto_metrics_snapshot_maps_all_public_metric_keys(self) -> None:
        harness = MetricsHarness()
        snapshot = harness._auto_metrics_snapshot(
            avg_surplus_power=1200.0,
            avg_grid_power=-300.0,
            battery_soc=56.0,
            threshold_metrics={"start_threshold": 1500.0, "stop_threshold": 900.0, "profile": "normal"},
            smoothing_metrics={
                "surplus": 1210.0,
                "grid": -310.0,
                "stop_alpha": 0.5,
                "stop_alpha_stage": "stable",
                "surplus_volatility": 17.0,
            },
            battery_metrics={
                "decision_surplus": 1235.0,
                "raw_decision_surplus": 1210.0,
                "surplus_penalty_w": 50.0,
                "near_term_adjustment_w": 75.0,
                **battery_activity(learning_profile_count=4),
            },
            learned_metrics={
                "learned_charge_power": 1900.0,
                "learned_charge_power_state": "stable",
                "threshold_scale": 1.25,
                "threshold_mode": "adaptive",
            },
        )

        expected = {
            "surplus": 1235.0,
            "grid": -310.0,
            "raw_surplus": 1200.0,
            "decision_surplus_before_battery_penalty": 1210.0,
            "raw_grid": -300.0,
            "soc": 56.0,
            "profile": "normal",
            "start_threshold": 1500.0,
            "stop_threshold": 900.0,
            "learned_charge_power": 1900.0,
            "learned_charge_power_state": "stable",
            "threshold_scale": 1.25,
            "threshold_mode": "adaptive",
            "stop_alpha": 0.5,
            "stop_alpha_stage": "stable",
            "surplus_volatility": 17.0,
            "battery_surplus_penalty_w": 50.0,
            "battery_near_term_adjustment_w": 75.0,
            "battery_support_mode": "assist",
            "battery_discharge_balance_policy_enabled": 1,
            "battery_discharge_balance_warning_active": 0,
            "battery_discharge_balance_warning_error_w": 21.0,
            "battery_discharge_balance_warn_threshold_w": 22.0,
            "battery_discharge_balance_bias_mode": "dynamic",
            "battery_discharge_balance_bias_gate_active": 1,
            "battery_discharge_balance_bias_start_error_w": 23.0,
            "battery_discharge_balance_bias_penalty_w": 24.0,
            "battery_discharge_balance_coordination_policy_enabled": 1,
            "battery_discharge_balance_coordination_support_mode": "coordinated",
            "battery_discharge_balance_coordination_feasibility": "possible",
            "battery_discharge_balance_coordination_gate_active": 0,
            "battery_discharge_balance_coordination_start_error_w": 25.0,
            "battery_discharge_balance_coordination_penalty_w": 26.0,
            "battery_discharge_balance_coordination_advisory_active": 1,
            "battery_discharge_balance_coordination_advisory_reason": "reserve",
            "battery_charge_power_w": 27.0,
            "battery_discharge_power_w": 28.0,
            "battery_charge_activity_ratio": 0.29,
            "battery_discharge_activity_ratio": 0.31,
            "battery_learning_profile_count": 4,
            "battery_observed_max_charge_power_w": 32.0,
            "battery_observed_max_discharge_power_w": 33.0,
            "battery_typical_response_delay_seconds": 34.0,
            "battery_support_bias": 35.0,
            "battery_day_support_bias": 36.0,
            "battery_night_support_bias": 37.0,
            "battery_import_support_bias": 38.0,
            "battery_export_bias": 39.0,
            "battery_battery_first_export_bias": 40.0,
            "battery_power_smoothing_ratio": 0.41,
            "battery_reserve_band_floor_soc": 42.0,
            "battery_reserve_band_ceiling_soc": 43.0,
            "battery_reserve_band_width_soc": 44.0,
            "battery_headroom_charge_w": 45.0,
            "battery_headroom_discharge_w": 46.0,
            "expected_near_term_export_w": 47.0,
            "expected_near_term_import_w": 48.0,
        }
        self.assertEqual(snapshot, expected)

    def test_smoothed_decision_metrics_resets_when_relay_off_and_uses_ewma_when_running(self) -> None:
        harness = MetricsHarness()
        harness.service._stop_smoothed_surplus_power = 100.0
        harness.service._stop_smoothed_grid_power = -100.0

        self.assertEqual(harness._smoothed_decision_metrics(False, 1200.0, -300.0, 0.25), (1200.0, -300.0))
        self.assertIsNone(harness.service._stop_smoothed_surplus_power)
        self.assertIsNone(harness.service._stop_smoothed_grid_power)
        self.assertEqual(harness.calls, [])

        harness = MetricsHarness()
        harness.service._stop_smoothed_surplus_power = 1000.0
        harness.service._stop_smoothed_grid_power = -100.0

        self.assertEqual(harness._smoothed_decision_metrics(True, 1200.0, -300.0, 0.25), (1050.0, -150.0))
        self.assertEqual(harness.service._stop_smoothed_surplus_power, 1050.0)
        self.assertEqual(harness.service._stop_smoothed_grid_power, -150.0)
        self.assertEqual(
            harness.calls,
            [
                ("smooth", 1000.0, 1200.0, 0.25),
                ("smooth", -100.0, -300.0, 0.25),
            ],
        )

        harness = MetricsHarness()
        delattr(harness.service, "_stop_smoothed_surplus_power")
        delattr(harness.service, "_stop_smoothed_grid_power")

        self.assertEqual(harness._smoothed_decision_metrics(True, 1300.0, -350.0, 0.4), (1300.0, -350.0))
        self.assertEqual(harness.service._stop_smoothed_surplus_power, 1300.0)
        self.assertEqual(harness.service._stop_smoothed_grid_power, -350.0)
        self.assertEqual(
            harness.calls,
            [
                ("smooth", None, 1300.0, 0.4),
                ("smooth", None, -350.0, 0.4),
            ],
        )


if __name__ == "__main__":
    unittest.main()
