# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for Victron ESS balance-bias recommendations."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_safety import _UpdateCycleVictronEssBalanceSafety


class _UpdateCycleVictronEssBalanceRecommendationSupport(_UpdateCycleVictronEssBalanceSafety):
    @staticmethod
    def _victron_ess_balance_has_insufficient_telemetry(
        observations: dict[str, Any],
        confidence: float,
    ) -> bool:
        return (
            observations["response_delay_seconds"] is None
            or observations["estimated_gain"] is None
            or confidence < 0.35
        )

    @staticmethod
    def _victron_ess_balance_has_overshoot_risk(observations: dict[str, Any]) -> bool:
        stability_score = observations["stability_score"]
        return int(observations["overshoot_count"] or 0) > 0 or (
            stability_score is not None and stability_score < 0.55
        )

    @staticmethod
    def _victron_ess_balance_has_slow_response(observations: dict[str, Any]) -> bool:
        return (
            float(observations["response_delay_seconds"]) > 8.0
            or float(observations["estimated_gain"]) < 0.75
        )

    @staticmethod
    def _victron_ess_balance_can_relax_conservatism(observations: dict[str, Any]) -> bool:
        stability_score = observations["stability_score"]
        if stability_score is None or stability_score < 0.8:
            return False
        if not _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_has_clean_settling(observations):
            return False
        return _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_observations_within_relaxed_bounds(
            observations
        )

    @staticmethod
    def _victron_ess_balance_has_clean_settling(observations: dict[str, Any]) -> bool:
        return (
            int(observations["settled_count"] or 0) >= 2
            and int(observations["overshoot_count"] or 0) == 0
        )

    @staticmethod
    def _victron_ess_balance_observations_within_relaxed_bounds(observations: dict[str, Any]) -> bool:
        return (
            float(observations["response_delay_seconds"]) <= 5.0
            and float(observations["estimated_gain"]) >= 0.75
        )

    @staticmethod
    def _victron_ess_balance_reason_adjustment(reason: str) -> dict[str, float]:
        presets: dict[str, dict[str, float]] = {
            "overshoot_risk": {
                "kp_factor": 0.8,
                "ki_factor": 0.5,
                "kd_floor": 0.02,
                "deadband_factor": 1.25,
                "deadband_default": 125.0,
                "max_abs_factor": 0.8,
                "max_abs_default": 350.0,
                "ramp_factor": 0.7,
                "ramp_default": 25.0,
            },
            "slow_response": {
                "kp_factor": 0.9,
                "ki_factor": 0.6,
                "kd_floor": 0.01,
                "deadband_factor": 1.1,
                "deadband_default": 110.0,
                "max_abs_factor": 0.9,
                "max_abs_default": 425.0,
                "ramp_factor": 0.8,
                "ramp_default": 35.0,
            },
            "can_relax_conservatism": {
                "kp_factor": 1.15,
                "ki_factor": 1.1,
                "kd_factor": 0.8,
                "kd_default": 0.0,
                "deadband_factor": 0.9,
                "deadband_default": 80.0,
                "max_abs_factor": 1.1,
                "max_abs_default": 550.0,
                "ramp_factor": 1.1,
                "ramp_default": 60.0,
            },
        }
        return presets.get(reason, {})

    @staticmethod
    def _victron_ess_balance_disabled_recommendation_metrics() -> dict[str, Any]:
        return {
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
        }

    @staticmethod
    def _victron_ess_balance_current_tuning_values(svc: Any) -> dict[str, float]:
        return {
            "kp": _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_non_negative_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_kp",
            ),
            "ki": _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_non_negative_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_ki",
            ),
            "kd": _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_non_negative_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_kd",
            ),
            "deadband": _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_non_negative_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_deadband_watts",
            ),
            "max_abs": _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_non_negative_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_max_abs_watts",
            ),
            "ramp": _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_non_negative_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            ),
        }

    @staticmethod
    def _victron_ess_balance_non_negative_attr(svc: Any, attr_name: str) -> float:
        return max(0.0, float(getattr(svc, attr_name, 0.0) or 0.0))

    @staticmethod
    def _victron_ess_balance_adjusted_tuning(current: dict[str, float], reason: str) -> dict[str, float]:
        adjusted = dict(current)
        preset = _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_reason_adjustment(reason)
        if preset:
            adjusted["kp"] = current["kp"] * float(preset["kp_factor"])
            adjusted["ki"] = current["ki"] * float(preset["ki_factor"])
            adjusted["kd"] = _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_adjusted_kd(
                current["kd"],
                preset,
            )
            adjusted["deadband"] = _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_scaled_or_default(
                current["deadband"], preset, "deadband_factor", "deadband_default"
            )
            adjusted["max_abs"] = _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_scaled_or_default(
                current["max_abs"], preset, "max_abs_factor", "max_abs_default"
            )
            adjusted["ramp"] = _UpdateCycleVictronEssBalanceRecommendationSupport._victron_ess_balance_scaled_or_default(
                current["ramp"], preset, "ramp_factor", "ramp_default"
            )
        adjusted["deadband"] = max(0.0, float(adjusted["deadband"]))
        adjusted["max_abs"] = max(0.0, float(adjusted["max_abs"]))
        adjusted["ramp"] = max(0.0, float(adjusted["ramp"]))
        return adjusted

    @staticmethod
    def _victron_ess_balance_adjusted_kd(current_kd: float, preset: dict[str, float]) -> float:
        if "kd_floor" in preset:
            return max(current_kd, float(preset["kd_floor"]))
        if current_kd > 0.0:
            return current_kd * float(preset.get("kd_factor", 1.0))
        return float(preset.get("kd_default", 0.0))

    @staticmethod
    def _victron_ess_balance_scaled_or_default(
        current_value: float,
        preset: dict[str, float],
        factor_key: str,
        default_key: str,
    ) -> float:
        if current_value > 0.0:
            return current_value * float(preset[factor_key])
        return float(preset[default_key])

    @staticmethod
    def _victron_ess_balance_recommendation_confidence(
        delay_samples: int,
        gain_samples: int,
        stability_score: float | None,
        regime_consistency_score: float | None,
        response_variance_score: float | None,
        reproducibility_score: float | None,
    ) -> float:
        sample_component = min(0.55, (0.12 * float(delay_samples)) + (0.12 * float(gain_samples)))
        stability_component = 0.0 if stability_score is None else min(0.35, 0.35 * max(0.0, float(stability_score)))
        consistency_component = (
            0.0
            if regime_consistency_score is None
            else min(0.2, 0.2 * max(0.0, float(regime_consistency_score)))
        )
        variance_component = (
            0.0
            if response_variance_score is None
            else min(0.15, 0.15 * max(0.0, float(response_variance_score)))
        )
        reproducibility_component = (
            0.0
            if reproducibility_score is None
            else min(0.2, 0.2 * max(0.0, float(reproducibility_score)))
        )
        return max(
            0.1,
            min(
                1.0,
                0.1
                + sample_component
                + stability_component
                + consistency_component
                + variance_component
                + reproducibility_component,
            ),
        )

    @staticmethod
    def _victron_ess_balance_recommendation_ini_snippet(
        recommended_kp: float,
        recommended_ki: float,
        recommended_kd: float,
        recommended_deadband: float,
        recommended_max_abs: float,
        recommended_ramp: float,
        recommended_activation_mode: str,
    ) -> str:
        return "\n".join(
            (
                "AutoBatteryDischargeBalanceVictronBiasKp=" f"{float(recommended_kp):g}",
                "AutoBatteryDischargeBalanceVictronBiasKi=" f"{float(recommended_ki):g}",
                "AutoBatteryDischargeBalanceVictronBiasKd=" f"{float(recommended_kd):g}",
                "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=" f"{float(recommended_deadband):g}",
                "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=" f"{float(recommended_max_abs):g}",
                "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=" f"{float(recommended_ramp):g}",
                "AutoBatteryDischargeBalanceVictronBiasActivationMode="
                f"{str(recommended_activation_mode or 'always')}",
            )
        )

    @staticmethod
    def _victron_ess_balance_recommendation_hint(reason: str, confidence: float) -> str:
        confidence_text = f"{float(confidence):.2f}"
        if reason == "can_relax_conservatism":
            return (
                "Telemetry looks stable; you can cautiously relax the current "
                f"Victron bias tuning (confidence {confidence_text})."
            )
        if reason == "overshoot_risk":
            return (
                "Telemetry shows overshoot risk; use more conservative Victron "
                f"bias tuning (confidence {confidence_text})."
            )
        if reason == "slow_response":
            return (
                "Telemetry suggests a slow site response; reduce aggressiveness "
                f"and ramp more gently (confidence {confidence_text})."
            )
        if reason == "telemetry_nominal":
            return (
                "Telemetry looks broadly nominal; keep tuning close to the "
                f"current values (confidence {confidence_text})."
            )
        return (
            "Telemetry is still too thin for a strong tuning recommendation "
            f"(confidence {confidence_text})."
        )
