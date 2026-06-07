# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias application helpers."""

from __future__ import annotations

import dbus
import logging
from typing import Any, cast

from .victron_ess_balance_apply_support import _UpdateCycleVictronEssBalanceApplySupportMixin
from .victron_ess_balance_learning_profiles_support import (
    _clear_victron_ess_balance_tracking_episode_state,
    _record_victron_ess_balance_tracking_command,
    _reset_victron_ess_balance_pid_integral_state,
    _reset_victron_ess_balance_pid_state,
)


class _UpdateCycleVictronEssBalanceApplyMixin(_UpdateCycleVictronEssBalanceApplySupportMixin):
    """Apply one optional Victron-side ESS balance bias through a GX DBus setpoint."""

    @staticmethod
    def _victron_ess_balance_default_metrics(reason: str = "disabled") -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_enabled": 0,
            "battery_discharge_balance_victron_bias_active": 0,
            "battery_discharge_balance_victron_bias_source_id": "",
            "battery_discharge_balance_victron_bias_topology_key": "",
            "battery_discharge_balance_victron_bias_support_mode": "supported_only",
            "battery_discharge_balance_victron_bias_learning_profile_key": "",
            "battery_discharge_balance_victron_bias_learning_profile_action_direction": "",
            "battery_discharge_balance_victron_bias_learning_profile_site_regime": "",
            "battery_discharge_balance_victron_bias_learning_profile_direction": "",
            "battery_discharge_balance_victron_bias_learning_profile_day_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_ev_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_pv_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": None,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_settled_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_response_variance_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": None,
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": None,
            "battery_discharge_balance_victron_bias_source_error_w": None,
            "battery_discharge_balance_victron_bias_pid_output_w": 0.0,
            "battery_discharge_balance_victron_bias_setpoint_w": None,
            "battery_discharge_balance_victron_bias_activation_mode": "always",
            "battery_discharge_balance_victron_bias_activation_gate_active": 0,
            "battery_discharge_balance_victron_bias_telemetry_clean": 0,
            "battery_discharge_balance_victron_bias_telemetry_clean_reason": "unknown",
            "battery_discharge_balance_victron_bias_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_estimated_gain": None,
            "battery_discharge_balance_victron_bias_overshoot_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "",
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": None,
            "battery_discharge_balance_victron_bias_settling_active": 0,
            "battery_discharge_balance_victron_bias_settled_count": 0,
            "battery_discharge_balance_victron_bias_stability_score": None,
            "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "",
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": None,
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 0,
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
            "battery_discharge_balance_victron_bias_auto_apply_enabled": 0,
            "battery_discharge_balance_victron_bias_auto_apply_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_reason": "disabled",
            "battery_discharge_balance_victron_bias_auto_apply_generation": 0,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": None,
            "battery_discharge_balance_victron_bias_auto_apply_last_param": "",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": "",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_until": None,
            "battery_discharge_balance_victron_bias_rollback_enabled": 0,
            "battery_discharge_balance_victron_bias_rollback_active": 0,
            "battery_discharge_balance_victron_bias_rollback_reason": "disabled",
            "battery_discharge_balance_victron_bias_rollback_stable_profile_key": "",
            "battery_discharge_balance_victron_bias_safe_state_active": 0,
            "battery_discharge_balance_victron_bias_safe_state_reason": "",
            "battery_discharge_balance_victron_bias_reason": reason,
        }

    def apply_victron_ess_balance_bias(self, svc: Any, now: float, auto_mode_active: bool) -> None:
        metrics = self._victron_ess_balance_default_metrics("disabled")
        self._initialize_victron_ess_balance_apply_metrics(svc, metrics)
        if not self._victron_ess_balance_enabled(svc):
            self._disable_victron_ess_balance(svc, metrics)
            return
        cluster, source_error_w, profile_key, prepare_reason = self._prepare_victron_ess_balance_tracking_state(
            svc,
            now,
            auto_mode_active,
            metrics,
        )
        if prepare_reason:
            self._restore_victron_ess_balance_base_setpoint(svc, now, metrics, prepare_reason)
            return
        assert source_error_w is not None
        assert profile_key is not None
        self._apply_victron_ess_balance_tracking(
            svc,
            now,
            cluster,
            source_error_w,
            profile_key,
            metrics,
        )

    def _initialize_victron_ess_balance_apply_metrics(self, svc: Any, metrics: dict[str, Any]) -> None:
        metrics["battery_discharge_balance_victron_bias_enabled"] = int(self._victron_ess_balance_enabled(svc))
        metrics["battery_discharge_balance_victron_bias_support_mode"] = self._victron_ess_balance_support_mode(svc)
        metrics["battery_discharge_balance_victron_bias_activation_mode"] = self._victron_ess_balance_activation_mode(svc)
        metrics["battery_discharge_balance_victron_bias_auto_apply_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False))
        )

    @staticmethod
    def _victron_ess_balance_enabled(svc: Any) -> bool:
        return bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_enabled", False))

    @staticmethod
    def _reset_victron_ess_balance_pid(svc: Any) -> None:
        _reset_victron_ess_balance_pid_state(svc)

    @staticmethod
    def _reset_victron_ess_balance_pid_integral(svc: Any, aggressive: bool = False) -> None:
        _reset_victron_ess_balance_pid_integral_state(svc, aggressive)

    def _record_victron_ess_balance_command(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
    ) -> None:
        _record_victron_ess_balance_tracking_command(svc, now, setpoint_w, source_error_w, profile_key)

    @staticmethod
    def _clear_victron_ess_balance_tracking_episode(svc: Any) -> None:
        _clear_victron_ess_balance_tracking_episode_state(svc)

    def _disable_victron_ess_balance(self, svc: Any, metrics: dict[str, Any]) -> None:
        self._merge_victron_ess_balance_metrics(svc, metrics)
        self._reset_victron_ess_balance_pid(svc)

    def _victron_ess_balance_cluster_state(
        self,
        svc: Any,
        auto_mode_active: bool,
    ) -> tuple[dict[str, Any], str]:
        if not auto_mode_active:
            return {}, "auto-mode-inactive"
        cluster = self._normalized_mapping(getattr(svc, "_last_energy_cluster", {}))
        eligible_source_count = int(cluster.get("battery_discharge_balance_eligible_source_count", 0) or 0)
        if eligible_source_count < 2:
            return cluster, "insufficient-eligible-sources"
        return cluster, ""

    def _victron_ess_balance_source_state(
        self,
        cluster: dict[str, Any],
        svc: Any,
        metrics: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, float | None, str]:
        source, resolve_reason = self._victron_ess_balance_source(cluster, svc)
        if source is None:
            return None, None, resolve_reason
        source_id = str(source.get("source_id", "")).strip()
        metrics["battery_discharge_balance_victron_bias_source_id"] = source_id
        metrics["battery_discharge_balance_victron_bias_topology_key"] = self._victron_ess_balance_current_topology_key(
            svc,
            source_id,
        )
        if not bool(source.get("online", True)):
            return None, None, "victron-source-offline"
        source_error_w = self._optional_float(source.get("discharge_balance_error_w"))
        metrics["battery_discharge_balance_victron_bias_source_error_w"] = source_error_w
        if source_error_w is None:
            return None, None, "victron-source-error-missing"
        if not self._victron_ess_balance_source_support_allowed(source, svc):
            return None, None, "victron-source-support-blocked"
        return source, source_error_w, ""

    def _prepare_victron_ess_balance_learning_state(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source: dict[str, Any],
        source_error_w: float,
        metrics: dict[str, Any],
    ) -> dict[str, str]:
        learning_profile = self._victron_ess_balance_learning_profile(svc, cluster, source, source_error_w)
        self._set_victron_ess_balance_active_profile(svc, learning_profile)
        self._merge_victron_ess_balance_learning_profile_metrics(svc, metrics, learning_profile["key"])
        self._victron_ess_balance_refresh_stable_tuning(svc, metrics, now)
        direction_change_count = self._victron_ess_balance_note_action_direction(
            svc,
            str(learning_profile.get("action_direction", "") or ""),
            now,
        )
        metrics["battery_discharge_balance_victron_bias_oscillation_direction_change_count"] = int(direction_change_count)
        self._populate_victron_ess_balance_runtime_safety_metrics(svc, now, metrics)
        return learning_profile

    def _prepare_victron_ess_balance_tracking_source(
        self,
        svc: Any,
        auto_mode_active: bool,
        metrics: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, float | None, str]:
        cluster, block_reason = self._victron_ess_balance_cluster_state(svc, auto_mode_active)
        if block_reason:
            return cluster, None, None, block_reason
        source, source_error_w, source_reason = self._victron_ess_balance_source_state(cluster, svc, metrics)
        if source_reason:
            return cluster, None, None, source_reason
        return cluster, source, source_error_w, ""

    def _prepare_victron_ess_balance_tracking_profile(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source: dict[str, Any],
        source_error_w: float,
        metrics: dict[str, Any],
    ) -> tuple[str | None, str]:
        learning_profile = self._prepare_victron_ess_balance_learning_state(
            svc,
            now,
            cluster,
            source,
            source_error_w,
            metrics,
        )
        safety_reason = self._victron_ess_balance_safety_block_reason(svc, now, metrics)
        if safety_reason:
            return None, safety_reason
        if not self._victron_ess_balance_activation_allowed(learning_profile, svc):
            return None, "activation-mode-blocked"
        return str(learning_profile["key"]), ""

    def _prepare_victron_ess_balance_tracking_state(
        self,
        svc: Any,
        now: float,
        auto_mode_active: bool,
        metrics: dict[str, Any],
    ) -> tuple[dict[str, Any], float | None, str | None, str]:
        cluster, source, source_error_w, source_reason = self._prepare_victron_ess_balance_tracking_source(
            svc,
            auto_mode_active,
            metrics,
        )
        if source_reason:
            return cluster, None, None, source_reason
        assert source is not None
        assert source_error_w is not None
        profile_key, profile_reason = self._prepare_victron_ess_balance_tracking_profile(
            svc,
            now,
            cluster,
            source,
            source_error_w,
            metrics,
        )
        if profile_reason:
            return cluster, None, None, profile_reason
        return cluster, source_error_w, profile_key, ""

    def _victron_ess_balance_safety_block_reason(self, svc: Any, now: float, metrics: dict[str, Any]) -> str:
        if self._victron_ess_balance_overshoot_cooldown_active(svc, now):
            self._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "overshoot_cooldown")
            return "overshoot-cooldown-active"
        if self._victron_ess_balance_oscillation_lockout_active(svc, now):
            self._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "oscillation_lockout")
            return "oscillation-lockout-active"
        svc._victron_ess_balance_safe_state_active = False
        svc._victron_ess_balance_safe_state_reason = ""
        return ""

    @staticmethod
    def _victron_ess_balance_base_setpoint_w(svc: Any) -> float:
        return float(getattr(svc, "auto_battery_discharge_balance_victron_bias_base_setpoint_watts", 50.0) or 0.0)

    def _victron_ess_balance_tracking_setpoint(
        self,
        svc: Any,
        now: float,
        source_error_w: float,
        metrics: dict[str, Any],
    ) -> float:
        metrics["battery_discharge_balance_victron_bias_activation_gate_active"] = 1
        output_w = self._victron_ess_balance_pid_output(svc, source_error_w, now)
        setpoint_w = float(self._victron_ess_balance_base_setpoint_w(svc) + output_w)
        metrics["battery_discharge_balance_victron_bias_pid_output_w"] = float(output_w)
        metrics["battery_discharge_balance_victron_bias_setpoint_w"] = float(setpoint_w)
        metrics["battery_discharge_balance_victron_bias_reason"] = "tracking"
        svc._victron_ess_balance_pid_last_output_w = float(output_w)
        return setpoint_w

    def _victron_ess_balance_update_tracking_telemetry(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        self._update_victron_ess_balance_telemetry(
            svc,
            now,
            cluster,
            source_error_w,
            metrics,
            profile_key,
        )

    def _victron_ess_balance_apply_write_outcome(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        if self._victron_ess_balance_write_setpoint(
            svc,
            getattr(svc, "auto_battery_discharge_balance_victron_bias_service", ""),
            getattr(svc, "auto_battery_discharge_balance_victron_bias_path", ""),
            setpoint_w,
        ):
            svc._victron_ess_balance_last_write_at = float(now)
            svc._victron_ess_balance_last_setpoint_w = float(setpoint_w)
            self._record_victron_ess_balance_command(svc, now, setpoint_w, source_error_w, profile_key)
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = "applied"
            return
        metrics["battery_discharge_balance_victron_bias_reason"] = "write-failed"

    def _victron_ess_balance_tracking_write_state(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        if self._victron_ess_balance_should_write(svc, now, setpoint_w):
            self._victron_ess_balance_apply_write_outcome(
                svc,
                now,
                setpoint_w,
                source_error_w,
                profile_key,
                metrics,
            )
            return
        if self._victron_ess_balance_last_setpoint(svc) is not None:
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = "holding"

    def _finalize_victron_ess_balance_metrics(self, svc: Any, now: float, metrics: dict[str, Any]) -> None:
        self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
        self._merge_victron_ess_balance_metrics(svc, metrics)

    def _apply_victron_ess_balance_tracking(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        setpoint_w = self._victron_ess_balance_tracking_setpoint(svc, now, source_error_w, metrics)
        self._victron_ess_balance_update_tracking_telemetry(
            svc,
            now,
            cluster,
            source_error_w,
            profile_key,
            metrics,
        )
        self._victron_ess_balance_tracking_write_state(
            svc,
            now,
            setpoint_w,
            source_error_w,
            profile_key,
            metrics,
        )
        self._finalize_victron_ess_balance_metrics(svc, now, metrics)

    def _restore_victron_ess_balance_base_setpoint(
        self,
        svc: Any,
        now: float,
        metrics: dict[str, Any],
        reason: str,
    ) -> None:
        base_setpoint_w = self._victron_ess_balance_base_setpoint_w(svc)
        self._reset_victron_ess_balance_pid(svc)
        self._clear_victron_ess_balance_tracking_episode(svc)
        self._clear_victron_ess_balance_active_profile(svc)
        metrics["battery_discharge_balance_victron_bias_setpoint_w"] = float(base_setpoint_w)
        metrics["battery_discharge_balance_victron_bias_reason"] = reason
        metrics["battery_discharge_balance_victron_bias_activation_gate_active"] = 0
        if self._victron_ess_balance_last_setpoint(svc) is None:
            self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
            self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
            self._merge_victron_ess_balance_metrics(svc, metrics)
            return
        if not self._victron_ess_balance_should_write(svc, now, base_setpoint_w):
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = f"{reason}-holding"
            self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
            self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
            self._merge_victron_ess_balance_metrics(svc, metrics)
            return
        if self._victron_ess_balance_write_setpoint(
            svc,
            getattr(svc, "auto_battery_discharge_balance_victron_bias_service", ""),
            getattr(svc, "auto_battery_discharge_balance_victron_bias_path", ""),
            base_setpoint_w,
        ):
            svc._victron_ess_balance_last_write_at = float(now)
            svc._victron_ess_balance_last_setpoint_w = None
            metrics["battery_discharge_balance_victron_bias_reason"] = f"{reason}-restored"
        else:
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = f"{reason}-restore-failed"
        self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
        self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
        self._merge_victron_ess_balance_metrics(svc, metrics)
