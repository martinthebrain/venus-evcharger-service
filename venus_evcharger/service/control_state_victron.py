# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron-bias state payload helpers for the Control API role."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts import normalized_state_api_runtime_fields
from venus_evcharger.service.control_state_operational import _ControlApiStateOperational


class _ControlApiStateVictron(_ControlApiStateOperational):
    def _state_api_victron_bias_recommendation_payload(self) -> dict[str, Any]:
        return _state_api_victron_bias_recommendation_payload(self)


def _state_api_victron_bias_recommendation_payload(owner: Any) -> dict[str, Any]:
    metrics = getattr(owner, "_last_auto_metrics", {}) or {}
    raw_learning_profiles = getattr(owner, "_victron_ess_balance_learning_profiles", {})
    learning_profiles = raw_learning_profiles if isinstance(raw_learning_profiles, dict) else {}
    state = _state_api_victron_bias_state(owner, metrics, learning_profiles)
    return normalized_state_api_runtime_fields(
        {
            "ok": True,
            "api_version": "v1",
            "kind": "victron-bias-recommendation",
            "state": state,
        }
    )


def _state_api_victron_bias_state(
    owner: Any,
    metrics: dict[str, Any],
    learning_profiles: dict[str, Any],
) -> dict[str, Any]:
    state = _state_api_victron_bias_core_state(owner, metrics)
    state["learning_state"] = _state_api_victron_bias_learning_state(metrics, learning_profiles)
    state["adaptive_tuning"] = _state_api_victron_bias_adaptive_tuning(owner, metrics)
    state["learning_profiles"] = learning_profiles
    return state


