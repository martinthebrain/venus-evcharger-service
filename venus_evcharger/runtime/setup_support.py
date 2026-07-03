# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for runtime setup and worker snapshot defaults."""

from __future__ import annotations

import os
from typing import Any

WorkerSnapshot = dict[str, Any]


def _read_version_line(path: str) -> str:
    """Return one stripped version line from a file when it exists."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def _first_existing_version_line(paths: tuple[str, ...]) -> str:
    """Return the first non-empty version line found in the candidate files."""
    for path in paths:
        version = _read_version_line(path)
        if version:
            return version
    return ""


def default_auto_metrics() -> dict[str, Any]:
    return {
        "state": "idle",
        "surplus": None,
        "grid": None,
        "soc": None,
        "profile": "normal",
        "start_threshold": None,
        "stop_threshold": None,
        "learned_charge_power": None,
        "threshold_scale": 1.0,
        "threshold_mode": "static",
        "stop_alpha": None,
        "stop_alpha_stage": "base",
        "surplus_volatility": None,
        "battery_surplus_penalty_w": 0.0,
        "battery_support_mode": "idle",
        "battery_charge_power_w": None,
        "battery_discharge_power_w": None,
        "battery_charge_activity_ratio": None,
        "battery_discharge_activity_ratio": None,
        "battery_learning_profile_count": 0,
        "battery_observed_max_charge_power_w": None,
        "battery_observed_max_discharge_power_w": None,
        "battery_discharge_balance_coordination_policy_enabled": 0,
        "battery_discharge_balance_coordination_support_mode": "supported_only",
        "battery_discharge_balance_coordination_feasibility": "not_needed",
        "battery_discharge_balance_coordination_gate_active": 0,
        "battery_discharge_balance_coordination_start_error_w": 0.0,
        "battery_discharge_balance_coordination_penalty_w": 0.0,
        "battery_discharge_balance_coordination_advisory_active": 0,
        "battery_discharge_balance_coordination_advisory_reason": "",
        "battery_discharge_balance_victron_bias_enabled": 0,
        "battery_discharge_balance_victron_bias_active": 0,
        "battery_discharge_balance_victron_bias_source_id": "",
        "battery_discharge_balance_victron_bias_topology_key": "",
        "battery_discharge_balance_victron_bias_activation_mode": "always",
        "battery_discharge_balance_victron_bias_activation_gate_active": 0,
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
        "battery_discharge_balance_victron_bias_reason": "disabled",
    }


def initialize_victron_balance_runtime_state(svc: Any) -> None:
    svc._victron_ess_balance_pid_last_error_w = 0.0
    svc._victron_ess_balance_pid_last_at = None
    svc._victron_ess_balance_pid_integral_output_w = 0.0
    svc._victron_ess_balance_pid_last_output_w = 0.0
    svc._victron_ess_balance_last_write_at = None
    svc._victron_ess_balance_last_setpoint_w = None
    svc._victron_ess_balance_active_learning_profile_key = ""
    svc._victron_ess_balance_active_learning_profile_action_direction = ""
    svc._victron_ess_balance_active_learning_profile_site_regime = ""
    svc._victron_ess_balance_active_learning_profile_direction = ""
    svc._victron_ess_balance_active_learning_profile_day_phase = ""
    svc._victron_ess_balance_active_learning_profile_reserve_phase = ""
    svc._victron_ess_balance_active_learning_profile_ev_phase = ""
    svc._victron_ess_balance_active_learning_profile_pv_phase = ""
    svc._victron_ess_balance_active_learning_profile_battery_limit_phase = ""
    svc._victron_ess_balance_learning_profiles = {}
    svc._victron_ess_balance_auto_apply_generation = 0
    svc._victron_ess_balance_auto_apply_observe_until = None
    svc._victron_ess_balance_auto_apply_last_applied_param = ""
    svc._victron_ess_balance_auto_apply_last_applied_at = None
    svc._victron_ess_balance_recent_action_changes = []
    svc._victron_ess_balance_last_action_direction = ""
    svc._victron_ess_balance_oscillation_lockout_until = None
    svc._victron_ess_balance_oscillation_lockout_reason = ""
    svc._victron_ess_balance_last_stable_tuning = {}
    svc._victron_ess_balance_last_stable_at = None
    svc._victron_ess_balance_last_stable_profile_key = ""
    svc._victron_ess_balance_conservative_tuning = {}
    svc._victron_ess_balance_auto_apply_suspend_until = None
    svc._victron_ess_balance_auto_apply_suspend_reason = ""
    svc._victron_ess_balance_safe_state_active = False
    svc._victron_ess_balance_safe_state_reason = ""
    svc._victron_ess_balance_telemetry_last_command_at = None
    svc._victron_ess_balance_telemetry_last_command_setpoint_w = None
    svc._victron_ess_balance_telemetry_last_command_error_w = None
    svc._victron_ess_balance_telemetry_last_command_profile_key = ""
    svc._victron_ess_balance_telemetry_command_response_recorded = False
    svc._victron_ess_balance_telemetry_command_overshoot_recorded = False
    svc._victron_ess_balance_telemetry_command_settled_recorded = False
    svc._victron_ess_balance_telemetry_response_delay_seconds = None
    svc._victron_ess_balance_telemetry_delay_samples = 0
    svc._victron_ess_balance_telemetry_estimated_gain = None
    svc._victron_ess_balance_telemetry_gain_samples = 0
    svc._victron_ess_balance_telemetry_last_observed_error_w = None
    svc._victron_ess_balance_telemetry_last_observed_at = None
    svc._victron_ess_balance_telemetry_overshoot_active = False
    svc._victron_ess_balance_telemetry_overshoot_count = 0
    svc._victron_ess_balance_overshoot_cooldown_until = None
    svc._victron_ess_balance_overshoot_cooldown_reason = ""
    svc._victron_ess_balance_telemetry_settling_active = False
    svc._victron_ess_balance_telemetry_settled_count = 0
    svc._victron_ess_balance_telemetry_stability_score = None
    svc._victron_ess_balance_telemetry_last_grid_interaction_w = None
    svc._victron_ess_balance_telemetry_last_ac_power_w = None
    svc._victron_ess_balance_telemetry_last_ev_power_w = None


def initialize_runtime_override_state(svc: Any) -> None:
    svc._runtime_state_serialized = None
    svc._runtime_overrides_serialized = None
    svc._runtime_overrides_last_saved_at = None
    svc._runtime_overrides_pending_serialized = None
    svc._runtime_overrides_pending_values = None
    svc._runtime_overrides_pending_text = None
    svc._runtime_overrides_pending_due_at = None
    svc._runtime_overrides_active = False
    svc._runtime_overrides_values = {}
    svc.runtime_overrides_write_min_interval_seconds = 1.0
    svc._dbus_publish_state = {}
    svc._dbus_live_publish_interval_seconds = 1.0
    svc._dbus_slow_publish_interval_seconds = 5.0
    svc._last_auto_audit_key = None
    svc._last_auto_audit_event_at = None
    svc._last_auto_audit_cleanup_at = 0.0


def empty_worker_snapshot() -> WorkerSnapshot:
    return {
        "captured_at": 0.0,
        "pm_captured_at": None,
        "pm_status": None,
        "pm_confirmed": False,
        "pv_captured_at": None,
        "pv_power": None,
        "battery_captured_at": None,
        "battery_soc": None,
        "battery_combined_soc": None,
        "battery_combined_usable_capacity_wh": None,
        "battery_combined_charge_power_w": None,
        "battery_combined_discharge_power_w": None,
        "battery_combined_net_power_w": None,
        "battery_combined_ac_power_w": None,
        "battery_headroom_charge_w": None,
        "battery_headroom_discharge_w": None,
        "expected_near_term_export_w": None,
        "expected_near_term_import_w": None,
        "battery_discharge_balance_mode": "",
        "battery_discharge_balance_target_distribution_mode": "",
        "battery_discharge_balance_error_w": None,
        "battery_discharge_balance_max_abs_error_w": None,
        "battery_discharge_balance_total_discharge_w": None,
        "battery_discharge_balance_eligible_source_count": 0,
        "battery_discharge_balance_active_source_count": 0,
        "battery_discharge_balance_control_candidate_count": 0,
        "battery_discharge_balance_control_ready_count": 0,
        "battery_discharge_balance_supported_control_source_count": 0,
        "battery_discharge_balance_experimental_control_source_count": 0,
        "battery_source_count": 0,
        "battery_online_source_count": 0,
        "battery_valid_soc_source_count": 0,
        "battery_sources": [],
        "battery_learning_profiles": {},
        "grid_captured_at": None,
        "grid_power": None,
        "auto_mode_active": False,
    }


def clone_worker_status_payload(snapshot: WorkerSnapshot) -> None:
    pm_status = snapshot.get("pm_status")
    if isinstance(pm_status, dict):
        snapshot["pm_status"] = dict(pm_status)


def clone_worker_battery_sources_payload(snapshot: WorkerSnapshot) -> None:
    battery_sources = snapshot.get("battery_sources")
    if isinstance(battery_sources, list):
        snapshot["battery_sources"] = [
            dict(item) if isinstance(item, dict) else item
            for item in battery_sources
        ]


def clone_worker_learning_profiles_payload(snapshot: WorkerSnapshot) -> None:
    learning_profiles = snapshot.get("battery_learning_profiles")
    if isinstance(learning_profiles, dict):
        snapshot["battery_learning_profiles"] = {
            str(key): dict(value) if isinstance(value, dict) else value
            for key, value in learning_profiles.items()
        }
