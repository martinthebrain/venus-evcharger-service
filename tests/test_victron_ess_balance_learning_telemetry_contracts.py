import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from venus_evcharger.update.victron_ess_balance_scoring import (
    clamped_stability_score,
    delay_penalty_value,
    gain_bonus_value,
    overshoot_penalty_value,
    settle_ratio_value,
)
from tests.support.victron_ess_balance import build_victron_ess_components


class _TelemetryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        components = build_victron_ess_components()
        self.harness = components.telemetry
        self.scorer = components.scorer
        self.profiles = components.profiles
        self.recovery = components.recovery
        self.safety = components.safety
        self.sources = components.sources
        self.recommendation = components.recommendation
        self.pid = components.pid
        self.profile_delay = self._patch_method(self.profiles, "_victron_ess_balance_update_profile_delay")
        self.profile_gain = self._patch_method(self.profiles, "_victron_ess_balance_update_profile_gain")
        self.profile_counter = self._patch_method(self.profiles, "_victron_ess_balance_increment_profile_counter")
        self.cooldown = self._patch_method(self.recovery, "_enter_victron_ess_balance_overshoot_cooldown")
        self.integral_reset = self._patch_method(self.pid, "reset_integral")

    def _patch_method(self, target: object, name: str, **kwargs: object) -> MagicMock:
        patcher = patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()


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


class TelemetryPureScoreContracts(_TelemetryTestCase):
    def test_outcome_ratios_and_penalties_have_exact_boundaries(self) -> None:
        self.assertEqual(settle_ratio_value(4, -1), 1.0)
        self.assertEqual(settle_ratio_value(1, 4), 0.25)
        self.assertEqual(settle_ratio_value(0, 1), 0.0)
        self.assertEqual(overshoot_penalty_value(3, -1), 0.0)
        self.assertEqual(overshoot_penalty_value(1, 4), 0.25)
        self.assertEqual(overshoot_penalty_value(1, 1), 0.6)
        self.assertEqual(overshoot_penalty_value(4, 4), 0.6)

    def test_gain_and_delay_adjustments_have_exact_boundaries(self) -> None:
        for value in (None, -1.0, 0.0):
            self.assertEqual(gain_bonus_value(value), 0.0)
        self.assertEqual(gain_bonus_value(0.01), 0.15)
        self.assertEqual(delay_penalty_value(None), 0.0)
        self.assertEqual(delay_penalty_value(4.0), 0.0)
        self.assertEqual(delay_penalty_value(10.0), 0.1)
        self.assertEqual(delay_penalty_value(20.0), 0.2)
        self.assertEqual(delay_penalty_value(100.0), 0.2)

    def test_stability_clamp_preserves_each_term(self) -> None:
        self.assertAlmostEqual(clamped_stability_score(0.5, 0.1, 0.2, 0.05), 0.5)
        self.assertEqual(clamped_stability_score(2.0, 1.0, 0.0, 0.0), 1.0)
        self.assertEqual(clamped_stability_score(0.0, 0.0, 1.0, 1.0), 0.0)


class TelemetryStateContracts(_TelemetryTestCase):

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
        self.assertEqual(self.scorer.ewma_learned_value(None, 8.0, 4), 8.0)
        self.assertEqual(self.scorer.ewma_learned_value(2.0, 8.0, 0), 8.0)
        self.assertEqual(self.scorer.ewma_learned_value(2.0, 10.0, 1), 4.0)

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
        self.profile_delay.assert_called_once_with(svc, "delay-profile", 8.0)
        self.profile_gain.assert_called_once_with(svc, "gain-profile", 0.8)

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
        self.profile_counter.assert_any_call(svc, "profile-o", "overshoot_count")
        self.profile_counter.assert_any_call(svc, "profile-s", "settled_count")
        self.cooldown.assert_called_once_with(svc, 12.5, "overshoot_detected")
        self.integral_reset.assert_called_once_with(svc, aggressive=True)


class TelemetryDecisionContracts(_TelemetryTestCase):

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


class TelemetryAggregateScoreContracts(_TelemetryTestCase):

    def test_stability_values_and_service_mapping_are_exact(self) -> None:
        self.assertAlmostEqual(self.scorer.stability_score_values(3, 1, 0.5, 10.0), 0.55)
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=3,
            _victron_ess_balance_telemetry_overshoot_count=1,
            _victron_ess_balance_telemetry_estimated_gain=0.5,
            _victron_ess_balance_telemetry_response_delay_seconds=10.0,
        )
        self.assertAlmostEqual(self.scorer.stability_score(svc), 0.55)
        empty = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_estimated_gain=None,
            _victron_ess_balance_telemetry_response_delay_seconds=None,
        )
        self.assertAlmostEqual(self.scorer.stability_score(empty), 0.85)
        empty._victron_ess_balance_telemetry_overshoot_count = 1
        self.assertEqual(self.scorer.stability_score(empty), 0.0)

    def test_variance_scores_preserve_floor_mean_and_mad(self) -> None:
        ratio = self.scorer.variance_ratio
        self.assertEqual(ratio(None, 2.0, 1.0), 1.0)
        self.assertEqual(ratio(0.0, 2.0, 1.0), 1.0)
        self.assertEqual(ratio(4.0, None, 1.0), 1.0)
        self.assertEqual(ratio(4.0, 1.0, 1.0), 0.75)
        self.assertEqual(ratio(0.5, 1.0, 1.0), 0.0)
        self.assertAlmostEqual(self.scorer.variance_score(4.0, 1.0, 1.0, 0.2), 0.775)
        self.assertAlmostEqual(self.scorer.variance_score(0.5, 0.25, 1.0, 0.2), 0.775)

    def test_profile_scores_preserve_weights_counts_and_clamps(self) -> None:
        profile = {"delay_samples": 2, "stability_score": 0.4, "response_variance_score": 0.6}
        self.assertAlmostEqual(self.scorer.regime_consistency_score(profile), 0.5)
        self.assertEqual(
            self.scorer.regime_consistency_score(
                {"delay_samples": 10, "stability_score": 10.0, "response_variance_score": 10.0}
            ),
            1.0,
        )
        self.assertEqual(self.scorer.regime_consistency_score({}), 0.0)
        self.assertEqual(
            self.scorer.regime_consistency_score(
                {"delay_samples": 5, "stability_score": 0.0, "response_variance_score": 0.0}
            ),
            0.3,
        )
        self.assertAlmostEqual(
            self.scorer.reproducibility_score(
                {"settled_count": 3, "overshoot_count": 1, "response_variance_score": 0.5}
            ),
            0.65,
        )
        self.assertEqual(
            self.scorer.reproducibility_score(
                {"settled_count": 0, "overshoot_count": 0}
            ),
            0.6,
        )
        self.assertEqual(
            self.scorer.reproducibility_score(
                {"settled_count": 1, "overshoot_count": 0, "response_variance_score": 10.0}
            ),
            1.0,
        )
        self.assertEqual(
            self.scorer.reproducibility_score(
                {"settled_count": 0, "overshoot_count": 1, "response_variance_score": 0.0}
            ),
            0.0,
        )


