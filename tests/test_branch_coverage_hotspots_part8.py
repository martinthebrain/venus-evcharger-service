# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronRecommendationCasesPart1:
    def test_victron_recommendation_helper_branches(self) -> None:
        components = _components()
        policy = components.policy
        recommendation = components.recommendation
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=False,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.04,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=300.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=40.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            _victron_ess_balance_active_learning_profile_key="",
        )
        self.assertFalse(policy._victron_ess_balance_can_relax_conservatism({"stability_score": None}))
        self.assertEqual(
            recommendation._victron_ess_balance_recommendation_reason(
                {
                    "response_delay_seconds": 9.0,
                    "estimated_gain": 1.0,
                    "stability_score": 0.9,
                    "overshoot_count": 0,
                    "settled_count": 2,
                },
                0.9,
            ),
            "slow_response",
        )
        self.assertEqual(
            policy._victron_ess_balance_adjusted_kd(0.04, {"kd_factor": 0.5}),
            0.02,
        )
        self.assertIn("slow site response", policy._victron_ess_balance_recommendation_hint("slow_response", 0.6))
        self.assertEqual(
            recommendation._victron_ess_balance_recommended_activation_mode(
                {"site_regime": "import", "reserve_phase": "above_reserve_band"},
                svc,
            ),
            "above_reserve_band",
        )
        self.assertEqual(recommendation._victron_ess_balance_export_activation_mode("reserve_band"), "export_only")
        disabled = recommendation._victron_ess_balance_recommendation_metrics(svc)
        self.assertEqual(disabled["battery_discharge_balance_victron_bias_recommendation_reason"], "disabled")


