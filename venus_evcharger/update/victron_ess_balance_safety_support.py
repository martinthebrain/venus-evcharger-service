# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for Victron ESS balance-bias safety handling."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts_basic import non_negative_int

from .victron_ess_balance_apply_sources import VictronEssSourceResolver
from .victron_ess_balance_learning_profiles import VictronEssLearningProfiles


class VictronEssSafetyRecovery:
    def __init__(self, sources: VictronEssSourceResolver, profiles: VictronEssLearningProfiles) -> None:
        self._sources = sources
        self._profiles = profiles

    def _victron_ess_balance_refresh_stable_tuning(self, svc: Any, metrics: dict[str, Any], now: float) -> None:
        confidence = self._sources._optional_float(
            metrics.get("battery_discharge_balance_victron_bias_recommendation_confidence")
        )
        stability = self._sources._optional_float(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_stability_score")
        )
        sample_count = non_negative_int(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_sample_count")
        )
        overshoot_count = non_negative_int(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_overshoot_count")
        )
        if not self._victron_ess_balance_can_refresh_stable_tuning(
            confidence,
            stability,
            sample_count,
            overshoot_count,
        ):
            return
        self._victron_ess_balance_ensure_conservative_tuning(svc)
        svc._victron_ess_balance_last_stable_tuning = self._profiles._victron_ess_balance_current_tuning_snapshot(svc)
        svc._victron_ess_balance_last_stable_at = float(now)
        svc._victron_ess_balance_last_stable_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_key") or ""
        ).strip()

    @staticmethod
    def _victron_ess_balance_can_refresh_stable_tuning(
        confidence: float | None,
        stability: float | None,
        sample_count: int,
        overshoot_count: int,
    ) -> bool:
        return (
            VictronEssSafetyRecovery._victron_ess_balance_has_minimum_confidence(confidence)
            and VictronEssSafetyRecovery._victron_ess_balance_has_minimum_stability(stability)
            and sample_count >= 2
            and overshoot_count <= 0
        )

    @staticmethod
    def _victron_ess_balance_has_minimum_confidence(confidence: float | None) -> bool:
        return confidence is not None and confidence >= 0.8

    @staticmethod
    def _victron_ess_balance_has_minimum_stability(stability: float | None) -> bool:
        return stability is not None and stability >= 0.8

    def _victron_ess_balance_ensure_conservative_tuning(self, svc: Any) -> None:
        if not svc._victron_ess_balance_conservative_tuning:
            svc._victron_ess_balance_conservative_tuning = self._profiles._victron_ess_balance_current_tuning_snapshot(svc)

    def _victron_ess_balance_should_rollback_stable_tuning(
        self,
        svc: Any,
        metrics: dict[str, Any],
        now: float,
    ) -> bool:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True)):
            return False
        observe_until = self._sources._optional_float(svc._victron_ess_balance_auto_apply_observe_until)
        if not self._victron_ess_balance_observation_window_active(now, observe_until):
            return False
        if self._victron_ess_balance_has_immediate_rollback_signal(metrics):
            return True
        stability = self._sources._optional_float(metrics.get("battery_discharge_balance_victron_bias_stability_score"))
        rollback_min_stability = self._victron_ess_balance_rollback_min_stability_score(svc)
        return bool(stability is not None and stability < rollback_min_stability)

    @staticmethod
    def _victron_ess_balance_rollback_min_stability_score(svc: Any) -> float:
        return max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score", None) or 0.45),
        )

    @staticmethod
    def _victron_ess_balance_observation_window_active(now: float, observe_until: float | None) -> bool:
        return observe_until is not None and float(now) < float(observe_until)

    @staticmethod
    def _victron_ess_balance_has_immediate_rollback_signal(metrics: dict[str, Any]) -> bool:
        return any(
            bool(metrics.get(key))
            for key in (
                "battery_discharge_balance_victron_bias_oscillation_lockout_active",
                "battery_discharge_balance_victron_bias_overshoot_active",
                "battery_discharge_balance_victron_bias_overshoot_cooldown_active",
            )
        )

    def _maybe_restore_victron_ess_balance_stable_tuning(
        self,
        svc: Any,
        metrics: dict[str, Any],
        reason: str,
    ) -> bool:
        metrics["battery_discharge_balance_victron_bias_rollback_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True))
        )
        stable, safe_state_reason = self._victron_ess_balance_restore_target(svc, reason)
        if stable is None:
            metrics["battery_discharge_balance_victron_bias_rollback_reason"] = "no_stable_tuning"
            return False
        svc._victron_ess_balance_safe_state_active = True
        svc._victron_ess_balance_safe_state_reason = safe_state_reason
        self._apply_victron_ess_balance_restored_tuning(svc, stable)
        svc._victron_ess_balance_auto_apply_observe_until = None
        self._victron_ess_balance_suspend_auto_apply(
            svc,
            reason,
            self._sources._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_last_applied_at", None)),
        )
        metrics["battery_discharge_balance_victron_bias_rollback_active"] = 1
        metrics["battery_discharge_balance_victron_bias_rollback_reason"] = str(reason)
        metrics["battery_discharge_balance_victron_bias_rollback_stable_profile_key"] = str(
            getattr(svc, "_victron_ess_balance_last_stable_profile_key", None) or ""
        )
        metrics["battery_discharge_balance_victron_bias_safe_state_active"] = 1
        metrics["battery_discharge_balance_victron_bias_safe_state_reason"] = str(svc._victron_ess_balance_safe_state_reason)
        return True

    def _victron_ess_balance_restore_target(
        self,
        svc: Any,
        reason: str,
    ) -> tuple[dict[str, Any] | None, str]:
        stable = getattr(svc, "_victron_ess_balance_last_stable_tuning", None)
        if isinstance(stable, dict) and stable:
            return stable, str(reason)
        conservative = getattr(svc, "_victron_ess_balance_conservative_tuning", None)
        if isinstance(conservative, dict) and conservative:
            return conservative, "conservative_fallback"
        return None, ""

    def _apply_victron_ess_balance_restored_tuning(self, svc: Any, stable: dict[str, Any]) -> None:
        self._apply_victron_ess_balance_restored_pid_terms(svc, stable)
        self._apply_victron_ess_balance_restored_limits(svc, stable)
        activation_mode = self._victron_ess_balance_restored_activation_mode(svc, stable)
        if activation_mode:
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = activation_mode

    @staticmethod
    def _apply_victron_ess_balance_restored_pid_terms(svc: Any, stable: dict[str, Any]) -> None:
        svc.auto_battery_discharge_balance_victron_bias_kp = float(stable.get("kp") or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_ki = float(stable.get("ki") or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_kd = float(stable.get("kd") or 0.0)

    @staticmethod
    def _apply_victron_ess_balance_restored_limits(svc: Any, stable: dict[str, Any]) -> None:
        svc.auto_battery_discharge_balance_victron_bias_deadband_watts = float(stable.get("deadband_watts") or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_max_abs_watts = float(stable.get("max_abs_watts") or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = float(
            stable.get("ramp_rate_watts_per_second") or 0.0
        )

    def _victron_ess_balance_restored_activation_mode(self, svc: Any, stable: dict[str, Any]) -> str:
        if "activation_mode" in stable:
            return str(stable.get("activation_mode") or "always").strip()
        return str(self._sources._victron_ess_balance_activation_mode(svc) or "always").strip()

    def _enter_victron_ess_balance_overshoot_cooldown(self, svc: Any, now: float, reason: str) -> None:
        response_delay = self._sources._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
        )
        cooldown_seconds = max(20.0, min(180.0, (response_delay or 10.0) * 4.0))
        svc._victron_ess_balance_overshoot_cooldown_until = float(now + cooldown_seconds)
        svc._victron_ess_balance_overshoot_cooldown_reason = str(reason)
        self._victron_ess_balance_suspend_auto_apply(svc, "overshoot_cooldown", now)

    def _victron_ess_balance_overshoot_cooldown_active(self, svc: Any, now: float) -> bool:
        cooldown_until = self._sources._optional_float(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
        )
        return cooldown_until is not None and float(now) < float(cooldown_until)

    def _victron_ess_balance_suspend_auto_apply(self, svc: Any, reason: str, now: float | None = None) -> None:
        effective_now = (
            float(now)
            if isinstance(now, (int, float))
            else (
                self._sources._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_observed_at", None))
                or self._sources._optional_float(
                    getattr(svc, "_victron_ess_balance_auto_apply_last_applied_at", None)
                )
                or 0.0
            )
        )
        observation_seconds = max(
            30.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_observation_window_seconds", None) or 30.0
            ),
        )
        svc._victron_ess_balance_auto_apply_suspend_until = float(effective_now + (2.0 * observation_seconds))
        svc._victron_ess_balance_auto_apply_suspend_reason = str(reason)

    def _victron_ess_balance_auto_apply_suspended(self, svc: Any, now: float) -> bool:
        suspend_until = self._sources._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
        )
        return suspend_until is not None and float(now) < float(suspend_until)