class TelemetryOrchestrationContracts(_TelemetryTestCase):

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
        clean = self._patch_method(
            self.safety, "_victron_ess_balance_telemetry_is_clean", return_value=(True, "stable")
        )
        self.harness._victron_ess_balance_process_clean_episode = MagicMock(return_value=(True, False))
        ev_power = self._patch_method(self.sources, "_victron_ess_balance_ev_power_w", return_value=123.5)
        stability = self._patch_method(self.scorer, "stability_score", return_value=0.75)
        refresh = self._patch_method(self.profiles, "_victron_ess_balance_refresh_profile_stability")
        populate = self._patch_method(self.recommendation, "_populate_victron_ess_balance_telemetry_metrics")
        self.harness._update_victron_ess_balance_telemetry(svc, 99.5, cluster, -7.5, metrics, "fallback")
        self.harness._victron_ess_balance_telemetry_command_state.assert_called_once_with(svc, "fallback")
        self.harness._victron_ess_balance_telemetry_thresholds.assert_called_once_with(svc)
        clean.assert_called_once_with(svc, cluster, -7.5)
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
        ev_power.assert_called_once_with(svc)
        stability.assert_called_once_with(svc)
        refresh.assert_called_once_with(svc, "profile-x")
        populate.assert_called_once_with(svc, metrics)

    def test_dirty_or_inactive_episode_is_not_processed(self) -> None:
        svc = SimpleNamespace()
        metrics: dict[str, Any] = {}
        state = {"command_at": None, "command_error_w": 1.0, "command_setpoint_w": 2.0, "command_profile_key": ""}
        self.harness._victron_ess_balance_telemetry_command_state = MagicMock(return_value=state)
        self.harness._victron_ess_balance_telemetry_thresholds = MagicMock(return_value=(2.0, 50.0))
        self._patch_method(self.safety, "_victron_ess_balance_telemetry_is_clean", return_value=(True, "clean"))
        self.harness._victron_ess_balance_process_clean_episode = MagicMock()
        self._patch_method(self.sources, "_victron_ess_balance_ev_power_w", return_value=None)
        self._patch_method(self.scorer, "stability_score", return_value=0.0)
        refresh = self._patch_method(self.profiles, "_victron_ess_balance_refresh_profile_stability")
        self._patch_method(self.recommendation, "_populate_victron_ess_balance_telemetry_metrics")
        self.harness._update_victron_ess_balance_telemetry(svc, 2.0, {}, 3.0, metrics, "fallback")
        self.harness._victron_ess_balance_process_clean_episode.assert_not_called()
        self.assertIs(svc._victron_ess_balance_telemetry_overshoot_active, False)
        self.assertIs(svc._victron_ess_balance_telemetry_settling_active, False)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "clean")
        refresh.assert_called_once_with(svc, "fallback")

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
        self._patch_method(self.safety, "_victron_ess_balance_telemetry_is_clean", return_value=(True, "clean"))
        self.harness._victron_ess_balance_process_clean_episode = MagicMock(return_value=(False, True))
        self._patch_method(self.sources, "_victron_ess_balance_ev_power_w", return_value=None)
        self._patch_method(self.scorer, "stability_score", return_value=0.5)
        self._patch_method(self.profiles, "_victron_ess_balance_refresh_profile_stability")
        self._patch_method(self.recommendation, "_populate_victron_ess_balance_telemetry_metrics")
        self.harness._update_victron_ess_balance_telemetry(svc, 4.0, {}, 5.0, metrics, "fallback")
        self.harness._victron_ess_balance_process_clean_episode.assert_called_once_with(
            svc, 4.0, 5.0, state, 10.0, 50.0, 2.0
        )

    def test_integral_reset_preserves_output_until_aggressive_reset(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_pid_integral_output_w=12.0,
            _victron_ess_balance_pid_last_output_w=8.0,
        )
        type(self.pid).reset_integral(svc)
        self.assertEqual(svc._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertEqual(svc._victron_ess_balance_pid_last_output_w, 8.0)
        type(self.pid).reset_integral(svc, aggressive=True)
        self.assertEqual(svc._victron_ess_balance_pid_last_output_w, 0.0)


if __name__ == "__main__":
    unittest.main()
