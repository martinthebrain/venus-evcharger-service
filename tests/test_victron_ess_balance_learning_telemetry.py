import unittest
from types import SimpleNamespace
from typing import Any

from venus_evcharger.update.victron_ess_balance_apply_write import _UpdateCycleVictronEssBalanceApplyWriteMixin
from venus_evcharger.update.victron_ess_balance_learning_telemetry import (
    _UpdateCycleVictronEssBalanceLearningTelemetryMixin,
    _victron_ess_balance_clamped_stability_score,
    _victron_ess_balance_delay_penalty,
    _victron_ess_balance_gain_bonus,
    _victron_ess_balance_overshoot_penalty,
    _victron_ess_balance_settle_ratio,
)


class _TelemetryHarness(_UpdateCycleVictronEssBalanceLearningTelemetryMixin):
    def __init__(self) -> None:
        self.clean_result = (True, "clean")
        self.delay_updates: list[tuple[str, float]] = []
        self.gain_updates: list[tuple[str, float]] = []
        self.counters: list[tuple[str, str]] = []
        self.cooldowns: list[tuple[float, str]] = []
        self.refreshed_profiles: list[str] = []

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _victron_ess_balance_profile_sample_count(profile: dict[str, Any]) -> int:
        return int(profile.get("sample_count", 0) or 0)

    def _victron_ess_balance_update_profile_delay(
        self,
        svc: Any,
        profile_key: str,
        response_delay_seconds: float,
    ) -> None:
        del svc
        self.delay_updates.append((profile_key, response_delay_seconds))

    def _victron_ess_balance_update_profile_gain(self, svc: Any, profile_key: str, gain_sample: float) -> None:
        del svc
        self.gain_updates.append((profile_key, gain_sample))

    def _victron_ess_balance_increment_profile_counter(
        self,
        svc: Any,
        profile_key: str,
        counter_name: str,
    ) -> None:
        del svc
        self.counters.append((profile_key, counter_name))

    def _enter_victron_ess_balance_overshoot_cooldown(self, svc: Any, now: float, reason: str) -> None:
        del svc
        self.cooldowns.append((now, reason))

    def _victron_ess_balance_telemetry_is_clean(
        self,
        svc: Any,
        cluster: dict[str, Any],
        source_error_w: float,
    ) -> tuple[bool, str]:
        del svc, cluster, source_error_w
        return self.clean_result

    @staticmethod
    def _victron_ess_balance_ev_power_w(svc: Any) -> float | None:
        return _TelemetryHarness._optional_float(getattr(svc, "ev_power", None))

    def _victron_ess_balance_refresh_profile_stability(self, svc: Any, profile_key: str) -> None:
        del svc
        self.refreshed_profiles.append(profile_key)

    @staticmethod
    def _populate_victron_ess_balance_telemetry_metrics(svc: Any, metrics: dict[str, Any]) -> None:
        metrics["telemetry_populated"] = bool(svc)


class VictronEssBalanceLearningTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _TelemetryHarness()

    def test_basic_score_helpers_cover_boundaries(self) -> None:
        self.assertEqual(_victron_ess_balance_settle_ratio(0, 0), 1.0)
        self.assertEqual(_victron_ess_balance_settle_ratio(2, 4), 0.5)
        self.assertEqual(_victron_ess_balance_overshoot_penalty(2, 0), 0.0)
        self.assertEqual(_victron_ess_balance_overshoot_penalty(10, 10), 0.6)
        self.assertEqual(_victron_ess_balance_gain_bonus(None), 0.0)
        self.assertEqual(_victron_ess_balance_gain_bonus(0.5), 0.15)
        self.assertEqual(_victron_ess_balance_delay_penalty(None), 0.0)
        self.assertGreater(_victron_ess_balance_delay_penalty(30.0), 0.0)
        self.assertEqual(_victron_ess_balance_clamped_stability_score(1.0, 0.2, 0.0, 0.0), 1.0)
        self.assertEqual(_victron_ess_balance_clamped_stability_score(0.0, 0.0, 2.0, 0.0), 0.0)

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
        self.assertEqual(self.harness._ewma_learned_value(None, 10.0, 0), 10.0)
        self.assertEqual(self.harness._ewma_learned_value(4.0, 8.0, 2), 5.0)

    def test_update_response_delay_gain_and_markers(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_telemetry_delay_samples=0, _victron_ess_balance_telemetry_gain_samples=0)
        self.harness._victron_ess_balance_update_response_delay(svc, "p", 3.0)
        self.harness._victron_ess_balance_update_gain(svc, "p", 0.5)
        self.assertEqual(self.harness.delay_updates, [("p", 3.0)])
        self.assertEqual(self.harness.gain_updates, [("p", 0.5)])
        self.harness._victron_ess_balance_mark_overshoot(svc, 10.0, "p")
        self.harness._victron_ess_balance_mark_settled(svc, "p")
        self.assertIn(("p", "overshoot_count"), self.harness.counters)
        self.assertIn(("p", "settled_count"), self.harness.counters)
        self.assertEqual(self.harness.cooldowns, [(10.0, "overshoot_detected")])

    def test_maybe_record_helpers_cover_skip_and_record_paths(self) -> None:
        svc = SimpleNamespace()
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
        )
        metrics: dict[str, Any] = {}
        cluster = {"battery_combined_grid_interaction_w": -12.0, "battery_combined_ac_power_w": 120.0}
        self.harness._update_victron_ess_balance_telemetry(svc, 100.0, cluster, -12.0, metrics, "fallback")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_ev_power_w, 123.0)
        self.assertEqual(self.harness.refreshed_profiles[-1], "profile-a")
        self.assertTrue(svc._victron_ess_balance_telemetry_overshoot_active)

        self.harness.clean_result = (False, "noisy")
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
        self.assertGreater(self.harness._victron_ess_balance_regime_consistency_score(profile), 0.0)
        self.assertEqual(self.harness._victron_ess_balance_reproducibility_score({"response_variance_score": 0.5}), 0.8)
        self.assertGreater(
            self.harness._victron_ess_balance_reproducibility_score(
                {"settled_count": 3, "overshoot_count": 1, "response_variance_score": 0.5}
            ),
            0.0,
        )

    def test_variance_and_stability_score_helpers(self) -> None:
        self.assertEqual(self.harness._victron_ess_balance_variance_ratio(None, 1.0, 1.0), 1.0)
        self.assertEqual(self.harness._victron_ess_balance_variance_ratio(0.0, 1.0, 1.0), 1.0)
        self.assertEqual(self.harness._victron_ess_balance_variance_ratio(10.0, None, 1.0), 1.0)
        self.assertGreater(self.harness._victron_ess_balance_variance_score(10.0, 1.0, 1.0, 0.1), 0.0)
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=2,
            _victron_ess_balance_telemetry_overshoot_count=1,
            _victron_ess_balance_telemetry_estimated_gain=0.5,
            _victron_ess_balance_telemetry_response_delay_seconds=12.0,
        )
        self.assertGreater(self.harness._victron_ess_balance_stability_score(svc), 0.0)

    def test_tracking_wrappers_and_gateway_dbus_guard(self) -> None:
        svc = SimpleNamespace()
        self.harness._record_victron_ess_balance_command(svc, 1.0, 50.0, -20.0, "p")
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_profile_key, "p")
        self.harness._clear_victron_ess_balance_tracking_episode(svc)
        self.assertIsNone(svc._victron_ess_balance_telemetry_last_command_at)
        self.harness._reset_victron_ess_balance_pid(svc)
        self.harness._reset_victron_ess_balance_pid_integral(svc, aggressive=True)
        with self.assertRaises(RuntimeError):
            _UpdateCycleVictronEssBalanceApplyWriteMixin._victron_ess_balance_dbus_module()


if __name__ == "__main__":
    unittest.main()