def _state_api_victron_bias_core_state(owner: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(metrics.get("battery_discharge_balance_victron_bias_enabled")),
        "active": bool(metrics.get("battery_discharge_balance_victron_bias_active")),
        "source_id": metrics.get("battery_discharge_balance_victron_bias_source_id"),
        "topology_key": metrics.get("battery_discharge_balance_victron_bias_topology_key"),
        "support_mode": metrics.get("battery_discharge_balance_victron_bias_support_mode"),
        "activation_mode": metrics.get("battery_discharge_balance_victron_bias_activation_mode"),
        "activation_gate_active": bool(metrics.get("battery_discharge_balance_victron_bias_activation_gate_active")),
        "active_learning_profile_key": metrics.get("battery_discharge_balance_victron_bias_learning_profile_key"),
        "active_learning_profile": _state_api_victron_active_learning_profile(metrics),
        "current_kp": getattr(owner, "auto_battery_discharge_balance_victron_bias_kp", 0.0),
        "current_ki": getattr(owner, "auto_battery_discharge_balance_victron_bias_ki", 0.0),
        "current_kd": getattr(owner, "auto_battery_discharge_balance_victron_bias_kd", 0.0),
        "current_deadband_watts": getattr(owner, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0),
        "current_max_abs_watts": getattr(owner, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0),
        "current_ramp_rate_watts_per_second": getattr(
            owner,
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            0.0,
        ),
        "recommended_kp": metrics.get("battery_discharge_balance_victron_bias_recommended_kp"),
        "recommended_ki": metrics.get("battery_discharge_balance_victron_bias_recommended_ki"),
        "recommended_kd": metrics.get("battery_discharge_balance_victron_bias_recommended_kd"),
        "recommended_deadband_watts": metrics.get("battery_discharge_balance_victron_bias_recommended_deadband_watts"),
        "recommended_max_abs_watts": metrics.get("battery_discharge_balance_victron_bias_recommended_max_abs_watts"),
        "recommended_ramp_rate_watts_per_second": metrics.get(
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"
        ),
        "recommended_activation_mode": metrics.get("battery_discharge_balance_victron_bias_recommended_activation_mode"),
        "recommendation_confidence": metrics.get("battery_discharge_balance_victron_bias_recommendation_confidence"),
        "recommendation_regime_consistency_score": metrics.get(
            "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score"
        ),
        "recommendation_response_variance_score": metrics.get(
            "battery_discharge_balance_victron_bias_recommendation_response_variance_score"
        ),
        "recommendation_reproducibility_score": metrics.get(
            "battery_discharge_balance_victron_bias_recommendation_reproducibility_score"
        ),
        "recommendation_reason": metrics.get("battery_discharge_balance_victron_bias_recommendation_reason"),
        "recommendation_profile_key": metrics.get("battery_discharge_balance_victron_bias_recommendation_profile_key"),
        "recommendation_hint": metrics.get("battery_discharge_balance_victron_bias_recommendation_hint"),
        "recommendation_ini_snippet": metrics.get("battery_discharge_balance_victron_bias_recommendation_ini_snippet"),
        "telemetry_clean": bool(metrics.get("battery_discharge_balance_victron_bias_telemetry_clean")),
        "telemetry_clean_reason": metrics.get("battery_discharge_balance_victron_bias_telemetry_clean_reason"),
        "response_delay_seconds": metrics.get("battery_discharge_balance_victron_bias_response_delay_seconds"),
        "estimated_gain": metrics.get("battery_discharge_balance_victron_bias_estimated_gain"),
        "overshoot_active": bool(metrics.get("battery_discharge_balance_victron_bias_overshoot_active")),
        "overshoot_count": metrics.get("battery_discharge_balance_victron_bias_overshoot_count"),
        "overshoot_cooldown_active": bool(metrics.get("battery_discharge_balance_victron_bias_overshoot_cooldown_active")),
        "overshoot_cooldown_reason": metrics.get("battery_discharge_balance_victron_bias_overshoot_cooldown_reason"),
        "overshoot_cooldown_until": metrics.get("battery_discharge_balance_victron_bias_overshoot_cooldown_until"),
        "settling_active": bool(metrics.get("battery_discharge_balance_victron_bias_settling_active")),
        "settled_count": metrics.get("battery_discharge_balance_victron_bias_settled_count"),
        "stability_score": metrics.get("battery_discharge_balance_victron_bias_stability_score"),
        "oscillation_lockout_enabled": bool(metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_enabled")),
        "oscillation_lockout_active": bool(metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_active")),
        "oscillation_lockout_reason": metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_reason"),
        "oscillation_lockout_until": metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_until"),
        "oscillation_direction_change_count": metrics.get(
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count"
        ),
        "auto_apply_enabled": bool(metrics.get("battery_discharge_balance_victron_bias_auto_apply_enabled")),
        "auto_apply_active": bool(metrics.get("battery_discharge_balance_victron_bias_auto_apply_active")),
        "auto_apply_reason": metrics.get("battery_discharge_balance_victron_bias_auto_apply_reason"),
        "auto_apply_generation": metrics.get("battery_discharge_balance_victron_bias_auto_apply_generation"),
        "auto_apply_observation_window_active": bool(
            metrics.get("battery_discharge_balance_victron_bias_auto_apply_observation_window_active")
        ),
        "auto_apply_observation_window_until": metrics.get(
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_until"
        ),
        "auto_apply_last_param": metrics.get("battery_discharge_balance_victron_bias_auto_apply_last_param"),
        "auto_apply_suspend_active": bool(metrics.get("battery_discharge_balance_victron_bias_auto_apply_suspend_active")),
        "auto_apply_suspend_reason": metrics.get("battery_discharge_balance_victron_bias_auto_apply_suspend_reason"),
        "auto_apply_suspend_until": metrics.get("battery_discharge_balance_victron_bias_auto_apply_suspend_until"),
        "rollback_enabled": bool(metrics.get("battery_discharge_balance_victron_bias_rollback_enabled")),
        "rollback_active": bool(metrics.get("battery_discharge_balance_victron_bias_rollback_active")),
        "rollback_reason": metrics.get("battery_discharge_balance_victron_bias_rollback_reason"),
        "rollback_stable_profile_key": metrics.get("battery_discharge_balance_victron_bias_rollback_stable_profile_key"),
        "safe_state_active": bool(metrics.get("battery_discharge_balance_victron_bias_safe_state_active")),
        "safe_state_reason": metrics.get("battery_discharge_balance_victron_bias_safe_state_reason"),
        "controller_reason": metrics.get("battery_discharge_balance_victron_bias_reason"),
    }


def _state_api_victron_active_learning_profile(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": metrics.get("battery_discharge_balance_victron_bias_learning_profile_key"),
        "action_direction": metrics.get("battery_discharge_balance_victron_bias_learning_profile_action_direction"),
        "site_regime": metrics.get("battery_discharge_balance_victron_bias_learning_profile_site_regime"),
        "direction": metrics.get("battery_discharge_balance_victron_bias_learning_profile_direction"),
        "day_phase": metrics.get("battery_discharge_balance_victron_bias_learning_profile_day_phase"),
        "reserve_phase": metrics.get("battery_discharge_balance_victron_bias_learning_profile_reserve_phase"),
        "ev_phase": metrics.get("battery_discharge_balance_victron_bias_learning_profile_ev_phase"),
        "pv_phase": metrics.get("battery_discharge_balance_victron_bias_learning_profile_pv_phase"),
        "battery_limit_phase": metrics.get("battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase"),
        "sample_count": metrics.get("battery_discharge_balance_victron_bias_learning_profile_sample_count"),
        "response_delay_seconds": metrics.get(
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds"
        ),
        "estimated_gain": metrics.get("battery_discharge_balance_victron_bias_learning_profile_estimated_gain"),
        "overshoot_count": metrics.get("battery_discharge_balance_victron_bias_learning_profile_overshoot_count"),
        "settled_count": metrics.get("battery_discharge_balance_victron_bias_learning_profile_settled_count"),
        "stability_score": metrics.get("battery_discharge_balance_victron_bias_learning_profile_stability_score"),
        "regime_consistency_score": metrics.get(
            "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score"
        ),
        "response_variance_score": metrics.get(
            "battery_discharge_balance_victron_bias_learning_profile_response_variance_score"
        ),
        "reproducibility_score": metrics.get(
            "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score"
        ),
        "safe_ramp_rate_watts_per_second": metrics.get(
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second"
        ),
        "preferred_bias_limit_watts": metrics.get(
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts"
        ),
    }


def _state_api_victron_bias_learning_state(
    metrics: dict[str, Any],
    learning_profiles: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "topology_key": metrics.get("battery_discharge_balance_victron_bias_topology_key"),
        "source_id": metrics.get("battery_discharge_balance_victron_bias_source_id"),
        "profiles": learning_profiles,
    }


def _state_api_victron_bias_adaptive_tuning(owner: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "topology_key": metrics.get("battery_discharge_balance_victron_bias_topology_key"),
        "source_id": metrics.get("battery_discharge_balance_victron_bias_source_id"),
        "kp": getattr(owner, "auto_battery_discharge_balance_victron_bias_kp", 0.0),
        "ki": getattr(owner, "auto_battery_discharge_balance_victron_bias_ki", 0.0),
        "kd": getattr(owner, "auto_battery_discharge_balance_victron_bias_kd", 0.0),
        "deadband_watts": getattr(owner, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0),
        "max_abs_watts": getattr(owner, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0),
        "ramp_rate_watts_per_second": getattr(
            owner,
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            0.0,
        ),
        "activation_mode": metrics.get("battery_discharge_balance_victron_bias_activation_mode"),
        "auto_apply_generation": metrics.get("battery_discharge_balance_victron_bias_auto_apply_generation"),
        "auto_apply_observe_until": metrics.get("battery_discharge_balance_victron_bias_auto_apply_observation_window_until"),
        "auto_apply_last_applied_param": metrics.get("battery_discharge_balance_victron_bias_auto_apply_last_param"),
        "oscillation_lockout_until": metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_until"),
        "oscillation_lockout_reason": metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_reason"),
        "overshoot_cooldown_until": metrics.get("battery_discharge_balance_victron_bias_overshoot_cooldown_until"),
        "overshoot_cooldown_reason": metrics.get("battery_discharge_balance_victron_bias_overshoot_cooldown_reason"),
        "last_stable_tuning": dict(getattr(owner, "_victron_ess_balance_last_stable_tuning", {}) or {}),
        "last_stable_at": getattr(owner, "_victron_ess_balance_last_stable_at", None),
        "last_stable_profile_key": getattr(owner, "_victron_ess_balance_last_stable_profile_key", ""),
        "conservative_tuning": dict(getattr(owner, "_victron_ess_balance_conservative_tuning", {}) or {}),
        "auto_apply_suspend_until": metrics.get("battery_discharge_balance_victron_bias_auto_apply_suspend_until"),
        "auto_apply_suspend_reason": metrics.get("battery_discharge_balance_victron_bias_auto_apply_suspend_reason"),
        "safe_state_active": metrics.get("battery_discharge_balance_victron_bias_safe_state_active"),
        "safe_state_reason": metrics.get("battery_discharge_balance_victron_bias_safe_state_reason"),
    }
