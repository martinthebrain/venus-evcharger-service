# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact recommendation policy contracts for Victron ESS balancing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from venus_evcharger.update.victron_ess_balance_recommendation_support import (
    VictronEssRecommendationPolicy as Recommendation,
)


def _observations(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "response_delay_seconds": 5.0,
        "estimated_gain": 0.8,
        "stability_score": 0.9,
        "settled_count": 2,
        "overshoot_count": 0,
    }
    values.update(overrides)
    return values


class VictronEssBalanceRecommendationSupportContractTests(unittest.TestCase):
    def test_telemetry_reason_gates_cover_all_threshold_boundaries(self) -> None:
        self.assertIs(Recommendation._victron_ess_balance_has_insufficient_telemetry(_observations(), 0.35), False)
        self.assertIs(Recommendation._victron_ess_balance_has_insufficient_telemetry(_observations(), 0.349), True)
        self.assertIs(
            Recommendation._victron_ess_balance_has_insufficient_telemetry(
                _observations(response_delay_seconds=None), 1.0
            ),
            True,
        )
        self.assertIs(
            Recommendation._victron_ess_balance_has_insufficient_telemetry(_observations(estimated_gain=None), 1.0),
            True,
        )
        self.assertIs(Recommendation._victron_ess_balance_has_overshoot_risk(_observations(),), False)
        self.assertIs(Recommendation._victron_ess_balance_has_overshoot_risk(_observations(overshoot_count=1)), True)
        self.assertIs(Recommendation._victron_ess_balance_has_overshoot_risk(_observations(stability_score=0.549)), True)
        self.assertIs(Recommendation._victron_ess_balance_has_overshoot_risk(_observations(stability_score=0.55)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_overshoot_risk(_observations(stability_score=None)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_slow_response(_observations(response_delay_seconds=8.0)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_slow_response(_observations(response_delay_seconds=8.01)), True)
        self.assertIs(Recommendation._victron_ess_balance_has_slow_response(_observations(estimated_gain=0.75)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_slow_response(_observations(estimated_gain=0.749)), True)

    def test_relaxation_requires_stability_clean_settling_and_bounds(self) -> None:
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations()), True)
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations(stability_score=None)), False)
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations(stability_score=0.799)), False)
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations(stability_score=0.8)), True)
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations(settled_count=1)), False)
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations(overshoot_count=1)), False)
        self.assertIs(
            Recommendation._victron_ess_balance_can_relax_conservatism(_observations(response_delay_seconds=5.01)),
            False,
        )
        self.assertIs(Recommendation._victron_ess_balance_can_relax_conservatism(_observations(estimated_gain=0.749)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_clean_settling(_observations(settled_count=2)), True)
        self.assertIs(Recommendation._victron_ess_balance_has_clean_settling(_observations(settled_count=1)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_clean_settling(_observations(settled_count=0)), False)
        self.assertIs(Recommendation._victron_ess_balance_has_clean_settling(_observations(settled_count=None)), False)
        self.assertIs(Recommendation._victron_ess_balance_observations_within_relaxed_bounds(_observations()), True)
        self.assertIs(
            Recommendation._victron_ess_balance_observations_within_relaxed_bounds(_observations(estimated_gain=0.75)),
            True,
        )

    def test_reason_presets_are_complete_and_unknown_is_empty(self) -> None:
        self.assertEqual(
            Recommendation._victron_ess_balance_reason_adjustment("overshoot_risk"),
            {"kp_factor": 0.8, "ki_factor": 0.5, "kd_floor": 0.02, "deadband_factor": 1.25, "deadband_default": 125.0, "max_abs_factor": 0.8, "max_abs_default": 350.0, "ramp_factor": 0.7, "ramp_default": 25.0},
        )
        self.assertEqual(
            Recommendation._victron_ess_balance_reason_adjustment("slow_response"),
            {"kp_factor": 0.9, "ki_factor": 0.6, "kd_floor": 0.01, "deadband_factor": 1.1, "deadband_default": 110.0, "max_abs_factor": 0.9, "max_abs_default": 425.0, "ramp_factor": 0.8, "ramp_default": 35.0},
        )
        self.assertEqual(
            Recommendation._victron_ess_balance_reason_adjustment("can_relax_conservatism"),
            {"kp_factor": 1.15, "ki_factor": 1.1, "kd_factor": 0.8, "kd_default": 0.0, "deadband_factor": 0.9, "deadband_default": 80.0, "max_abs_factor": 1.1, "max_abs_default": 550.0, "ramp_factor": 1.1, "ramp_default": 60.0},
        )
        self.assertEqual(Recommendation._victron_ess_balance_reason_adjustment("unknown"), {})

    def test_disabled_metrics_schema_is_exact(self) -> None:
        result = Recommendation._victron_ess_balance_disabled_recommendation_metrics()
        self.assertEqual(
            result,
            {
                "battery_discharge_balance_victron_bias_recommended_kp": None,
                "battery_discharge_balance_victron_bias_recommended_ki": None,
                "battery_discharge_balance_victron_bias_recommended_kd": None,
                "battery_discharge_balance_victron_bias_recommended_deadband_watts": None,
                "battery_discharge_balance_victron_bias_recommended_max_abs_watts": None,
                "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": None,
                "battery_discharge_balance_victron_bias_recommended_activation_mode": "",
                "battery_discharge_balance_victron_bias_recommendation_confidence": None,
                "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": None,
                "battery_discharge_balance_victron_bias_recommendation_response_variance_score": None,
                "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": None,
                "battery_discharge_balance_victron_bias_recommendation_reason": "disabled",
                "battery_discharge_balance_victron_bias_recommendation_profile_key": "",
                "battery_discharge_balance_victron_bias_recommendation_ini_snippet": "",
                "battery_discharge_balance_victron_bias_recommendation_hint": "",
            },
        )

    def test_current_tuning_normalizes_every_named_attribute(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp=1,
            auto_battery_discharge_balance_victron_bias_ki=2,
            auto_battery_discharge_balance_victron_bias_kd=3,
            auto_battery_discharge_balance_victron_bias_deadband_watts=4,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=5,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=6,
        )
        self.assertEqual(
            Recommendation._victron_ess_balance_current_tuning_values(svc),
            {"kp": 1.0, "ki": 2.0, "kd": 3.0, "deadband": 4.0, "max_abs": 5.0, "ramp": 6.0},
        )
        self.assertEqual(Recommendation._victron_ess_balance_non_negative_attr(SimpleNamespace(value=-2), "value"), 0.0)
        self.assertEqual(Recommendation._victron_ess_balance_non_negative_attr(SimpleNamespace(), "missing"), 0.0)

    def test_kd_and_scaled_value_helpers_cover_floor_factor_and_defaults(self) -> None:
        self.assertEqual(Recommendation._victron_ess_balance_adjusted_kd(0.01, {"kd_floor": 0.02}), 0.02)
        self.assertEqual(Recommendation._victron_ess_balance_adjusted_kd(0.03, {"kd_floor": 0.02}), 0.03)
        self.assertEqual(Recommendation._victron_ess_balance_adjusted_kd(0.5, {"kd_factor": 0.8}), 0.4)
        self.assertEqual(Recommendation._victron_ess_balance_adjusted_kd(0.5, {}), 0.5)
        self.assertEqual(Recommendation._victron_ess_balance_adjusted_kd(0.0, {"kd_default": 0.2}), 0.2)
        self.assertEqual(Recommendation._victron_ess_balance_adjusted_kd(0.0, {}), 0.0)
        preset = {"factor": 1.5, "default": 7.0}
        self.assertEqual(Recommendation._victron_ess_balance_scaled_or_default(2.0, preset, "factor", "default"), 3.0)
        self.assertEqual(Recommendation._victron_ess_balance_scaled_or_default(0.5, preset, "factor", "default"), 0.75)
        self.assertEqual(Recommendation._victron_ess_balance_scaled_or_default(0.0, preset, "factor", "default"), 7.0)

    def test_adjusted_tuning_applies_presets_defaults_and_non_negative_limits(self) -> None:
        current = {"kp": 1.0, "ki": 2.0, "kd": 0.01, "deadband": 100.0, "max_abs": 500.0, "ramp": 50.0}
        self.assertEqual(
            Recommendation._victron_ess_balance_adjusted_tuning(current, "overshoot_risk"),
            {"kp": 0.8, "ki": 1.0, "kd": 0.02, "deadband": 125.0, "max_abs": 400.0, "ramp": 35.0},
        )
        zero = {key: 0.0 for key in current}
        self.assertEqual(
            Recommendation._victron_ess_balance_adjusted_tuning(zero, "slow_response"),
            {"kp": 0.0, "ki": 0.0, "kd": 0.01, "deadband": 110.0, "max_abs": 425.0, "ramp": 35.0},
        )
        negative = {"kp": -1.0, "ki": -2.0, "kd": -3.0, "deadband": -4.0, "max_abs": -5.0, "ramp": -6.0}
        self.assertEqual(
            Recommendation._victron_ess_balance_adjusted_tuning(negative, "unknown"),
            {"kp": -1.0, "ki": -2.0, "kd": -3.0, "deadband": 0.0, "max_abs": 0.0, "ramp": 0.0},
        )

    def test_confidence_components_are_bounded_and_additive(self) -> None:
        confidence = Recommendation._victron_ess_balance_recommendation_confidence
        self.assertEqual(confidence(0, 0, None, None, None, None), 0.1)
        self.assertAlmostEqual(confidence(1, 1, 0.5, 0.5, 0.5, 0.5), 0.79)
        self.assertEqual(confidence(99, 99, 99.0, 99.0, 99.0, 99.0), 1.0)
        self.assertEqual(confidence(0, 0, -1.0, -1.0, -1.0, -1.0), 0.1)
        self.assertEqual(confidence(5, 0, None, None, None, None), 0.65)
        self.assertAlmostEqual(confidence(0, 0, 2.0, None, None, None), 0.45)
        self.assertAlmostEqual(confidence(0, 0, None, 2.0, None, None), 0.3)
        self.assertEqual(confidence(0, 0, None, None, 2.0, None), 0.25)
        self.assertAlmostEqual(confidence(0, 0, None, None, None, 2.0), 0.3)

    def test_ini_snippet_and_hints_are_exact_public_guidance(self) -> None:
        self.assertEqual(
            Recommendation._victron_ess_balance_recommendation_ini_snippet(1, 0.5, 0.02, 100, 500, 25, "export_only"),
            "AutoBatteryDischargeBalanceVictronBiasKp=1\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.5\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0.02\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=100\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=500\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=25\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_only",
        )
        self.assertTrue(Recommendation._victron_ess_balance_recommendation_ini_snippet(0, 0, 0, 0, 0, 0, "").endswith("=always"))
        expected = {
            "can_relax_conservatism": "Telemetry looks stable; you can cautiously relax the current Victron bias tuning (confidence 0.62).",
            "overshoot_risk": "Telemetry shows overshoot risk; use more conservative Victron bias tuning (confidence 0.62).",
            "slow_response": "Telemetry suggests a slow site response; reduce aggressiveness and ramp more gently (confidence 0.62).",
            "telemetry_nominal": "Telemetry looks broadly nominal; keep tuning close to the current values (confidence 0.62).",
            "other": "Telemetry is still too thin for a strong tuning recommendation (confidence 0.62).",
        }
        for reason, hint in expected.items():
            self.assertEqual(Recommendation._victron_ess_balance_recommendation_hint(reason, 0.625), hint)


if __name__ == "__main__":
    unittest.main()
