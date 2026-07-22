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


class VictronEssBalanceLearningTelemetryTests(unittest.TestCase):
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
        self.delay_update = self._patch_method(
            self.profiles, "_victron_ess_balance_update_profile_delay"
        )
        self.gain_update = self._patch_method(
            self.profiles, "_victron_ess_balance_update_profile_gain"
        )
        self.counter_update = self._patch_method(
            self.profiles, "_victron_ess_balance_increment_profile_counter"
        )
        self.cooldown = self._patch_method(
            self.recovery, "_enter_victron_ess_balance_overshoot_cooldown"
        )
        self.profile_refresh = self._patch_method(
            self.profiles, "_victron_ess_balance_refresh_profile_stability"
        )
        self.clean = self._patch_method(
            self.safety,
            "_victron_ess_balance_telemetry_is_clean",
            return_value=(True, "clean"),
        )
        self.ev_power = self._patch_method(
            self.sources,
            "_victron_ess_balance_ev_power_w",
            side_effect=lambda svc: self.sources._optional_float(getattr(svc, "ev_power", None)),
        )
        self.populate = self._patch_method(
            self.recommendation,
            "_populate_victron_ess_balance_telemetry_metrics",
            side_effect=lambda svc, metrics: metrics.update(telemetry_populated=bool(svc)),
        )
        self.integral_reset = self._patch_method(self.pid, "reset_integral")

    def _patch_method(self, target: object, name: str, **kwargs: object) -> MagicMock:
        patcher = patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_basic_score_helpers_cover_boundaries(self) -> None:
        self.assertEqual(settle_ratio_value(0, 0), 1.0)
        self.assertEqual(settle_ratio_value(2, 4), 0.5)
        self.assertEqual(overshoot_penalty_value(2, 0), 0.0)
        self.assertEqual(overshoot_penalty_value(10, 10), 0.6)
        self.assertEqual(gain_bonus_value(None), 0.0)
        self.assertEqual(gain_bonus_value(0.5), 0.15)
        self.assertEqual(delay_penalty_value(None), 0.0)
        self.assertGreater(delay_penalty_value(30.0), 0.0)
        self.assertEqual(clamped_stability_score(1.0, 0.2, 0.0, 0.0), 1.0)
        self.assertEqual(clamped_stability_score(0.0, 0.0, 2.0, 0.0), 0.0)

    def test_telemetry_static_helpers_cover_edges(self) -> None:
        stats = self.harness._victron_ess_balance_improvement_stats(-5.0, 20.0, 75.0, 50.0)
        self.assertEqual(stats["improvement_w"], 15.0)
        self.assertEqual(stats["setpoint_bias_w"], 25.0)
        self.assertTrue(self.harness._victron_ess_balance_is_overshoot(-20.0, 10.0, 20.0, 10.0))
        self.assertFalse(self.harness._victron_ess_balance_is_overshoot(0.0, 10.0, 20.0, 10.0))
        self.assertFalse(self.harness._victron_ess_balance_is_overshoot(-1.0, 10.0, 1.0, 10.0))
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=12.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=42.0,
        )
        self.assertEqual(self.harness._victron_ess_balance_telemetry_thresholds(svc), (12.0, 42.0))
        self.assertEqual(self.scorer.ewma_learned_value(None, 10.0, 0), 10.0)
        self.assertEqual(self.scorer.ewma_learned_value(4.0, 8.0, 2), 5.0)

    def test_update_response_delay_gain_and_markers(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_delay_samples=0,
            _victron_ess_balance_telemetry_gain_samples=0,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_settled_count=0,
        )
        self.harness._victron_ess_balance_update_response_delay(svc, "p", 3.0)
        self.harness._victron_ess_balance_update_gain(svc, "p", 0.5)
        self.delay_update.assert_called_once_with(svc, "p", 3.0)
        self.gain_update.assert_called_once_with(svc, "p", 0.5)
        self.harness._victron_ess_balance_mark_overshoot(svc, 10.0, "p")
        self.harness._victron_ess_balance_mark_settled(svc, "p")
        self.counter_update.assert_any_call(svc, "p", "overshoot_count")
        self.counter_update.assert_any_call(svc, "p", "settled_count")
        self.cooldown.assert_called_once_with(svc, 10.0, "overshoot_detected")

    def test_maybe_record_helpers_cover_skip_and_record_paths(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_settled_count=0,
        )
        state = {"command_response_recorded": True, "command_overshoot_recorded": True, "command_settled_recorded": True}
        self.harness._victron_ess_balance_maybe_record_response_delay(svc, 20.0, state, "p", 1.0, 10.0, 5.0)
        self.harness._victron_ess_balance_maybe_record_gain(svc, "p", 0.0, 2.0)
        self.harness._victron_ess_balance_maybe_mark_overshoot(svc, 20.0, -10.0, state, "p", 10.0, 10.0, 5.0)
        self.harness._victron_ess_balance_maybe_mark_settled(svc, state, "p", 0.0, 5.0)
        state = {"command_response_recorded": False, "command_overshoot_recorded": False, "command_settled_recorded": False}
        self.harness._victron_ess_balance_maybe_record_response_delay(svc, 20.0, state, "p", 11.0, 10.0, 5.0)
        self.harness._victron_ess_balance_maybe_record_gain(svc, "p", 10.0, 5.0)
        self.harness._victron_ess_balance_maybe_mark_overshoot(svc, 20.0, 10.0, state, "p", 10.0, 10.0, 5.0)
        self.harness._victron_ess_balance_maybe_mark_overshoot(svc, 20.0, -10.0, state, "p", 10.0, 10.0, 5.0)
        self.assertTrue(state["command_response_recorded"])
        self.assertTrue(state["command_overshoot_recorded"])
        state["command_settled_recorded"] = False
        self.harness._victron_ess_balance_maybe_mark_settled(svc, state, "p", 1.0, 5.0)
        self.assertTrue(state["command_settled_recorded"])

    def test_process_clean_episode_and_update_telemetry(self) -> None:
        svc = SimpleNamespace(
            ev_power=123.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=4.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            _victron_ess_balance_telemetry_last_command_at=90.0,
            _victron_ess_balance_telemetry_last_command_error_w=30.0,
            _victron_ess_balance_telemetry_last_command_setpoint_w=70.0,
            _victron_ess_balance_telemetry_last_command_profile_key="profile-a",
            _victron_ess_balance_telemetry_command_response_recorded=False,
            _victron_ess_balance_telemetry_command_overshoot_recorded=False,
            _victron_ess_balance_telemetry_command_settled_recorded=False,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_estimated_gain=None,
            _victron_ess_balance_telemetry_response_delay_seconds=None,
        )
        metrics: dict[str, Any] = {}
        cluster = {"battery_combined_grid_interaction_w": -12.0, "battery_combined_ac_power_w": 120.0}
        self.harness._update_victron_ess_balance_telemetry(svc, 100.0, cluster, -12.0, metrics, "fallback")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_ev_power_w, 123.0)
        self.profile_refresh.assert_called_with(svc, "profile-a")
        self.assertTrue(svc._victron_ess_balance_telemetry_overshoot_active)

        self.clean.return_value = (False, "noisy")
        metrics = {}
        svc._victron_ess_balance_telemetry_last_command_at = None
        self.harness._update_victron_ess_balance_telemetry(svc, 101.0, cluster, 2.0, metrics, "fallback")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "noisy")
        self.assertFalse(svc._victron_ess_balance_telemetry_overshoot_active)

    def test_settling_episode_and_profile_scores(self) -> None:
        state = {
            "command_at": 0.0,
            "command_error_w": 30.0,
            "command_setpoint_w": 60.0,
            "command_profile_key": "p",
            "command_response_recorded": False,
            "command_overshoot_recorded": False,
            "command_settled_recorded": False,
        }
        overshoot, settling = self.harness._victron_ess_balance_process_clean_episode(
            SimpleNamespace(),
            20.0,
            15.0,
            state,
            10.0,
            50.0,
            4.0,
        )
        self.assertFalse(overshoot)
        self.assertTrue(settling)
        profile = {"sample_count": 4, "stability_score": 0.8, "response_variance_score": 0.6}
        self.assertGreater(self.scorer.regime_consistency_score(profile), 0.0)
        self.assertEqual(
            self.scorer.reproducibility_score(
                {"settled_count": 0, "overshoot_count": 0, "response_variance_score": 0.5}
            ),
            0.8,
        )
        self.assertGreater(
            self.scorer.reproducibility_score(
                {"settled_count": 3, "overshoot_count": 1, "response_variance_score": 0.5}
            ),
            0.0,
        )

    def test_variance_and_stability_score_helpers(self) -> None:
        self.assertEqual(self.scorer.variance_ratio(None, 1.0, 1.0), 1.0)
        self.assertEqual(self.scorer.variance_ratio(0.0, 1.0, 1.0), 1.0)
        self.assertEqual(self.scorer.variance_ratio(10.0, None, 1.0), 1.0)
        self.assertGreater(self.scorer.variance_score(10.0, 1.0, 1.0, 0.1), 0.0)
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=2,
            _victron_ess_balance_telemetry_overshoot_count=1,
            _victron_ess_balance_telemetry_estimated_gain=0.5,
            _victron_ess_balance_telemetry_response_delay_seconds=12.0,
        )
        self.assertGreater(self.scorer.stability_score(svc), 0.0)

    def test_tracking_wrappers_reset_episode_and_pid_state(self) -> None:
        svc = SimpleNamespace()
        self.harness._record_victron_ess_balance_command(svc, 1.0, 50.0, -20.0, "p")
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_profile_key, "p")
        self.harness._clear_victron_ess_balance_tracking_episode(svc)
        self.assertIsNone(svc._victron_ess_balance_telemetry_last_command_at)
        self.pid.reset(svc)
        self.pid.reset_integral(svc, aggressive=True)


if __name__ == "__main__":
    unittest.main()
