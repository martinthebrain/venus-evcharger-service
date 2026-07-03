# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias adaptive tuning helpers."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_recommendation import _UpdateCycleVictronEssBalanceRecommendation


class _UpdateCycleVictronEssBalanceAdaptive(_UpdateCycleVictronEssBalanceRecommendation):
    @staticmethod
    def _victron_ess_balance_profile_keys(metrics: dict[str, Any]) -> tuple[str, str]:
        recommendation_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_recommendation_profile_key", "") or ""
        ).strip()
        active_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_key", "") or ""
        ).strip()
        return recommendation_profile_key, active_profile_key

    def _initialize_victron_ess_balance_auto_apply_metrics(self, svc: Any, metrics: dict[str, Any], now: float) -> None:
        self._initialize_victron_ess_balance_auto_apply_runtime_metrics(svc, metrics, now)
        self._initialize_victron_ess_balance_rollback_metrics(svc, metrics)
        self._initialize_victron_ess_balance_safe_state_metrics(svc, metrics)

    def _initialize_victron_ess_balance_auto_apply_runtime_metrics(
        self,
        svc: Any,
        metrics: dict[str, Any],
        now: float,
    ) -> None:
        metrics["battery_discharge_balance_victron_bias_auto_apply_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False))
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_active"] = 0
        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "disabled"
        metrics["battery_discharge_balance_victron_bias_auto_apply_generation"] = int(
            getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"] = 0
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None)
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_last_param"] = str(
            getattr(svc, "_victron_ess_balance_auto_apply_last_applied_param", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_active"] = int(
            self._victron_ess_balance_auto_apply_suspended(svc, now)
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_reason"] = str(
            getattr(svc, "_victron_ess_balance_auto_apply_suspend_reason", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
        )

    @staticmethod
    def _initialize_victron_ess_balance_rollback_metrics(svc: Any, metrics: dict[str, Any]) -> None:
        metrics["battery_discharge_balance_victron_bias_rollback_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True))
        )
        metrics["battery_discharge_balance_victron_bias_rollback_active"] = 0
        metrics["battery_discharge_balance_victron_bias_rollback_reason"] = "disabled"
        metrics["battery_discharge_balance_victron_bias_rollback_stable_profile_key"] = str(
            getattr(svc, "_victron_ess_balance_last_stable_profile_key", "") or ""
        )

    @staticmethod
    def _initialize_victron_ess_balance_safe_state_metrics(svc: Any, metrics: dict[str, Any]) -> None:
        metrics["battery_discharge_balance_victron_bias_safe_state_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_safe_state_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_safe_state_reason"] = str(
            getattr(svc, "_victron_ess_balance_safe_state_reason", "") or ""
        )

    def _victron_ess_balance_auto_apply_thresholds(self, svc: Any) -> tuple[float, float, int]:
        min_confidence = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence", 0.85) or 0.85),
        )
        min_stability = max(
            0.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score", 0.75)
                or 0.75
            ),
        )
        min_samples = max(
            1,
            int(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples", 3) or 3),
        )
        return min_confidence, min_stability, min_samples

    def _victron_ess_balance_auto_apply_confidence_reason(
        self,
        svc: Any,
        metrics: dict[str, Any],
    ) -> str:
        confidence = self._optional_float(
            metrics.get("battery_discharge_balance_victron_bias_recommendation_confidence")
        )
        min_confidence, _, _ = self._victron_ess_balance_auto_apply_thresholds(svc)
        if confidence is None or confidence < min_confidence:
            return "confidence_too_low"
        return ""

    def _victron_ess_balance_auto_apply_stability_reason(
        self,
        svc: Any,
        metrics: dict[str, Any],
    ) -> str:
        stability = self._optional_float(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_stability_score")
        )
        _, min_stability, _ = self._victron_ess_balance_auto_apply_thresholds(svc)
        if stability is None or stability < min_stability:
            return "stability_too_low"
        return ""

    def _victron_ess_balance_auto_apply_sample_reason(self, svc: Any, metrics: dict[str, Any]) -> str:
        sample_count = max(
            0,
            int(metrics.get("battery_discharge_balance_victron_bias_learning_profile_sample_count", 0) or 0),
        )
        _, _, min_samples = self._victron_ess_balance_auto_apply_thresholds(svc)
        if sample_count < min_samples:
            return "insufficient_profile_samples"
        return ""

    def _victron_ess_balance_auto_apply_profile_reason(self, metrics: dict[str, Any]) -> str:
        recommendation_profile_key, active_profile_key = self._victron_ess_balance_profile_keys(metrics)
        if not recommendation_profile_key or recommendation_profile_key != active_profile_key:
            return "profile_mismatch"
        return ""

    def _victron_ess_balance_auto_apply_readiness(
        self,
        svc: Any,
        metrics: dict[str, Any],
    ) -> str:
        for readiness_check in (
            self._victron_ess_balance_auto_apply_confidence_reason,
            self._victron_ess_balance_auto_apply_stability_reason,
            self._victron_ess_balance_auto_apply_sample_reason,
        ):
            reason = readiness_check(svc, metrics)
            if reason:
                return reason
        profile_reason = self._victron_ess_balance_auto_apply_profile_reason(metrics)
        if profile_reason:
            return profile_reason
        return ""

    @staticmethod
    def _victron_ess_balance_auto_apply_blend(svc: Any) -> float:
        return max(
            0.0,
            min(
                1.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_blend", 0.25) or 0.25),
            ),
        )

    @staticmethod
    def _victron_ess_balance_auto_apply_observation_until(svc: Any, now: float) -> float | None:
        observation_seconds = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_observation_window_seconds", 30.0) or 30.0),
        )
        if observation_seconds <= 0.0:
            return None
        return float(now + observation_seconds)

    @staticmethod
    def _victron_ess_balance_save_runtime_state(svc: Any) -> None:
        save_runtime_state = getattr(svc, "_save_runtime_state", None)
        if callable(save_runtime_state):
            save_runtime_state()

    def _apply_victron_ess_balance_auto_apply_step(self, svc: Any, metrics: dict[str, Any], now: float) -> bool:
        blend = self._victron_ess_balance_auto_apply_blend(svc)
        changed_param = self._apply_victron_ess_balance_recommended_tuning_step(svc, metrics, blend)
        if not changed_param:
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "already_at_recommendation"
            return False
        svc._victron_ess_balance_auto_apply_generation = max(
            0,
            int(getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0),
        ) + 1
        svc._victron_ess_balance_auto_apply_observe_until = self._victron_ess_balance_auto_apply_observation_until(
            svc,
            now,
        )
        svc._victron_ess_balance_auto_apply_last_applied_param = str(changed_param)
        svc._victron_ess_balance_auto_apply_last_applied_at = float(now)
        metrics["battery_discharge_balance_victron_bias_auto_apply_active"] = 1
        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "applied_step"
        metrics["battery_discharge_balance_victron_bias_auto_apply_generation"] = int(
            getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_last_param"] = str(changed_param)
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None)
        )
        self._victron_ess_balance_save_runtime_state(svc)
        return True

    @staticmethod
    def _victron_ess_balance_auto_apply_enabled(svc: Any) -> bool:
        return bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False))

    def _victron_ess_balance_auto_apply_observation_reason(self, svc: Any, metrics: dict[str, Any], now: float) -> str:
        observe_until = self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None))
        if observe_until is None or float(now) >= float(observe_until):
            return ""
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"] = 1
        return "observation_window_active"

    def _victron_ess_balance_auto_apply_suspend_reason(self, svc: Any, now: float) -> str:
        if self._victron_ess_balance_auto_apply_suspended(svc, now):
            return "auto_apply_suspended"
        return ""

    def _victron_ess_balance_auto_apply_rollback_reason(
        self,
        svc: Any,
        metrics: dict[str, Any],
        now: float,
    ) -> str:
        if not self._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, now):
            return ""
        if self._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "unstable_observation_window"):
            return "rolled_back"
        return ""

    def _victron_ess_balance_auto_apply_blocker_reason(
        self,
        svc: Any,
        metrics: dict[str, Any],
        now: float,
    ) -> str:
        for reason in (
            self._victron_ess_balance_auto_apply_suspend_reason(svc, now),
            self._victron_ess_balance_auto_apply_rollback_reason(svc, metrics, now),
            self._victron_ess_balance_auto_apply_observation_reason(svc, metrics, now),
            self._victron_ess_balance_auto_apply_readiness(svc, metrics),
        ):
            if reason:
                return reason
        return ""

    def _maybe_auto_apply_victron_ess_balance_recommendation(self, svc: Any, metrics: dict[str, Any], now: float) -> None:
        self._initialize_victron_ess_balance_auto_apply_metrics(svc, metrics, now)
        if not self._victron_ess_balance_auto_apply_enabled(svc):
            return
        blocker_reason = self._victron_ess_balance_auto_apply_blocker_reason(svc, metrics, now)
        if blocker_reason:
            if blocker_reason != "rolled_back":
                metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = blocker_reason
            return
        self._apply_victron_ess_balance_auto_apply_step(svc, metrics, now)

    def _apply_victron_ess_balance_recommended_tuning_step(
        self,
        svc: Any,
        metrics: dict[str, Any],
        blend: float,
    ) -> str:
        for attr_name, recommendation_key in self._victron_ess_balance_recommended_setting_pairs():
            if self._blend_recommended_setting(svc, attr_name, metrics.get(recommendation_key), blend):
                return attr_name
        return self._victron_ess_balance_recommended_activation_step(svc, metrics)

    @staticmethod
    def _victron_ess_balance_recommended_setting_pairs() -> tuple[tuple[str, str], ...]:
        return (
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
            ("auto_battery_discharge_balance_victron_bias_kp", "battery_discharge_balance_victron_bias_recommended_kp"),
            ("auto_battery_discharge_balance_victron_bias_ki", "battery_discharge_balance_victron_bias_recommended_ki"),
            ("auto_battery_discharge_balance_victron_bias_kd", "battery_discharge_balance_victron_bias_recommended_kd"),
        )

    def _victron_ess_balance_recommended_activation_step(self, svc: Any, metrics: dict[str, Any]) -> str:
        recommended_activation_mode = str(
            metrics.get("battery_discharge_balance_victron_bias_recommended_activation_mode", "") or ""
        ).strip()
        if recommended_activation_mode and recommended_activation_mode != self._victron_ess_balance_activation_mode(svc):
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = recommended_activation_mode
            return "auto_battery_discharge_balance_victron_bias_activation_mode"
        return ""

    @staticmethod
    def _blend_recommended_setting(svc: Any, attr_name: str, recommendation: Any, blend: float) -> bool:
        if recommendation is None or not hasattr(svc, attr_name):
            return False
        current_value = float(getattr(svc, attr_name, 0.0) or 0.0)
        target_value = float(recommendation)
        if abs(current_value - target_value) < 1e-6:
            return False
        blended_value = ((1.0 - float(blend)) * current_value) + (float(blend) * target_value)
        setattr(svc, attr_name, float(blended_value))
        return True
