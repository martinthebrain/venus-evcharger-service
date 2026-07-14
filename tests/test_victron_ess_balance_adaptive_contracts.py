import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

from venus_evcharger.update.victron_ess_balance_adaptive import _UpdateCycleVictronEssBalanceAdaptive


class _AdaptiveHarness(_UpdateCycleVictronEssBalanceAdaptive):
    def __init__(self) -> None:
        self.suspended = False
        self.activation_mode = "always"
        self.suspend_calls: list[tuple[Any, float]] = []
        self.activation_calls: list[Any] = []

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    def _victron_ess_balance_auto_apply_suspended(self, svc: Any, now: float) -> bool:
        self.suspend_calls.append((svc, now))
        return self.suspended

    def _victron_ess_balance_activation_mode(self, svc: Any) -> str:
        self.activation_calls.append(svc)
        return self.activation_mode


def _adaptive_service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "auto_battery_discharge_balance_victron_bias_auto_apply_enabled": True,
        "auto_battery_discharge_balance_victron_bias_rollback_enabled": True,
        "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence": 0.8,
        "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score": 0.7,
        "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples": 3,
        "auto_battery_discharge_balance_victron_bias_auto_apply_blend": 0.25,
        "auto_battery_discharge_balance_victron_bias_observation_window_seconds": 30.0,
        "_victron_ess_balance_auto_apply_generation": 4,
        "_victron_ess_balance_auto_apply_observe_until": 120.0,
        "_victron_ess_balance_auto_apply_last_applied_param": "kp",
        "_victron_ess_balance_auto_apply_last_applied_at": 90.0,
        "_victron_ess_balance_auto_apply_suspend_reason": "manual",
        "_victron_ess_balance_auto_apply_suspend_until": 150.0,
        "_victron_ess_balance_last_stable_profile_key": "stable-profile",
        "_victron_ess_balance_safe_state_active": True,
        "_victron_ess_balance_safe_state_reason": "guard",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AdaptiveMetricContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _AdaptiveHarness()

    def test_profile_keys_map_and_normalize_both_fields(self) -> None:
        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_profile_key": " recommendation ",
            "battery_discharge_balance_victron_bias_learning_profile_key": " active ",
        }
        self.assertEqual(self.harness._victron_ess_balance_profile_keys(metrics), ("recommendation", "active"))
        self.assertEqual(self.harness._victron_ess_balance_profile_keys({}), ("", ""))

    def test_runtime_metrics_publish_exact_service_state(self) -> None:
        svc = _adaptive_service()
        self.harness.suspended = True
        metrics: dict[str, Any] = {}
        self.harness._initialize_victron_ess_balance_auto_apply_runtime_metrics(svc, metrics, 100.0)
        self.assertEqual(self.harness.suspend_calls, [(svc, 100.0)])
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_auto_apply_enabled": 1,
                "battery_discharge_balance_victron_bias_auto_apply_active": 0,
                "battery_discharge_balance_victron_bias_auto_apply_reason": "disabled",
                "battery_discharge_balance_victron_bias_auto_apply_generation": 4,
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 0,
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": 120.0,
                "battery_discharge_balance_victron_bias_auto_apply_last_param": "kp",
                "battery_discharge_balance_victron_bias_auto_apply_suspend_active": 1,
                "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": "manual",
                "battery_discharge_balance_victron_bias_auto_apply_suspend_until": 150.0,
            },
        )

    def test_rollback_and_safe_state_metrics_are_exact(self) -> None:
        svc = _adaptive_service()
        metrics: dict[str, Any] = {}
        self.harness._initialize_victron_ess_balance_rollback_metrics(svc, metrics)
        self.harness._initialize_victron_ess_balance_safe_state_metrics(svc, metrics)
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_rollback_enabled": 1,
                "battery_discharge_balance_victron_bias_rollback_active": 0,
                "battery_discharge_balance_victron_bias_rollback_reason": "disabled",
                "battery_discharge_balance_victron_bias_rollback_stable_profile_key": "stable-profile",
                "battery_discharge_balance_victron_bias_safe_state_active": 1,
                "battery_discharge_balance_victron_bias_safe_state_reason": "guard",
            },
        )

    def test_composite_initializer_delegates_in_order(self) -> None:
        svc = _adaptive_service()
        metrics: dict[str, Any] = {}
        self.harness._initialize_victron_ess_balance_auto_apply_runtime_metrics = MagicMock()
        self.harness._initialize_victron_ess_balance_rollback_metrics = MagicMock()
        self.harness._initialize_victron_ess_balance_safe_state_metrics = MagicMock()
        self.harness._initialize_victron_ess_balance_auto_apply_metrics(svc, metrics, 7.0)
        self.harness._initialize_victron_ess_balance_auto_apply_runtime_metrics.assert_called_once_with(svc, metrics, 7.0)
        self.harness._initialize_victron_ess_balance_rollback_metrics.assert_called_once_with(svc, metrics)
        self.harness._initialize_victron_ess_balance_safe_state_metrics.assert_called_once_with(svc, metrics)


class AdaptiveReadinessContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _AdaptiveHarness()
        self.svc = _adaptive_service()

    def test_thresholds_clamp_each_independent_value(self) -> None:
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_thresholds(self.svc), (0.8, 0.7, 3))
        limited = _adaptive_service(
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=-1.0,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=-2.0,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=0,
        )
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_thresholds(limited), (0.0, 0.0, 1))

    def test_confidence_stability_and_sample_boundaries(self) -> None:
        confidence_key = "battery_discharge_balance_victron_bias_recommendation_confidence"
        stability_key = "battery_discharge_balance_victron_bias_learning_profile_stability_score"
        samples_key = "battery_discharge_balance_victron_bias_learning_profile_sample_count"
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_confidence_reason(self.svc, {}), "confidence_too_low")
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_confidence_reason(self.svc, {confidence_key: 0.79}),
            "confidence_too_low",
        )
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_confidence_reason(self.svc, {confidence_key: 0.8}), "")
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_stability_reason(self.svc, {}), "stability_too_low")
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_stability_reason(self.svc, {stability_key: 0.69}),
            "stability_too_low",
        )
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_stability_reason(self.svc, {stability_key: 0.7}), "")
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_sample_reason(self.svc, {samples_key: 2}),
            "insufficient_profile_samples",
        )
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_sample_reason(
                _adaptive_service(auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=1),
                {},
            ),
            "insufficient_profile_samples",
        )
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_sample_reason(self.svc, {samples_key: -1}),
            "insufficient_profile_samples",
        )
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_sample_reason(self.svc, {samples_key: 3}), "")

    def test_profile_match_requires_both_equal_nonempty_keys(self) -> None:
        key_a = "battery_discharge_balance_victron_bias_recommendation_profile_key"
        key_b = "battery_discharge_balance_victron_bias_learning_profile_key"
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_profile_reason({}), "profile_mismatch")
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_profile_reason({key_a: "p", key_b: "q"}), "profile_mismatch")
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_profile_reason({key_a: "p", key_b: "p"}), "")

    def test_readiness_returns_first_failed_contract(self) -> None:
        self.harness._victron_ess_balance_auto_apply_confidence_reason = MagicMock(return_value="first")
        self.harness._victron_ess_balance_auto_apply_stability_reason = MagicMock(return_value="second")
        self.harness._victron_ess_balance_auto_apply_sample_reason = MagicMock(return_value="third")
        self.harness._victron_ess_balance_auto_apply_profile_reason = MagicMock(return_value="fourth")
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_readiness(self.svc, {}), "first")
        self.harness._victron_ess_balance_auto_apply_stability_reason.assert_not_called()
        self.harness._victron_ess_balance_auto_apply_confidence_reason.return_value = ""
        self.harness._victron_ess_balance_auto_apply_stability_reason.return_value = ""
        self.harness._victron_ess_balance_auto_apply_sample_reason.return_value = ""
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_readiness(self.svc, {}), "fourth")


class AdaptiveTimingAndApplyContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _AdaptiveHarness()

    def test_blend_and_observation_window_clamp_exactly(self) -> None:
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_blend(_adaptive_service()), 0.25)
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_blend(
                _adaptive_service(auto_battery_discharge_balance_victron_bias_auto_apply_blend=-1.0)
            ),
            0.0,
        )
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_blend(
                _adaptive_service(auto_battery_discharge_balance_victron_bias_auto_apply_blend=2.0)
            ),
            1.0,
        )
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_observation_until(_adaptive_service(), 10.0), 40.0)
        self.assertIsNone(
            self.harness._victron_ess_balance_auto_apply_observation_until(
                _adaptive_service(auto_battery_discharge_balance_victron_bias_observation_window_seconds=0.0),
                10.0,
            )
        )
        self.assertEqual(
            self.harness._victron_ess_balance_auto_apply_observation_until(
                _adaptive_service(auto_battery_discharge_balance_victron_bias_observation_window_seconds=0.5),
                10.0,
            ),
            10.5,
        )

    def test_observation_reason_includes_end_boundary(self) -> None:
        svc = _adaptive_service(_victron_ess_balance_auto_apply_observe_until=12.0)
        metrics: dict[str, Any] = {}
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_observation_reason(svc, metrics, 11.99), "observation_window_active")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"], 1)
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_observation_reason(svc, {}, 12.0), "")
        svc._victron_ess_balance_auto_apply_observe_until = None
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_observation_reason(svc, {}, 1.0), "")

    def test_apply_step_records_exact_transition_and_save(self) -> None:
        svc = _adaptive_service(_victron_ess_balance_auto_apply_generation=2)
        svc._save_runtime_state = MagicMock()
        metrics: dict[str, Any] = {}
        self.harness._victron_ess_balance_auto_apply_blend = MagicMock(return_value=0.4)
        self.harness._apply_victron_ess_balance_recommended_tuning_step = MagicMock(return_value="ki")
        self.harness._victron_ess_balance_auto_apply_observation_until = MagicMock(return_value=130.0)
        self.assertTrue(self.harness._apply_victron_ess_balance_auto_apply_step(svc, metrics, 100.0))
        self.harness._apply_victron_ess_balance_recommended_tuning_step.assert_called_once_with(svc, metrics, 0.4)
        self.assertEqual(svc._victron_ess_balance_auto_apply_generation, 3)
        self.assertEqual(svc._victron_ess_balance_auto_apply_observe_until, 130.0)
        self.assertEqual(svc._victron_ess_balance_auto_apply_last_applied_param, "ki")
        self.assertEqual(svc._victron_ess_balance_auto_apply_last_applied_at, 100.0)
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_auto_apply_active": 1,
                "battery_discharge_balance_victron_bias_auto_apply_reason": "applied_step",
                "battery_discharge_balance_victron_bias_auto_apply_generation": 3,
                "battery_discharge_balance_victron_bias_auto_apply_last_param": "ki",
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": 130.0,
            },
        )
        svc._save_runtime_state.assert_called_once_with()

    def test_apply_step_no_change_has_no_state_transition(self) -> None:
        svc = _adaptive_service()
        metrics: dict[str, Any] = {}
        self.harness._apply_victron_ess_balance_recommended_tuning_step = MagicMock(return_value="")
        self.assertFalse(self.harness._apply_victron_ess_balance_auto_apply_step(svc, metrics, 100.0))
        self.assertEqual(metrics, {"battery_discharge_balance_victron_bias_auto_apply_reason": "already_at_recommendation"})
        self.assertEqual(svc._victron_ess_balance_auto_apply_generation, 4)

    def test_apply_step_normalizes_negative_generation_before_increment(self) -> None:
        svc = _adaptive_service(_victron_ess_balance_auto_apply_generation=-4)
        metrics: dict[str, Any] = {}
        self.harness._apply_victron_ess_balance_recommended_tuning_step = MagicMock(return_value="kp")
        self.assertTrue(self.harness._apply_victron_ess_balance_auto_apply_step(svc, metrics, 10.0))
        self.assertEqual(svc._victron_ess_balance_auto_apply_generation, 1)

    def test_runtime_save_is_optional_but_calls_callable(self) -> None:
        svc = SimpleNamespace(_save_runtime_state=MagicMock())
        self.harness._victron_ess_balance_save_runtime_state(svc)
        svc._save_runtime_state.assert_called_once_with()
        self.harness._victron_ess_balance_save_runtime_state(SimpleNamespace())


class AdaptiveSchedulingAndSettingContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _AdaptiveHarness()

    def test_blocker_returns_first_reason_in_fixed_order(self) -> None:
        svc = _adaptive_service()
        metrics: dict[str, Any] = {}
        self.harness._victron_ess_balance_auto_apply_suspend_reason = MagicMock(return_value="suspend")
        self.harness._victron_ess_balance_auto_apply_rollback_reason = MagicMock(return_value="rollback")
        self.harness._victron_ess_balance_auto_apply_observation_reason = MagicMock(return_value="observe")
        self.harness._victron_ess_balance_auto_apply_readiness = MagicMock(return_value="readiness")
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_blocker_reason(svc, metrics, 1.0), "suspend")
        self.harness._victron_ess_balance_auto_apply_suspend_reason.assert_called_once_with(svc, 1.0)
        self.harness._victron_ess_balance_auto_apply_rollback_reason.assert_not_called()
        for method in (
            self.harness._victron_ess_balance_auto_apply_suspend_reason,
            self.harness._victron_ess_balance_auto_apply_rollback_reason,
            self.harness._victron_ess_balance_auto_apply_observation_reason,
            self.harness._victron_ess_balance_auto_apply_readiness,
        ):
            method.return_value = ""
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_blocker_reason(svc, metrics, 1.0), "")
        self.harness._victron_ess_balance_auto_apply_readiness.assert_called_once_with(svc, metrics)

    def test_rollback_reason_owns_restore_result_and_arguments(self) -> None:
        svc = _adaptive_service()
        metrics: dict[str, Any] = {}
        self.harness._victron_ess_balance_should_rollback_stable_tuning = MagicMock(return_value=False)
        self.harness._maybe_restore_victron_ess_balance_stable_tuning = MagicMock(return_value=True)
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_rollback_reason(svc, metrics, 4.0), "")
        self.harness._maybe_restore_victron_ess_balance_stable_tuning.assert_not_called()
        self.harness._victron_ess_balance_should_rollback_stable_tuning.return_value = True
        self.harness._maybe_restore_victron_ess_balance_stable_tuning.return_value = False
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_rollback_reason(svc, metrics, 5.0), "")
        self.harness._maybe_restore_victron_ess_balance_stable_tuning.return_value = True
        self.assertEqual(self.harness._victron_ess_balance_auto_apply_rollback_reason(svc, metrics, 6.0), "rolled_back")
        self.harness._maybe_restore_victron_ess_balance_stable_tuning.assert_called_with(
            svc,
            metrics,
            "unstable_observation_window",
        )

    def test_maybe_apply_honors_disabled_blocked_rollback_and_ready_states(self) -> None:
        svc = _adaptive_service()
        metrics: dict[str, Any] = {}
        self.harness._initialize_victron_ess_balance_auto_apply_metrics = MagicMock()
        self.harness._victron_ess_balance_auto_apply_enabled = MagicMock(return_value=False)
        self.harness._victron_ess_balance_auto_apply_blocker_reason = MagicMock()
        self.harness._apply_victron_ess_balance_auto_apply_step = MagicMock()
        self.harness._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 1.0)
        self.harness._victron_ess_balance_auto_apply_blocker_reason.assert_not_called()
        self.harness._victron_ess_balance_auto_apply_enabled.return_value = True
        self.harness._victron_ess_balance_auto_apply_blocker_reason.return_value = "blocked"
        self.harness._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 2.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "blocked")
        self.harness._victron_ess_balance_auto_apply_blocker_reason.return_value = "rolled_back"
        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "rollback-owned"
        self.harness._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 3.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "rollback-owned")
        self.harness._victron_ess_balance_auto_apply_blocker_reason.return_value = ""
        self.harness._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 4.0)
        self.harness._apply_victron_ess_balance_auto_apply_step.assert_called_once_with(svc, metrics, 4.0)

    def test_recommended_setting_pairs_are_complete_and_ordered(self) -> None:
        pairs = self.harness._victron_ess_balance_recommended_setting_pairs()
        self.assertEqual(
            pairs,
            (
                (
                    "auto_battery_discharge_balance_victron_bias_deadband_watts",
                    "battery_discharge_balance_victron_bias_recommended_deadband_watts",
                ),
                (
                    "auto_battery_discharge_balance_victron_bias_max_abs_watts",
                    "battery_discharge_balance_victron_bias_recommended_max_abs_watts",
                ),
                (
                    "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
                    "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second",
                ),
                (
                    "auto_battery_discharge_balance_victron_bias_kp",
                    "battery_discharge_balance_victron_bias_recommended_kp",
                ),
                (
                    "auto_battery_discharge_balance_victron_bias_ki",
                    "battery_discharge_balance_victron_bias_recommended_ki",
                ),
                (
                    "auto_battery_discharge_balance_victron_bias_kd",
                    "battery_discharge_balance_victron_bias_recommended_kd",
                ),
            ),
        )

    def test_blend_setting_handles_absence_equality_and_exact_formula(self) -> None:
        svc = SimpleNamespace(value=10.0)
        self.assertFalse(self.harness._blend_recommended_setting(svc, "missing", 20.0, 0.25))
        self.assertFalse(self.harness._blend_recommended_setting(svc, "value", None, 0.25))
        self.assertFalse(self.harness._blend_recommended_setting(svc, "value", 10.0000001, 0.25))
        svc.value = 0.0
        self.assertTrue(self.harness._blend_recommended_setting(svc, "value", 0.000001, 0.5))
        self.assertEqual(svc.value, 0.0000005)
        svc.value = 10.0
        self.assertTrue(self.harness._blend_recommended_setting(svc, "value", 10.5, 0.5))
        self.assertEqual(svc.value, 10.25)
        svc.value = 10.0
        self.assertTrue(self.harness._blend_recommended_setting(svc, "value", 20.0, 0.25))
        self.assertEqual(svc.value, 12.5)

    def test_activation_step_changes_only_mismatching_nonempty_mode(self) -> None:
        svc = SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="always")
        key = "battery_discharge_balance_victron_bias_recommended_activation_mode"
        self.assertEqual(self.harness._victron_ess_balance_recommended_activation_step(svc, {}), "")
        self.assertEqual(self.harness._victron_ess_balance_recommended_activation_step(svc, {key: "   "}), "")
        self.assertEqual(self.harness._victron_ess_balance_recommended_activation_step(svc, {key: "always"}), "")
        self.assertEqual(
            self.harness._victron_ess_balance_recommended_activation_step(svc, {key: " export_only "}),
            "auto_battery_discharge_balance_victron_bias_activation_mode",
        )
        self.assertEqual(svc.auto_battery_discharge_balance_victron_bias_activation_mode, "export_only")
        self.assertEqual(self.harness.activation_calls[-1], svc)


if __name__ == "__main__":
    unittest.main()
