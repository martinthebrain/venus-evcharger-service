# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias recommendation helpers."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_recommendation_support import (
    VictronEssRecommendationPolicy,
)
from .victron_ess_balance_apply_sources import VictronEssSourceResolver
from .victron_ess_balance_learning_profiles import VictronEssLearningProfiles
from .victron_ess_balance_safety import VictronEssSafetyController
from .victron_ess_balance_safety_support import VictronEssSafetyRecovery

_OBSERVATION_FLOAT_FIELDS = (
    ("response_delay_seconds", "_victron_ess_balance_telemetry_response_delay_seconds"),
    ("estimated_gain", "_victron_ess_balance_telemetry_estimated_gain"),
    ("stability_score", "_victron_ess_balance_telemetry_stability_score"),
)
_OBSERVATION_COUNT_FIELDS = (
    ("overshoot_count", "_victron_ess_balance_telemetry_overshoot_count"),
    ("settled_count", "_victron_ess_balance_telemetry_settled_count"),
    ("delay_samples", "_victron_ess_balance_telemetry_delay_samples"),
    ("gain_samples", "_victron_ess_balance_telemetry_gain_samples"),
)
_PROFILE_SCORE_FIELDS = ("regime_consistency_score", "response_variance_score", "reproducibility_score")
_TELEMETRY_FLOAT_FIELDS = (
    ("battery_discharge_balance_victron_bias_response_delay_seconds", "_victron_ess_balance_telemetry_response_delay_seconds"),
    ("battery_discharge_balance_victron_bias_estimated_gain", "_victron_ess_balance_telemetry_estimated_gain"),
    ("battery_discharge_balance_victron_bias_overshoot_cooldown_until", "_victron_ess_balance_overshoot_cooldown_until"),
    ("battery_discharge_balance_victron_bias_stability_score", "_victron_ess_balance_telemetry_stability_score"),
)
_TELEMETRY_BOOL_FIELDS = (
    ("battery_discharge_balance_victron_bias_overshoot_active", "_victron_ess_balance_telemetry_overshoot_active"),
    ("battery_discharge_balance_victron_bias_settling_active", "_victron_ess_balance_telemetry_settling_active"),
)
_TELEMETRY_COUNT_FIELDS = (
    ("battery_discharge_balance_victron_bias_overshoot_count", "_victron_ess_balance_telemetry_overshoot_count"),
    ("battery_discharge_balance_victron_bias_settled_count", "_victron_ess_balance_telemetry_settled_count"),
)


