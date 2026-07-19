# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronAdaptiveCasesPart1:
    def test_victron_adaptive_helper_branches(self) -> None:
        components = _components()
        adaptive = components.adaptive
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=False,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.85,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.75,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=3,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.25,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=30.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=300.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=40.0,
            _victron_ess_balance_auto_apply_observe_until=50.0,
            _victron_ess_balance_auto_apply_suspend_until=60.0,
            _victron_ess_balance_auto_apply_suspend_reason="cooldown",
            _victron_ess_balance_last_stable_profile_key="stable",
        )
        initialize_victron_test_service(svc)
        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.1,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.1,
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 1,
            "battery_discharge_balance_victron_bias_recommendation_profile_key": "recommended",
            "battery_discharge_balance_victron_bias_learning_profile_key": "active",
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "always",
        }

        self.assertEqual(adaptive._victron_ess_balance_auto_apply_confidence_reason(svc, metrics), "confidence_too_low")
        self.assertEqual(adaptive._victron_ess_balance_auto_apply_stability_reason(svc, metrics), "stability_too_low")
        self.assertEqual(adaptive._victron_ess_balance_auto_apply_sample_reason(svc, metrics), "insufficient_profile_samples")
        self.assertEqual(adaptive._victron_ess_balance_auto_apply_profile_reason(metrics), "profile_mismatch")
        self.assertEqual(adaptive._victron_ess_balance_auto_apply_observation_until(svc, 10.0), 40.0)
        svc.auto_battery_discharge_balance_victron_bias_observation_window_seconds = -1.0
        self.assertIsNone(adaptive._victron_ess_balance_auto_apply_observation_until(svc, 10.0))
        self.assertEqual(adaptive._victron_ess_balance_auto_apply_suspend_reason(svc, 10.0), "auto_apply_suspended")
        self.assertEqual(adaptive._victron_ess_balance_auto_apply_observation_reason(svc, {}, 10.0), "observation_window_active")

        saver = MagicMock()
        svc._save_runtime_state = saver
        adaptive._victron_ess_balance_save_runtime_state(svc)
        saver.assert_called_once()
        adaptive._victron_ess_balance_save_runtime_state(SimpleNamespace(_save_runtime_state=None))

        self.assertFalse(adaptive._blend_recommended_setting(svc, "missing_attr", 1.0, 0.5))
        self.assertFalse(
            adaptive._blend_recommended_setting(
                svc,
                "auto_battery_discharge_balance_victron_bias_kp",
                0.2,
                0.5,
            )
        )
        self.assertEqual(
            adaptive._victron_ess_balance_recommended_activation_step(svc, metrics),
            "",
        )
        self.assertEqual(
            adaptive._victron_ess_balance_auto_apply_readiness(
                svc,
                {
                    "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_sample_count": 5,
                    "battery_discharge_balance_victron_bias_recommendation_profile_key": "r",
                    "battery_discharge_balance_victron_bias_learning_profile_key": "a",
                },
            ),
            "profile_mismatch",
        )
        self.assertEqual(
            adaptive._victron_ess_balance_auto_apply_readiness(
                svc,
                {
                    "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_sample_count": 1,
                    "battery_discharge_balance_victron_bias_recommendation_profile_key": "same",
                    "battery_discharge_balance_victron_bias_learning_profile_key": "same",
                },
            ),
            "insufficient_profile_samples",
        )
        self.assertEqual(
            adaptive._victron_ess_balance_auto_apply_readiness(
                svc,
                {
                    "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_sample_count": 5,
                    "battery_discharge_balance_victron_bias_recommendation_profile_key": "same",
                    "battery_discharge_balance_victron_bias_learning_profile_key": "same",
                },
            ),
            "",
        )

        metrics = {}
        adaptive._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 10.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "disabled")

        svc.auto_battery_discharge_balance_victron_bias_auto_apply_enabled = True
        metrics = {}
        with patch.object(adaptive, "_victron_ess_balance_auto_apply_blocker_reason", return_value="blocked"):
            adaptive._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 11.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "blocked")

        metrics = {
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_only",
        }
        self.assertEqual(
            adaptive._apply_victron_ess_balance_recommended_tuning_step(svc, metrics, 0.25),
            "auto_battery_discharge_balance_victron_bias_activation_mode",
        )

        with patch.object(components.recovery, "_victron_ess_balance_should_rollback_stable_tuning", return_value=True), patch.object(
            components.recovery, "_maybe_restore_victron_ess_balance_stable_tuning", return_value=False
        ):
            self.assertEqual(adaptive._victron_ess_balance_auto_apply_rollback_reason(svc, {}, 12.0), "")

        metrics = {}
        with patch.object(adaptive, "_apply_victron_ess_balance_recommended_tuning_step", return_value=""):
            self.assertFalse(adaptive._apply_victron_ess_balance_auto_apply_step(svc, metrics, 13.0))
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "already_at_recommendation")

