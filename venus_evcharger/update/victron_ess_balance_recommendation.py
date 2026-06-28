# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias recommendation helpers."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_recommendation_support import (
    _UpdateCycleVictronEssBalanceRecommendationSupportMixin,
)

class _UpdateCycleVictronEssBalanceRecommendationMixin(_UpdateCycleVictronEssBalanceRecommendationSupportMixin):

    def _victron_ess_balance_recommendation_source(
        self,
        svc: Any,
    ) -> tuple[str, dict[str, Any], bool]:
        active_profile_key = str(getattr(svc, "_victron_ess_balance_active_learning_profile_key", "") or "").strip()
        active_profile = self._victron_ess_balance_learning_profile_state(svc, active_profile_key)
        use_profile = self._victron_ess_balance_profile_sample_count(active_profile) > 0
        return active_profile_key, active_profile, use_profile

    def _victron_ess_balance_recommendation_observations(
        self,
        svc: Any,
        active_profile: dict[str, Any],
        use_profile: bool,
    ) -> dict[str, Any]:
        return {
            "response_delay_seconds": self._optional_float(
                self._victron_ess_balance_observation_value(
                    svc,
                    active_profile,
                    use_profile,
                    "response_delay_seconds",
                    "_victron_ess_balance_telemetry_response_delay_seconds",
                )
            ),
            "estimated_gain": self._optional_float(
                self._victron_ess_balance_observation_value(
                    svc,
                    active_profile,
                    use_profile,
                    "estimated_gain",
                    "_victron_ess_balance_telemetry_estimated_gain",
                )
            ),
            "stability_score": self._optional_float(
                self._victron_ess_balance_observation_value(
                    svc,
                    active_profile,
                    use_profile,
                    "stability_score",
                    "_victron_ess_balance_telemetry_stability_score",
                )
            ),
            "overshoot_count": self._victron_ess_balance_observation_count(
                svc,
                active_profile,
                use_profile,
                "overshoot_count",
                "_victron_ess_balance_telemetry_overshoot_count",
            ),
            "settled_count": self._victron_ess_balance_observation_count(
                svc,
                active_profile,
                use_profile,
                "settled_count",
                "_victron_ess_balance_telemetry_settled_count",
            ),
            "delay_samples": self._victron_ess_balance_observation_count(
                svc,
                active_profile,
                use_profile,
                "delay_samples",
                "_victron_ess_balance_telemetry_delay_samples",
            ),
            "gain_samples": self._victron_ess_balance_observation_count(
                svc,
                active_profile,
                use_profile,
                "gain_samples",
                "_victron_ess_balance_telemetry_gain_samples",
            ),
            "regime_consistency_score": self._optional_float(
                active_profile.get("regime_consistency_score") if use_profile else None
            ),
            "response_variance_score": self._optional_float(
                active_profile.get("response_variance_score") if use_profile else None
            ),
            "reproducibility_score": self._optional_float(
                active_profile.get("reproducibility_score") if use_profile else None
            ),
        }

    @staticmethod
    def _victron_ess_balance_observation_value(
        svc: Any,
        active_profile: dict[str, Any],
        use_profile: bool,
        profile_key: str,
        service_attr: str,
    ) -> Any:
        return active_profile.get(profile_key) if use_profile else getattr(svc, service_attr, None)

    @classmethod
    def _victron_ess_balance_observation_count(
        cls,
        svc: Any,
        active_profile: dict[str, Any],
        use_profile: bool,
        profile_key: str,
        service_attr: str,
    ) -> int:
        return max(
            0,
            int(cls._victron_ess_balance_observation_value(svc, active_profile, use_profile, profile_key, service_attr) or 0),
        )

    def _victron_ess_balance_recommendation_reason(
        self,
        observations: dict[str, Any],
        confidence: float,
    ) -> str:
        if self._victron_ess_balance_has_insufficient_telemetry(observations, confidence):
            return "insufficient_telemetry"
        if self._victron_ess_balance_has_overshoot_risk(observations):
            return "overshoot_risk"
        if self._victron_ess_balance_has_slow_response(observations):
            return "slow_response"
        if self._victron_ess_balance_can_relax_conservatism(observations):
            return "can_relax_conservatism"
        return "telemetry_nominal"

    @staticmethod
    def _victron_ess_balance_adjusted_tuning(current: dict[str, float], reason: str) -> dict[str, float]:
        return _UpdateCycleVictronEssBalanceRecommendationSupportMixin._victron_ess_balance_adjusted_tuning(
            current,
            reason,
        )

    def _victron_ess_balance_recommendation_payload(
        self,
        svc: Any,
        active_profile_key: str,
        use_profile: bool,
        active_profile: dict[str, Any],
        observations: dict[str, Any],
        confidence: float,
    ) -> dict[str, Any]:
        current = self._victron_ess_balance_current_tuning_values(svc)
        reason = self._victron_ess_balance_recommendation_reason(observations, confidence)
        adjusted = self._victron_ess_balance_adjusted_tuning(current, reason)
        activation_mode = self._victron_ess_balance_recommended_activation_mode(active_profile if use_profile else {}, svc)
        ini_snippet = self._victron_ess_balance_recommendation_ini_snippet(
            adjusted["kp"],
            adjusted["ki"],
            adjusted["kd"],
            adjusted["deadband"],
            adjusted["max_abs"],
            adjusted["ramp"],
            activation_mode,
        )
        return {
            "battery_discharge_balance_victron_bias_recommended_kp": round(float(adjusted["kp"]), 4),
            "battery_discharge_balance_victron_bias_recommended_ki": round(float(adjusted["ki"]), 4),
            "battery_discharge_balance_victron_bias_recommended_kd": round(float(adjusted["kd"]), 4),
            "battery_discharge_balance_victron_bias_recommended_deadband_watts": round(
                float(adjusted["deadband"]), 4
            ),
            "battery_discharge_balance_victron_bias_recommended_max_abs_watts": round(float(adjusted["max_abs"]), 4),
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": round(
                float(adjusted["ramp"]), 4
            ),
            "battery_discharge_balance_victron_bias_recommended_activation_mode": activation_mode,
            "battery_discharge_balance_victron_bias_recommendation_confidence": round(float(confidence), 4),
            "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": observations[
                "regime_consistency_score"
            ],
            "battery_discharge_balance_victron_bias_recommendation_response_variance_score": observations[
                "response_variance_score"
            ],
            "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": observations[
                "reproducibility_score"
            ],
            "battery_discharge_balance_victron_bias_recommendation_reason": reason,
            "battery_discharge_balance_victron_bias_recommendation_profile_key": active_profile_key if use_profile else "",
            "battery_discharge_balance_victron_bias_recommendation_ini_snippet": ini_snippet,
            "battery_discharge_balance_victron_bias_recommendation_hint": self._victron_ess_balance_recommendation_hint(
                reason,
                confidence,
            ),
        }

    def _populate_victron_ess_balance_telemetry_metrics(self, svc: Any, metrics: dict[str, Any]) -> None:
        observed_at = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_observed_at", None)) or 0.0
        self._populate_victron_ess_balance_recommendation_telemetry_metrics(svc, metrics, observed_at)
        active_profile_key = str(getattr(svc, "_victron_ess_balance_active_learning_profile_key", "") or "").strip()
        self._merge_victron_ess_balance_learning_profile_metrics(svc, metrics, active_profile_key)
        metrics.update(self._victron_ess_balance_recommendation_metrics(svc))

    def _populate_victron_ess_balance_recommendation_telemetry_metrics(
        self,
        svc: Any,
        metrics: dict[str, Any],
        observed_at: float,
    ) -> None:
        metrics["battery_discharge_balance_victron_bias_response_delay_seconds"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
        )
        metrics["battery_discharge_balance_victron_bias_estimated_gain"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_estimated_gain", None)
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_telemetry_overshoot_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_count"] = int(
            getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_active"] = int(
            self._victron_ess_balance_overshoot_cooldown_active(svc, observed_at)
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_reason"] = str(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
        )
        metrics["battery_discharge_balance_victron_bias_settling_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_telemetry_settling_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_settled_count"] = int(
            getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_stability_score"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_stability_score", None)
        )
        self._populate_victron_ess_balance_runtime_safety_metrics(svc, observed_at, metrics)

    def _victron_ess_balance_recommendation_metrics(self, svc: Any) -> dict[str, Any]:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_enabled", False)):
            return self._victron_ess_balance_disabled_recommendation_metrics()
        active_profile_key, active_profile, use_profile = self._victron_ess_balance_recommendation_source(svc)
        observations = self._victron_ess_balance_recommendation_observations(svc, active_profile, use_profile)
        confidence = self._victron_ess_balance_recommendation_confidence(
            observations["delay_samples"],
            observations["gain_samples"],
            observations["stability_score"],
            observations["regime_consistency_score"],
            observations["response_variance_score"],
            observations["reproducibility_score"],
        )
        return self._victron_ess_balance_recommendation_payload(
            svc,
            active_profile_key,
            use_profile,
            active_profile,
            observations,
            confidence,
        )

    def _victron_ess_balance_recommended_activation_mode(
        self,
        active_profile: dict[str, Any],
        svc: Any,
    ) -> str:
        reserve_phase = str(active_profile.get("reserve_phase", "") or "")
        site_regime = str(active_profile.get("site_regime", "") or "")
        current_mode = self._victron_ess_balance_activation_mode(svc)
        if site_regime == "export":
            return self._victron_ess_balance_export_activation_mode(reserve_phase)
        if reserve_phase == "above_reserve_band":
            return "above_reserve_band"
        return current_mode

    @staticmethod
    def _victron_ess_balance_export_activation_mode(reserve_phase: str) -> str:
        if reserve_phase == "above_reserve_band":
            return "export_and_above_reserve_band"
        return "export_only"
