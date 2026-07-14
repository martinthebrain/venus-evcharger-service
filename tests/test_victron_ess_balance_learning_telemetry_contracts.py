import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import venus_evcharger.update.victron_ess_balance_learning_telemetry as telemetry_module
from venus_evcharger.update.victron_ess_balance_learning_telemetry import (
    _UpdateCycleVictronEssBalanceLearningTelemetry,
    _victron_ess_balance_clamped_stability_score,
    _victron_ess_balance_delay_penalty,
    _victron_ess_balance_gain_bonus,
    _victron_ess_balance_overshoot_penalty,
    _victron_ess_balance_settle_ratio,
)


class _TelemetryContractHarness(_UpdateCycleVictronEssBalanceLearningTelemetry):
    def __init__(self) -> None:
        self.profile_delays: list[tuple[object, str, float]] = []
        self.profile_gains: list[tuple[object, str, float]] = []
        self.profile_counters: list[tuple[object, str, str]] = []
        self.cooldowns: list[tuple[object, float, str]] = []
        self.integral_resets: list[tuple[object, bool]] = []

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _victron_ess_balance_profile_sample_count(profile: dict[str, Any]) -> int:
        return int(profile.get("sample_count", 0) or 0)

    def _victron_ess_balance_update_profile_delay(self, svc: Any, profile_key: str, value: float) -> None:
        self.profile_delays.append((svc, profile_key, value))

    def _victron_ess_balance_update_profile_gain(self, svc: Any, profile_key: str, value: float) -> None:
        self.profile_gains.append((svc, profile_key, value))

    def _victron_ess_balance_increment_profile_counter(
        self, svc: Any, profile_key: str, counter_name: str
    ) -> None:
        self.profile_counters.append((svc, profile_key, counter_name))

    def _enter_victron_ess_balance_overshoot_cooldown(self, svc: Any, now: float, reason: str) -> None:
        self.cooldowns.append((svc, now, reason))

    def _reset_victron_ess_balance_pid_integral(self, svc: Any, aggressive: bool = False) -> None:
        self.integral_resets.append((svc, aggressive))


def _command_service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "_victron_ess_balance_telemetry_last_command_at": 11.5,
        "_victron_ess_balance_telemetry_last_command_error_w": -22.5,
        "_victron_ess_balance_telemetry_last_command_setpoint_w": 33.5,
        "_victron_ess_balance_telemetry_last_command_profile_key": " profile-a ",
        "_victron_ess_balance_telemetry_command_response_recorded": True,
        "_victron_ess_balance_telemetry_command_overshoot_recorded": True,
        "_victron_ess_balance_telemetry_command_settled_recorded": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelemetryPureScoreContracts(unittest.TestCase):
    def test_outcome_ratios_and_penalties_have_exact_boundaries(self) -> None:
        self.assertEqual(_victron_ess_balance_settle_ratio(4, -1), 1.0)
        self.assertEqual(_victron_ess_balance_settle_ratio(1, 4), 0.25)
        self.assertEqual(_victron_ess_balance_settle_ratio(0, 1), 0.0)
        self.assertEqual(_victron_ess_balance_overshoot_penalty(3, -1), 0.0)
        self.assertEqual(_victron_ess_balance_overshoot_penalty(1, 4), 0.25)
        self.assertEqual(_victron_ess_balance_overshoot_penalty(1, 1), 0.6)
        self.assertEqual(_victron_ess_balance_overshoot_penalty(4, 4), 0.6)

    def test_gain_and_delay_adjustments_have_exact_boundaries(self) -> None:
        for value in (None, -1.0, 0.0):
            self.assertEqual(_victron_ess_balance_gain_bonus(value), 0.0)
        self.assertEqual(_victron_ess_balance_gain_bonus(0.01), 0.15)
        self.assertEqual(_victron_ess_balance_delay_penalty(None), 0.0)
        self.assertEqual(_victron_ess_balance_delay_penalty(4.0), 0.0)
        self.assertEqual(_victron_ess_balance_delay_penalty(10.0), 0.1)
        self.assertEqual(_victron_ess_balance_delay_penalty(20.0), 0.2)
        self.assertEqual(_victron_ess_balance_delay_penalty(100.0), 0.2)

    def test_stability_clamp_preserves_each_term(self) -> None:
        self.assertAlmostEqual(_victron_ess_balance_clamped_stability_score(0.5, 0.1, 0.2, 0.05), 0.5)
        self.assertEqual(_victron_ess_balance_clamped_stability_score(2.0, 1.0, 0.0, 0.0), 1.0)
        self.assertEqual(_victron_ess_balance_clamped_stability_score(0.0, 0.0, 1.0, 1.0), 0.0)


class TelemetryStateContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _TelemetryContractHarness()

    def test_improvement_stats_preserve_all_inputs(self) -> None:
        self.assertEqual(
            self.harness._victron_ess_balance_improvement_stats(-3.0, -10.0, 80.0, 50.0),
            {
                "initial_abs_error_w": 10.0,
                "current_abs_error_w": 3.0,
                "improvement_w": 7.0,
                "setpoint_bias_w": 30.0,
            },
        )
        self.assertEqual(
            self.harness._victron_ess_balance_improvement_stats(12.0, 10.0, 40.0, 50.0)["improvement_w"],
            0.0,
        )

    def test_overshoot_requires_every_condition_and_includes_threshold(self) -> None:
        check = self.harness._victron_ess_balance_is_overshoot
        self.assertTrue(check(-5.0, 5.0, 3.0, 3.0))
        self.assertFalse(check(-5.0, 0.0, 3.0, 3.0))
        self.assertFalse(check(0.0, 5.0, 3.0, 3.0))
        self.assertFalse(check(5.0, 5.0, 3.0, 3.0))
        self.assertFalse(check(-5.0, 5.0, 2.99, 3.0))
        self.assertTrue(check(-5.0, 1.0, 3.0, 3.0))
        self.assertTrue(check(1.0, -5.0, 3.0, 3.0))

    def test_command_state_maps_each_runtime_field_and_fallback(self) -> None:
        self.assertEqual(
            self.harness._victron_ess_balance_telemetry_command_state(_command_service(), "fallback"),
            {
                "command_at": 11.5,
                "command_error_w": -22.5,
                "command_setpoint_w": 33.5,
                "command_profile_key": "profile-a",
                "command_response_recorded": True,
                "command_overshoot_recorded": True,
                "command_settled_recorded": True,
            },
        )
        self.assertEqual(
            self.harness._victron_ess_balance_telemetry_command_state(
                _command_service(
                    _victron_ess_balance_telemetry_last_command_at=None,
                    _victron_ess_balance_telemetry_last_command_error_w=None,
                    _victron_ess_balance_telemetry_last_command_setpoint_w=None,
                    _victron_ess_balance_telemetry_last_command_profile_key="",
                    _victron_ess_balance_telemetry_command_response_recorded=False,
                    _victron_ess_balance_telemetry_command_overshoot_recorded=False,
                    _victron_ess_balance_telemetry_command_settled_recorded=False,
                ),
                "fallback",
            ),
            {
                "command_at": None,
                "command_error_w": None,
                "command_setpoint_w": None,
                "command_profile_key": "fallback",
                "command_response_recorded": False,
                "command_overshoot_recorded": False,
                "command_settled_recorded": False,
            },
        )

    def test_thresholds_normalize_deadband_but_preserve_base_sign(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=-4.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=-12.5,
        )
        self.assertEqual(self.harness._victron_ess_balance_telemetry_thresholds(svc), (0.0, -12.5))
        defaults = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=0.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
        )
        self.assertEqual(self.harness._victron_ess_balance_telemetry_thresholds(defaults), (0.0, 50.0))

    def test_ewma_uses_first_sample_then_exact_quarter_weight(self) -> None:
        self.assertEqual(self.harness._ewma_learned_value(None, 8.0, 4), 8.0)
        self.assertEqual(self.harness._ewma_learned_value(2.0, 8.0, 0), 8.0)
        self.assertEqual(self.harness._ewma_learned_value(2.0, 10.0, 1), 4.0)

    def test_sample_updates_write_service_and_profile(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_delay_samples=2,
            _victron_ess_balance_telemetry_response_delay_seconds=4.0,
            _victron_ess_balance_telemetry_gain_samples=3,
            _victron_ess_balance_telemetry_estimated_gain=0.4,
        )
        self.harness._victron_ess_balance_update_response_delay(svc, "delay-profile", 8.0)
        self.harness._victron_ess_balance_update_gain(svc, "gain-profile", 0.8)
        self.assertEqual(svc._victron_ess_balance_telemetry_delay_samples, 3)
        self.assertEqual(svc._victron_ess_balance_telemetry_response_delay_seconds, 5.0)
        self.assertEqual(svc._victron_ess_balance_telemetry_gain_samples, 4)
        self.assertAlmostEqual(svc._victron_ess_balance_telemetry_estimated_gain, 0.5)
        self.assertEqual(self.harness.profile_delays, [(svc, "delay-profile", 8.0)])
        self.assertEqual(self.harness.profile_gains, [(svc, "gain-profile", 0.8)])

    def test_markers_update_exact_state_and_collaborators(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_overshoot_count=2,
            _victron_ess_balance_telemetry_settled_count=4,
        )
        self.harness._victron_ess_balance_mark_overshoot(svc, 12.5, "profile-o")
        self.harness._victron_ess_balance_mark_settled(svc, "profile-s")
        self.assertIs(svc._victron_ess_balance_telemetry_command_overshoot_recorded, True)
        self.assertEqual(svc._victron_ess_balance_telemetry_overshoot_count, 3)
        self.assertIs(svc._victron_ess_balance_telemetry_command_settled_recorded, True)
        self.assertEqual(svc._victron_ess_balance_telemetry_settled_count, 5)
        self.assertEqual(
            self.harness.profile_counters,
            [(svc, "profile-o", "overshoot_count"), (svc, "profile-s", "settled_count")],
        )
        self.assertEqual(self.harness.cooldowns, [(svc, 12.5, "overshoot_detected")])
        self.assertEqual(self.harness.integral_resets, [(svc, True)])


class TelemetryDecisionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _TelemetryContractHarness()

    def test_response_delay_boundary_and_nonnegative_elapsed_time(self) -> None:
        svc = SimpleNamespace()
        state = {"command_response_recorded": False}
        self.harness._victron_ess_balance_update_response_delay = MagicMock()
        self.harness._victron_ess_balance_maybe_record_response_delay(svc, 5.0, state, "p", 9.99, 10.0, 8.0)
        self.harness._victron_ess_balance_update_response_delay.assert_not_called()
        self.harness._victron_ess_balance_maybe_record_response_delay(svc, 5.0, state, "p", 10.0, 10.0, 8.0)
        self.harness._victron_ess_balance_update_response_delay.assert_called_once_with(svc, "p", 0.0)
        self.assertIs(state["command_response_recorded"], True)
        self.assertIs(svc._victron_ess_balance_telemetry_command_response_recorded, True)

    def test_gain_requires_positive_improvement_and_one_watt_bias(self) -> None:
        svc = SimpleNamespace()
        self.harness._victron_ess_balance_update_gain = MagicMock()
        self.harness._victron_ess_balance_maybe_record_gain(svc, "p", 0.0, 2.0)
        self.harness._victron_ess_balance_maybe_record_gain(svc, "p", 2.0, 0.99)
        self.harness._victron_ess_balance_update_gain.assert_not_called()
        self.harness._victron_ess_balance_maybe_record_gain(svc, "p", 0.5, 2.0)
        self.harness._victron_ess_balance_update_gain.assert_called_once_with(svc, "p", 0.25)
        self.harness._victron_ess_balance_update_gain.reset_mock()
        self.harness._victron_ess_balance_maybe_record_gain(svc, "p", 2.0, 1.0)
        self.harness._victron_ess_balance_update_gain.assert_called_once_with(svc, "p", 2.0)

    def test_marker_guards_and_boundaries(self) -> None:
        svc = SimpleNamespace()
        self.harness._victron_ess_balance_mark_overshoot = MagicMock()
        state = {"command_overshoot_recorded": False}
        self.harness._victron_ess_balance_maybe_mark_overshoot(svc, 7.0, -5.0, state, "p", 5.0, 3.0, 3.0)
        self.harness._victron_ess_balance_mark_overshoot.assert_called_once_with(svc, 7.0, "p")
        self.assertIs(state["command_overshoot_recorded"], True)
        self.harness._victron_ess_balance_mark_settled = MagicMock()
        settled = {"command_settled_recorded": False}
        self.harness._victron_ess_balance_maybe_mark_settled(svc, settled, "s", 4.0, 4.0)
        self.harness._victron_ess_balance_mark_settled.assert_called_once_with(svc, "s")
        self.assertIs(settled["command_settled_recorded"], True)


class TelemetryAggregateScoreContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _TelemetryContractHarness()

    def test_stability_values_and_service_mapping_are_exact(self) -> None:
        self.assertAlmostEqual(self.harness._victron_ess_balance_stability_score_values(3, 1, 0.5, 10.0), 0.55)
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=3,
            _victron_ess_balance_telemetry_overshoot_count=1,
            _victron_ess_balance_telemetry_estimated_gain=0.5,
            _victron_ess_balance_telemetry_response_delay_seconds=10.0,
        )
        self.assertAlmostEqual(self.harness._victron_ess_balance_stability_score(svc), 0.55)
        empty = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_estimated_gain=None,
            _victron_ess_balance_telemetry_response_delay_seconds=None,
        )
        self.assertAlmostEqual(self.harness._victron_ess_balance_stability_score(empty), 0.85)
        empty._victron_ess_balance_telemetry_overshoot_count = 1
        self.assertEqual(self.harness._victron_ess_balance_stability_score(empty), 0.0)

    def test_variance_scores_preserve_floor_mean_and_mad(self) -> None:
        ratio = self.harness._victron_ess_balance_variance_ratio
        self.assertEqual(ratio(None, 2.0, 1.0), 1.0)
        self.assertEqual(ratio(0.0, 2.0, 1.0), 1.0)
        self.assertEqual(ratio(4.0, None, 1.0), 1.0)
        self.assertEqual(ratio(4.0, 1.0, 1.0), 0.75)
        self.assertEqual(ratio(0.5, 1.0, 1.0), 0.0)
        self.assertAlmostEqual(self.harness._victron_ess_balance_variance_score(4.0, 1.0, 1.0, 0.2), 0.775)
        self.assertAlmostEqual(self.harness._victron_ess_balance_variance_score(0.5, 0.25, 1.0, 0.2), 0.775)

    def test_profile_scores_preserve_weights_counts_and_clamps(self) -> None:
        profile = {"sample_count": 2, "stability_score": 0.4, "response_variance_score": 0.6}
        self.assertAlmostEqual(self.harness._victron_ess_balance_regime_consistency_score(profile), 0.5)
        self.assertEqual(
            self.harness._victron_ess_balance_regime_consistency_score(
                {"sample_count": 10, "stability_score": 10.0, "response_variance_score": 10.0}
            ),
            1.0,
        )
        self.assertEqual(self.harness._victron_ess_balance_regime_consistency_score({}), 0.0)
        self.assertEqual(
            self.harness._victron_ess_balance_regime_consistency_score(
                {"sample_count": 5, "stability_score": 0.0, "response_variance_score": 0.0}
            ),
            0.3,
        )
        self.assertAlmostEqual(
            self.harness._victron_ess_balance_reproducibility_score(
                {"settled_count": 3, "overshoot_count": 1, "response_variance_score": 0.5}
            ),
            0.65,
        )
        self.assertEqual(
            self.harness._victron_ess_balance_reproducibility_score(
                {"settled_count": 0, "overshoot_count": 0}
            ),
            0.6,
        )
        self.assertEqual(
            self.harness._victron_ess_balance_reproducibility_score(
                {"settled_count": 1, "overshoot_count": 0, "response_variance_score": 10.0}
            ),
            1.0,
        )
        self.assertEqual(
            self.harness._victron_ess_balance_reproducibility_score(
                {"settled_count": 0, "overshoot_count": 1, "response_variance_score": 0.0}
            ),
            0.0,
        )


class TelemetryOrchestrationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _TelemetryContractHarness()

    def test_clean_episode_forwards_every_derived_value(self) -> None:
        svc = SimpleNamespace()
        state = {
            "command_at": 10.0,
            "command_error_w": 40.0,
            "command_setpoint_w": 80.0,
            "command_profile_key": "profile-x",
            "command_response_recorded": False,
            "command_overshoot_recorded": True,
            "command_settled_recorded": False,
        }
        self.harness._victron_ess_balance_maybe_record_response_delay = MagicMock()
        self.harness._victron_ess_balance_maybe_record_gain = MagicMock()
        self.harness._victron_ess_balance_maybe_mark_overshoot = MagicMock()
        self.harness._victron_ess_balance_maybe_mark_settled = MagicMock()
        result = self.harness._victron_ess_balance_process_clean_episode(
            svc, 20.0, 15.0, state, 12.0, 50.0, 5.0
        )
        self.assertEqual(result, (True, False))
        self.harness._victron_ess_balance_maybe_record_response_delay.assert_called_once_with(
            svc, 20.0, state, "profile-x", 25.0, 12.0, 10.0
        )
        self.harness._victron_ess_balance_maybe_record_gain.assert_called_once_with(
            svc, "profile-x", 25.0, 30.0
        )
        self.harness._victron_ess_balance_maybe_mark_overshoot.assert_called_once_with(
            svc, 20.0, 15.0, state, "profile-x", 40.0, 15.0, 12.0
        )
        self.harness._victron_ess_balance_maybe_mark_settled.assert_called_once_with(
            svc, state, "profile-x", 15.0, 5.0
        )

    def test_telemetry_update_maps_inputs_outputs_and_collaborators(self) -> None:
        svc = SimpleNamespace()
        state = {
            "command_at": 10.0,
            "command_error_w": 20.0,
            "command_setpoint_w": 30.0,
            "command_profile_key": "profile-x",
        }
        cluster = {
            "battery_combined_grid_interaction_w": -12.5,
            "battery_combined_ac_power_w": 34.5,
        }
        metrics: dict[str, Any] = {}
        self.harness._victron_ess_balance_telemetry_command_state = MagicMock(return_value=state)
        self.harness._victron_ess_balance_telemetry_thresholds = MagicMock(return_value=(30.0, 55.0))
        self.harness._victron_ess_balance_telemetry_is_clean = MagicMock(return_value=(True, "stable"))
        self.harness._victron_ess_balance_process_clean_episode = MagicMock(return_value=(True, False))
        self.harness._victron_ess_balance_ev_power_w = MagicMock(return_value=123.5)
        self.harness._victron_ess_balance_stability_score = MagicMock(return_value=0.75)
        self.harness._victron_ess_balance_refresh_profile_stability = MagicMock()
        self.harness._populate_victron_ess_balance_telemetry_metrics = MagicMock()
        self.harness._update_victron_ess_balance_telemetry(svc, 99.5, cluster, -7.5, metrics, "fallback")
        self.harness._victron_ess_balance_telemetry_command_state.assert_called_once_with(svc, "fallback")
        self.harness._victron_ess_balance_telemetry_thresholds.assert_called_once_with(svc)
        self.harness._victron_ess_balance_telemetry_is_clean.assert_called_once_with(svc, cluster, -7.5)
        self.harness._victron_ess_balance_process_clean_episode.assert_called_once_with(
            svc, 99.5, -7.5, state, 15.0, 55.0, 30.0
        )
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_telemetry_clean": 1,
                "battery_discharge_balance_victron_bias_telemetry_clean_reason": "stable",
            },
        )
        self.assertIs(svc._victron_ess_balance_telemetry_overshoot_active, True)
        self.assertIs(svc._victron_ess_balance_telemetry_settling_active, False)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_observed_error_w, -7.5)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_observed_at, 99.5)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_grid_interaction_w, -12.5)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_ac_power_w, 34.5)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_ev_power_w, 123.5)
        self.assertEqual(svc._victron_ess_balance_telemetry_stability_score, 0.75)
        self.harness._victron_ess_balance_ev_power_w.assert_called_once_with(svc)
        self.harness._victron_ess_balance_stability_score.assert_called_once_with(svc)
        self.harness._victron_ess_balance_refresh_profile_stability.assert_called_once_with(svc, "profile-x")
        self.harness._populate_victron_ess_balance_telemetry_metrics.assert_called_once_with(svc, metrics)

    def test_dirty_or_inactive_episode_is_not_processed(self) -> None:
        svc = SimpleNamespace()
        metrics: dict[str, Any] = {}
        state = {"command_at": None, "command_error_w": 1.0, "command_setpoint_w": 2.0, "command_profile_key": ""}
        self.harness._victron_ess_balance_telemetry_command_state = MagicMock(return_value=state)
        self.harness._victron_ess_balance_telemetry_thresholds = MagicMock(return_value=(2.0, 50.0))
        self.harness._victron_ess_balance_telemetry_is_clean = MagicMock(return_value=(True, "clean"))
        self.harness._victron_ess_balance_process_clean_episode = MagicMock()
        self.harness._victron_ess_balance_ev_power_w = MagicMock(return_value=None)
        self.harness._victron_ess_balance_stability_score = MagicMock(return_value=0.0)
        self.harness._victron_ess_balance_refresh_profile_stability = MagicMock()
        self.harness._populate_victron_ess_balance_telemetry_metrics = MagicMock()
        self.harness._update_victron_ess_balance_telemetry(svc, 2.0, {}, 3.0, metrics, "fallback")
        self.harness._victron_ess_balance_process_clean_episode.assert_not_called()
        self.assertIs(svc._victron_ess_balance_telemetry_overshoot_active, False)
        self.assertIs(svc._victron_ess_balance_telemetry_settling_active, False)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "clean")
        self.harness._victron_ess_balance_refresh_profile_stability.assert_called_once_with(svc, "fallback")

    def test_improvement_threshold_has_ten_watt_floor(self) -> None:
        svc = SimpleNamespace()
        metrics: dict[str, Any] = {}
        state = {
            "command_at": 1.0,
            "command_error_w": 2.0,
            "command_setpoint_w": 3.0,
            "command_profile_key": "p",
        }
        self.harness._victron_ess_balance_telemetry_command_state = MagicMock(return_value=state)
        self.harness._victron_ess_balance_telemetry_thresholds = MagicMock(return_value=(2.0, 50.0))
        self.harness._victron_ess_balance_telemetry_is_clean = MagicMock(return_value=(True, "clean"))
        self.harness._victron_ess_balance_process_clean_episode = MagicMock(return_value=(False, True))
        self.harness._victron_ess_balance_ev_power_w = MagicMock(return_value=None)
        self.harness._victron_ess_balance_stability_score = MagicMock(return_value=0.5)
        self.harness._victron_ess_balance_refresh_profile_stability = MagicMock()
        self.harness._populate_victron_ess_balance_telemetry_metrics = MagicMock()
        self.harness._update_victron_ess_balance_telemetry(svc, 4.0, {}, 5.0, metrics, "fallback")
        self.harness._victron_ess_balance_process_clean_episode.assert_called_once_with(
            svc, 4.0, 5.0, state, 10.0, 50.0, 2.0
        )

    def test_integral_reset_wrapper_forwards_default_and_explicit_flag(self) -> None:
        svc = SimpleNamespace()
        original = telemetry_module._reset_victron_ess_balance_pid_integral_state
        replacement = MagicMock()
        telemetry_module._reset_victron_ess_balance_pid_integral_state = replacement
        try:
            _UpdateCycleVictronEssBalanceLearningTelemetry._reset_victron_ess_balance_pid_integral(svc)
            _UpdateCycleVictronEssBalanceLearningTelemetry._reset_victron_ess_balance_pid_integral(svc, True)
        finally:
            telemetry_module._reset_victron_ess_balance_pid_integral_state = original
        self.assertEqual(replacement.call_args_list[0].args, (svc, False))
        self.assertEqual(replacement.call_args_list[1].args, (svc, True))


if __name__ == "__main__":
    unittest.main()