class VictronEssRecommendationEngine:
    def __init__(
        self,
        policy: VictronEssRecommendationPolicy,
        sources: VictronEssSourceResolver,
        profiles: VictronEssLearningProfiles,
        safety: VictronEssSafetyController,
        recovery: VictronEssSafetyRecovery,
    ) -> None:
        self._policy = policy
        self._sources = sources
        self._profiles = profiles
        self._safety = safety
        self._recovery = recovery


    def _victron_ess_balance_recommendation_source(
        self,
        svc: Any,
    ) -> tuple[str, dict[str, Any], bool]:
        active_profile_key = self._sources._normalized_text(
            getattr(svc, "_victron_ess_balance_active_learning_profile_key", None)
        )
        active_profile = self._profiles._victron_ess_balance_learning_profile_state(svc, active_profile_key)
        use_profile = self._profiles._victron_ess_balance_profile_sample_count(active_profile) > 0
        return active_profile_key, active_profile, use_profile

    def _victron_ess_balance_recommendation_observations(
        self,
        svc: Any,
        active_profile: dict[str, Any],
        use_profile: bool,
    ) -> dict[str, Any]:
        observations = {
            key: self._sources._optional_float(
                self._victron_ess_balance_observation_value(svc, active_profile, use_profile, key, service_attr)
            )
            for key, service_attr in _OBSERVATION_FLOAT_FIELDS
        }
        observations.update(
            {
                key: self._victron_ess_balance_observation_count(
                    svc, active_profile, use_profile, key, service_attr
                )
                for key, service_attr in _OBSERVATION_COUNT_FIELDS
            }
        )
        observations.update(
            {
                key: self._sources._optional_float(active_profile.get(key) if use_profile else None)
                for key in _PROFILE_SCORE_FIELDS
            }
        )
        return observations

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
        value = cls._victron_ess_balance_observation_value(
            svc, active_profile, use_profile, profile_key, service_attr
        )
        return 0 if value is None else max(0, int(value))

    def _victron_ess_balance_recommendation_reason(
        self,
        observations: dict[str, Any],
        confidence: float,
    ) -> str:
        if self._policy._victron_ess_balance_has_insufficient_telemetry(observations, confidence):
            return "insufficient_telemetry"
        if self._policy._victron_ess_balance_has_overshoot_risk(observations):
            return "overshoot_risk"
        if self._policy._victron_ess_balance_has_slow_response(observations):
            return "slow_response"
        if self._policy._victron_ess_balance_can_relax_conservatism(observations):
            return "can_relax_conservatism"
        return "telemetry_nominal"

    @staticmethod
    def _victron_ess_balance_adjusted_tuning(current: dict[str, float], reason: str) -> dict[str, float]:
        return VictronEssRecommendationPolicy._victron_ess_balance_adjusted_tuning(
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
        current = self._policy._victron_ess_balance_current_tuning_values(svc)
        reason = self._victron_ess_balance_recommendation_reason(observations, confidence)
        adjusted = self._victron_ess_balance_adjusted_tuning(current, reason)
        activation_mode = self._victron_ess_balance_recommended_activation_mode(active_profile if use_profile else {}, svc)
        ini_snippet = self._policy._victron_ess_balance_recommendation_ini_snippet(
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
            "battery_discharge_balance_victron_bias_recommendation_hint": self._policy._victron_ess_balance_recommendation_hint(
                reason,
                confidence,
            ),
        }

    def _populate_victron_ess_balance_telemetry_metrics(self, svc: Any, metrics: dict[str, Any]) -> None:
        observed = self._sources._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_observed_at", None))
        observed_at = 0.0 if observed is None else observed
        self._populate_victron_ess_balance_recommendation_telemetry_metrics(svc, metrics, observed_at)
        active_profile_key = self._sources._normalized_text(
            getattr(svc, "_victron_ess_balance_active_learning_profile_key", None)
        )
        self._profiles._merge_victron_ess_balance_learning_profile_metrics(svc, metrics, active_profile_key)
        metrics.update(self._victron_ess_balance_recommendation_metrics(svc))

    def _populate_victron_ess_balance_recommendation_telemetry_metrics(
        self,
        svc: Any,
        metrics: dict[str, Any],
        observed_at: float,
    ) -> None:
        metrics.update(self._victron_ess_balance_telemetry_float_metrics(svc))
        metrics.update(self._victron_ess_balance_telemetry_bool_metrics(svc))
        metrics.update(self._victron_ess_balance_telemetry_count_metrics(svc))
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_active"] = int(
            self._recovery._victron_ess_balance_overshoot_cooldown_active(svc, observed_at)
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_reason"] = str(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", None) or ""
        )
        self._safety._populate_victron_ess_balance_runtime_safety_metrics(svc, observed_at, metrics)

    def _victron_ess_balance_telemetry_float_metrics(self, svc: Any) -> dict[str, float | None]:
        return {
            metric: self._sources._optional_float(getattr(svc, service_attr, None))
            for metric, service_attr in _TELEMETRY_FLOAT_FIELDS
        }

    @staticmethod
    def _victron_ess_balance_telemetry_bool_metrics(svc: Any) -> dict[str, int]:
        return {
            metric: int(bool(getattr(svc, service_attr, None)))
            for metric, service_attr in _TELEMETRY_BOOL_FIELDS
        }

    @staticmethod
    def _victron_ess_balance_telemetry_count_metrics(svc: Any) -> dict[str, int]:
        return {
            metric: int(getattr(svc, service_attr, None) or 0)
            for metric, service_attr in _TELEMETRY_COUNT_FIELDS
        }

    def _victron_ess_balance_recommendation_metrics(self, svc: Any) -> dict[str, Any]:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_enabled", None)):
            return self._policy._victron_ess_balance_disabled_recommendation_metrics()
        active_profile_key, active_profile, use_profile = self._victron_ess_balance_recommendation_source(svc)
        observations = self._victron_ess_balance_recommendation_observations(svc, active_profile, use_profile)
        confidence = self._policy._victron_ess_balance_recommendation_confidence(
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
        reserve_phase = self._sources._normalized_text(active_profile.get("reserve_phase"))
        site_regime = self._sources._normalized_text(active_profile.get("site_regime"))
        if site_regime == "export":
            return self._victron_ess_balance_export_activation_mode(reserve_phase)
        if reserve_phase == "above_reserve_band":
            return "above_reserve_band"
        return self._sources._victron_ess_balance_activation_mode(svc)

    @staticmethod
    def _victron_ess_balance_export_activation_mode(reserve_phase: str) -> str:
        if reserve_phase == "above_reserve_band":
            return "export_and_above_reserve_band"
        return "export_only"
