# SPDX-License-Identifier: GPL-3.0-or-later
"""Orchestration contracts for Victron ESS balance recommendations."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.update.victron_ess_balance_recommendation import (
    VictronEssRecommendationEngine,
)
from venus_evcharger.update.victron_ess_balance_recommendation_support import (
    VictronEssRecommendationPolicy,
)
from tests.support.victron_ess_balance import build_victron_ess_components


class VictronEssBalanceRecommendationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        components = build_victron_ess_components()
        self.recommendation: VictronEssRecommendationEngine = components.recommendation
        self.profiles = components.profiles
        self.policy = components.policy
        self.recovery = components.recovery
        self.safety = components.safety
        self.sources = components.sources

    def test_source_uses_trimmed_active_profile_and_positive_sample_count(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_active_learning_profile_key=" profile ")
        profile = {"sample_count": 1}
        with (
            patch.object(self.profiles, "_victron_ess_balance_learning_profile_state", return_value=profile) as state,
            patch.object(self.profiles, "_victron_ess_balance_profile_sample_count", return_value=1) as count,
        ):
            self.assertEqual(self.recommendation._victron_ess_balance_recommendation_source(svc), ("profile", profile, True))
        state.assert_called_once_with(svc, "profile")
        count.assert_called_once_with(profile)
        with patch.object(self.profiles, "_victron_ess_balance_profile_sample_count", return_value=0):
            self.assertEqual(self.recommendation._victron_ess_balance_recommendation_source(SimpleNamespace()), ("", {}, False))

    def test_observation_value_and_count_select_profile_or_runtime(self) -> None:
        svc = SimpleNamespace(runtime=7)
        profile = {"profile": 5}
        self.assertEqual(self.recommendation._victron_ess_balance_observation_value(svc, profile, True, "profile", "runtime"), 5)
        self.assertEqual(self.recommendation._victron_ess_balance_observation_value(svc, profile, False, "profile", "runtime"), 7)
        self.assertIsNone(self.recommendation._victron_ess_balance_observation_value(SimpleNamespace(), {}, False, "x", "missing"))
        self.assertEqual(self.recommendation._victron_ess_balance_observation_count(svc, profile, True, "profile", "runtime"), 5)
        self.assertEqual(
            self.recommendation._victron_ess_balance_observation_count(
                SimpleNamespace(runtime=-3), {}, False, "profile", "runtime"
            ),
            0,
        )
        self.assertEqual(
            self.recommendation._victron_ess_balance_observation_count(
                SimpleNamespace(), {}, False, "profile", "runtime"
            ),
            0,
        )

    def test_observations_map_every_profile_and_runtime_field(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_response_delay_seconds=1,
            _victron_ess_balance_telemetry_estimated_gain=2,
            _victron_ess_balance_telemetry_stability_score=3,
            _victron_ess_balance_telemetry_overshoot_count=4,
            _victron_ess_balance_telemetry_settled_count=5,
            _victron_ess_balance_telemetry_delay_samples=6,
            _victron_ess_balance_telemetry_gain_samples=7,
        )
        self.assertEqual(
            self.recommendation._victron_ess_balance_recommendation_observations(svc, {}, False),
            {
                "response_delay_seconds": 1.0,
                "estimated_gain": 2.0,
                "stability_score": 3.0,
                "overshoot_count": 4,
                "settled_count": 5,
                "delay_samples": 6,
                "gain_samples": 7,
                "regime_consistency_score": None,
                "response_variance_score": None,
                "reproducibility_score": None,
            },
        )
        profile = {
            "response_delay_seconds": 11,
            "estimated_gain": 12,
            "stability_score": 13,
            "overshoot_count": 14,
            "settled_count": 15,
            "delay_samples": 16,
            "gain_samples": 17,
            "regime_consistency_score": 0.8,
            "response_variance_score": 0.7,
            "reproducibility_score": 0.6,
        }
        result = self.recommendation._victron_ess_balance_recommendation_observations(svc, profile, True)
        self.assertEqual(result["response_delay_seconds"], 11.0)
        self.assertEqual(result["gain_samples"], 17)
        self.assertEqual(result["regime_consistency_score"], 0.8)
        self.assertEqual(result["response_variance_score"], 0.7)
        self.assertEqual(result["reproducibility_score"], 0.6)

    def test_reason_priority_is_deterministic(self) -> None:
        observations: dict[str, object] = {}
        checks = (
            ("_victron_ess_balance_has_insufficient_telemetry", "insufficient_telemetry"),
            ("_victron_ess_balance_has_overshoot_risk", "overshoot_risk"),
            ("_victron_ess_balance_has_slow_response", "slow_response"),
            ("_victron_ess_balance_can_relax_conservatism", "can_relax_conservatism"),
        )
        for index, (winner, expected) in enumerate(checks):
            values = {name: position == index for position, (name, _reason) in enumerate(checks)}
            with (
                patch.object(self.policy, checks[0][0], return_value=values[checks[0][0]]) as insufficient,
                patch.object(self.policy, checks[1][0], return_value=values[checks[1][0]]) as overshoot,
                patch.object(self.policy, checks[2][0], return_value=values[checks[2][0]]) as slow,
                patch.object(self.policy, checks[3][0], return_value=values[checks[3][0]]) as relax,
            ):
                self.assertEqual(self.recommendation._victron_ess_balance_recommendation_reason(observations, 0.5), expected)
            insufficient.assert_called_once_with(observations, 0.5)
            if index > 0:
                overshoot.assert_called_once_with(observations)
            else:
                overshoot.assert_not_called()
            if index > 1:
                slow.assert_called_once_with(observations)
            else:
                slow.assert_not_called()
            if index > 2:
                relax.assert_called_once_with(observations)
            else:
                relax.assert_not_called()
        with (
            patch.object(self.policy, checks[0][0], return_value=False) as insufficient,
            patch.object(self.policy, checks[1][0], return_value=False) as overshoot,
            patch.object(self.policy, checks[2][0], return_value=False) as slow,
            patch.object(self.policy, checks[3][0], return_value=False) as relax,
        ):
            self.assertEqual(self.recommendation._victron_ess_balance_recommendation_reason(observations, 0.5), "telemetry_nominal")
        insufficient.assert_called_once_with(observations, 0.5)
        overshoot.assert_called_once_with(observations)
        slow.assert_called_once_with(observations)
        relax.assert_called_once_with(observations)

    def test_adjusted_tuning_delegates_to_support_contract(self) -> None:
        current = {"kp": 1.0}
        with patch.object(
            VictronEssRecommendationPolicy,
            "_victron_ess_balance_adjusted_tuning",
            return_value={"kp": 2.0},
        ) as adjusted:
            self.assertEqual(self.recommendation._victron_ess_balance_adjusted_tuning(current, "reason"), {"kp": 2.0})
        adjusted.assert_called_once_with(current, "reason")

    def test_payload_preserves_rounding_metadata_and_generated_guidance(self) -> None:
        svc = object()
        profile = {"reserve_phase": "above_reserve_band"}
        observations = {"regime_consistency_score": 0.8, "response_variance_score": 0.7, "reproducibility_score": 0.6}
        adjusted = {"kp": 1.23456, "ki": 2.34567, "kd": 3.45678, "deadband": 4.56789, "max_abs": 5.67891, "ramp": 6.78912}
        with (
            patch.object(self.policy, "_victron_ess_balance_current_tuning_values", return_value={"kp": 1.0}) as current,
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_reason", return_value="reason") as reason,
            patch.object(self.recommendation, "_victron_ess_balance_adjusted_tuning", return_value=adjusted) as tune,
            patch.object(self.recommendation, "_victron_ess_balance_recommended_activation_mode", return_value="export_only") as mode,
            patch.object(self.policy, "_victron_ess_balance_recommendation_ini_snippet", return_value="ini") as ini,
            patch.object(self.policy, "_victron_ess_balance_recommendation_hint", return_value="hint") as hint,
        ):
            result = self.recommendation._victron_ess_balance_recommendation_payload(svc, "profile", True, profile, observations, 0.87654)
        self.assertEqual(result, {
            "battery_discharge_balance_victron_bias_recommended_kp": 1.2346,
            "battery_discharge_balance_victron_bias_recommended_ki": 2.3457,
            "battery_discharge_balance_victron_bias_recommended_kd": 3.4568,
            "battery_discharge_balance_victron_bias_recommended_deadband_watts": 4.5679,
            "battery_discharge_balance_victron_bias_recommended_max_abs_watts": 5.6789,
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": 6.7891,
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_only",
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.8765,
            "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": 0.8,
            "battery_discharge_balance_victron_bias_recommendation_response_variance_score": 0.7,
            "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": 0.6,
            "battery_discharge_balance_victron_bias_recommendation_reason": "reason",
            "battery_discharge_balance_victron_bias_recommendation_profile_key": "profile",
            "battery_discharge_balance_victron_bias_recommendation_ini_snippet": "ini",
            "battery_discharge_balance_victron_bias_recommendation_hint": "hint",
        })
        current.assert_called_once_with(svc)
        reason.assert_called_once_with(observations, 0.87654)
        tune.assert_called_once_with({"kp": 1.0}, "reason")
        mode.assert_called_once_with(profile, svc)
        ini.assert_called_once_with(1.23456, 2.34567, 3.45678, 4.56789, 5.67891, 6.78912, "export_only")
        hint.assert_called_once_with("reason", 0.87654)

        with (
            patch.object(self.policy, "_victron_ess_balance_current_tuning_values", return_value={"kp": 1.0}),
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_reason", return_value="reason"),
            patch.object(self.recommendation, "_victron_ess_balance_adjusted_tuning", return_value=adjusted),
            patch.object(self.recommendation, "_victron_ess_balance_recommended_activation_mode", return_value="always") as mode,
            patch.object(self.policy, "_victron_ess_balance_recommendation_ini_snippet", return_value="ini"),
            patch.object(self.policy, "_victron_ess_balance_recommendation_hint", return_value="hint"),
        ):
            without_profile = self.recommendation._victron_ess_balance_recommendation_payload(
                svc, "ignored-profile", False, profile, observations, 0.5
            )
        self.assertEqual(without_profile["battery_discharge_balance_victron_bias_recommendation_profile_key"], "")
        mode.assert_called_once_with({}, svc)

    def test_telemetry_population_and_top_level_merge_are_exact(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_last_observed_at=10,
            _victron_ess_balance_active_learning_profile_key=" profile ",
            _victron_ess_balance_telemetry_response_delay_seconds=1,
            _victron_ess_balance_telemetry_estimated_gain=2,
            _victron_ess_balance_telemetry_overshoot_active=True,
            _victron_ess_balance_telemetry_overshoot_count=3,
            _victron_ess_balance_overshoot_cooldown_reason="reason",
            _victron_ess_balance_overshoot_cooldown_until=20,
            _victron_ess_balance_telemetry_settling_active=True,
            _victron_ess_balance_telemetry_settled_count=4,
            _victron_ess_balance_telemetry_stability_score=0.8,
        )
        metrics: dict[str, object] = {}
        with (
            patch.object(self.recovery, "_victron_ess_balance_overshoot_cooldown_active", return_value=True) as cooldown,
            patch.object(self.safety, "_populate_victron_ess_balance_runtime_safety_metrics") as safety,
        ):
            self.recommendation._populate_victron_ess_balance_recommendation_telemetry_metrics(svc, metrics, 10.0)
        self.assertEqual(metrics, {
            "battery_discharge_balance_victron_bias_response_delay_seconds": 1.0,
            "battery_discharge_balance_victron_bias_estimated_gain": 2.0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": 20.0,
            "battery_discharge_balance_victron_bias_stability_score": 0.8,
            "battery_discharge_balance_victron_bias_overshoot_active": 1,
            "battery_discharge_balance_victron_bias_settling_active": 1,
            "battery_discharge_balance_victron_bias_overshoot_count": 3,
            "battery_discharge_balance_victron_bias_settled_count": 4,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 1,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "reason",
        })
        cooldown.assert_called_once_with(svc, 10.0)
        safety.assert_called_once_with(svc, 10.0, metrics)

        empty_metrics: dict[str, object] = {}
        with (
            patch.object(self.recovery, "_victron_ess_balance_overshoot_cooldown_active", return_value=False),
            patch.object(self.safety, "_populate_victron_ess_balance_runtime_safety_metrics"),
        ):
            self.recommendation._populate_victron_ess_balance_recommendation_telemetry_metrics(
                SimpleNamespace(), empty_metrics, 0.0
            )
        self.assertEqual(empty_metrics, {
            "battery_discharge_balance_victron_bias_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_estimated_gain": None,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": None,
            "battery_discharge_balance_victron_bias_stability_score": None,
            "battery_discharge_balance_victron_bias_overshoot_active": 0,
            "battery_discharge_balance_victron_bias_settling_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_settled_count": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "",
        })

        metrics = {}
        with (
            patch.object(self.recommendation, "_populate_victron_ess_balance_recommendation_telemetry_metrics") as telemetry,
            patch.object(self.profiles, "_merge_victron_ess_balance_learning_profile_metrics") as merge,
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_metrics", return_value={"recommendation": 1}) as recommendation,
        ):
            self.recommendation._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
        telemetry.assert_called_once_with(svc, metrics, 10.0)
        merge.assert_called_once_with(svc, metrics, "profile")
        recommendation.assert_called_once_with(svc)
        self.assertEqual(metrics, {"recommendation": 1})

        metrics = {}
        missing = SimpleNamespace()
        with (
            patch.object(self.recommendation, "_populate_victron_ess_balance_recommendation_telemetry_metrics") as telemetry,
            patch.object(self.profiles, "_merge_victron_ess_balance_learning_profile_metrics") as merge,
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_metrics", return_value={}),
        ):
            self.recommendation._populate_victron_ess_balance_telemetry_metrics(missing, metrics)
        telemetry.assert_called_once_with(missing, metrics, 0.0)
        merge.assert_called_once_with(missing, metrics, "")

    def test_metrics_disabled_and_enabled_orchestration(self) -> None:
        disabled = SimpleNamespace(auto_battery_discharge_balance_victron_bias_enabled=False)
        with patch.object(self.policy, "_victron_ess_balance_disabled_recommendation_metrics", return_value={"disabled": 1}) as result:
            self.assertEqual(self.recommendation._victron_ess_balance_recommendation_metrics(disabled), {"disabled": 1})
        result.assert_called_once_with()
        with patch.object(self.policy, "_victron_ess_balance_disabled_recommendation_metrics", return_value={"disabled": 2}):
            self.assertEqual(self.recommendation._victron_ess_balance_recommendation_metrics(SimpleNamespace()), {"disabled": 2})

        svc = SimpleNamespace(auto_battery_discharge_balance_victron_bias_enabled=True)
        observations = {"delay_samples": 1, "gain_samples": 2, "stability_score": 0.8, "regime_consistency_score": 0.7, "response_variance_score": 0.6, "reproducibility_score": 0.5}
        with (
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_source", return_value=("key", {"p": 1}, True)) as source,
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_observations", return_value=observations) as observed,
            patch.object(self.policy, "_victron_ess_balance_recommendation_confidence", return_value=0.9) as confidence,
            patch.object(self.recommendation, "_victron_ess_balance_recommendation_payload", return_value={"ok": 1}) as payload,
        ):
            self.assertEqual(self.recommendation._victron_ess_balance_recommendation_metrics(svc), {"ok": 1})
        source.assert_called_once_with(svc)
        observed.assert_called_once_with(svc, {"p": 1}, True)
        confidence.assert_called_once_with(1, 2, 0.8, 0.7, 0.6, 0.5)
        payload.assert_called_once_with(svc, "key", True, {"p": 1}, observations, 0.9)

    def test_recommended_activation_modes_cover_export_reserve_and_fallback(self) -> None:
        svc = object()
        with patch.object(self.sources, "_victron_ess_balance_activation_mode", return_value="always") as current:
            self.assertEqual(self.recommendation._victron_ess_balance_recommended_activation_mode({}, svc), "always")
            self.assertEqual(
                self.recommendation._victron_ess_balance_recommended_activation_mode(
                    {"reserve_phase": "above_reserve_band"}, svc
                ),
                "above_reserve_band",
            )
            self.assertEqual(
                self.recommendation._victron_ess_balance_recommended_activation_mode(
                    {"site_regime": "export", "reserve_phase": "above_reserve_band"}, svc
                ),
                "export_and_above_reserve_band",
            )
            self.assertEqual(
                self.recommendation._victron_ess_balance_recommended_activation_mode(
                    {"site_regime": "export", "reserve_phase": "reserve_band"}, svc
                ),
                "export_only",
            )
        self.assertEqual(self.recommendation._victron_ess_balance_export_activation_mode("above_reserve_band"), "export_and_above_reserve_band")
        self.assertEqual(self.recommendation._victron_ess_balance_export_activation_mode("reserve_band"), "export_only")
        current.assert_called_once_with(svc)

        with patch.object(self.sources, "_victron_ess_balance_activation_mode", return_value="always"):
            self.assertEqual(
                self.recommendation._victron_ess_balance_recommended_activation_mode(
                    {"site_regime": " export ", "reserve_phase": " above_reserve_band "}, svc
                ),
                "export_and_above_reserve_band",
            )


if __name__ == "__main__":
    unittest.main()
