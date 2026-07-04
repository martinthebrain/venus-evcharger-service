# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for Control API operational state payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.contracts import finite_float_or_none, sanitized_auto_metrics
from venus_evcharger.energy import summarize_energy_learning_profiles


def _worker_snapshot(owner: Any) -> dict[str, Any]:
    if not hasattr(owner, "_get_worker_snapshot"):
        return {}
    get_worker_snapshot = getattr(owner, "_get_worker_snapshot")
    raw_worker_snapshot = get_worker_snapshot() if callable(get_worker_snapshot) else {}
    if not isinstance(raw_worker_snapshot, Mapping):
        return {}
    return {str(key): value for key, value in raw_worker_snapshot.items()}


def _worker_learning_summary(worker_snapshot: dict[str, Any]) -> dict[str, Any]:
    learning_profiles = worker_snapshot.get("battery_learning_profiles")
    return summarize_energy_learning_profiles(learning_profiles if isinstance(learning_profiles, dict) else {})


def _state_api_operational_energy_state(
    worker_snapshot: dict[str, Any],
    learning_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "combined_battery_soc": worker_snapshot.get("battery_combined_soc"),
        "combined_battery_source_count": _mapping_value_or_default(worker_snapshot, "battery_source_count", 0),
        "combined_battery_online_source_count": _mapping_value_or_default(
            worker_snapshot,
            "battery_online_source_count",
            0,
        ),
        "combined_battery_charge_power_w": worker_snapshot.get("battery_combined_charge_power_w"),
        "combined_battery_discharge_power_w": worker_snapshot.get("battery_combined_discharge_power_w"),
        "combined_battery_net_power_w": worker_snapshot.get("battery_combined_net_power_w"),
        "combined_battery_ac_power_w": worker_snapshot.get("battery_combined_ac_power_w"),
        "combined_battery_pv_input_power_w": worker_snapshot.get("battery_combined_pv_input_power_w"),
        "combined_battery_grid_interaction_w": worker_snapshot.get("battery_combined_grid_interaction_w"),
        "combined_battery_headroom_charge_w": worker_snapshot.get("battery_headroom_charge_w"),
        "combined_battery_headroom_discharge_w": worker_snapshot.get("battery_headroom_discharge_w"),
        "expected_near_term_export_w": worker_snapshot.get("expected_near_term_export_w"),
        "expected_near_term_import_w": worker_snapshot.get("expected_near_term_import_w"),
        "combined_battery_average_confidence": worker_snapshot.get("battery_average_confidence"),
        "combined_battery_battery_source_count": _mapping_value_or_default(
            worker_snapshot,
            "battery_battery_source_count",
            0,
        ),
        "combined_battery_hybrid_inverter_source_count": _mapping_value_or_default(
            worker_snapshot,
            "battery_hybrid_inverter_source_count",
            0,
        ),
        "combined_battery_inverter_source_count": _mapping_value_or_default(
            worker_snapshot,
            "battery_inverter_source_count",
            0,
        ),
        "combined_battery_learning_profile_count": _mapping_value_or_default(learning_summary, "profile_count", 0),
        "combined_battery_observed_max_charge_power_w": learning_summary.get("observed_max_charge_power_w"),
        "combined_battery_observed_max_discharge_power_w": learning_summary.get("observed_max_discharge_power_w"),
        "combined_battery_observed_max_ac_power_w": learning_summary.get("observed_max_ac_power_w"),
        "combined_battery_observed_max_pv_input_power_w": learning_summary.get("observed_max_pv_input_power_w"),
        "combined_battery_observed_max_grid_import_w": learning_summary.get("observed_max_grid_import_w"),
        "combined_battery_observed_max_grid_export_w": learning_summary.get("observed_max_grid_export_w"),
        "combined_battery_average_active_charge_power_w": learning_summary.get("average_active_charge_power_w"),
        "combined_battery_average_active_discharge_power_w": learning_summary.get("average_active_discharge_power_w"),
        "combined_battery_average_active_power_delta_w": learning_summary.get("average_active_power_delta_w"),
        "combined_battery_power_smoothing_ratio": learning_summary.get("power_smoothing_ratio"),
        "combined_battery_typical_response_delay_seconds": learning_summary.get("typical_response_delay_seconds"),
        "combined_battery_support_bias": learning_summary.get("support_bias"),
        "combined_battery_day_support_bias": learning_summary.get("day_support_bias"),
        "combined_battery_night_support_bias": learning_summary.get("night_support_bias"),
        "combined_battery_import_support_bias": learning_summary.get("import_support_bias"),
        "combined_battery_export_bias": learning_summary.get("export_bias"),
        "combined_battery_battery_first_export_bias": learning_summary.get("battery_first_export_bias"),
        "combined_battery_reserve_band_floor_soc": learning_summary.get("reserve_band_floor_soc"),
        "combined_battery_reserve_band_ceiling_soc": learning_summary.get("reserve_band_ceiling_soc"),
        "combined_battery_reserve_band_width_soc": learning_summary.get("reserve_band_width_soc"),
        "combined_battery_direction_change_count": _mapping_value_or_default(
            learning_summary,
            "direction_change_count",
            0,
        ),
        "combined_battery_learning_summary": learning_summary,
    }


def _state_api_operational_auto_decision_state(
    owner: Any,
    last_auto_metrics: dict[str, Any],
    auto_state: object,
    auto_state_code: object,
) -> dict[str, Any]:
    metrics = sanitized_auto_metrics(last_auto_metrics)
    return {
        "auto_decision": {
            "reason": _owner_health_reason(owner),
            "state": auto_state,
            "state_code": auto_state_code,
            "relay_intent": _relay_intent_value(metrics.get("relay_intent")),
            "surplus_watts": finite_float_or_none(metrics.get("surplus")),
            "grid_watts": finite_float_or_none(metrics.get("grid")),
            "soc_percent": finite_float_or_none(metrics.get("soc")),
            "start_threshold_watts": finite_float_or_none(metrics.get("start_threshold")),
            "stop_threshold_watts": finite_float_or_none(metrics.get("stop_threshold")),
            "profile": _optional_metric_text(metrics.get("profile")),
            "threshold_mode": _optional_metric_text(metrics.get("threshold_mode")),
        }
    }


def _relay_intent_value(value: object) -> int:
    if value is None:
        return -1
    return int(bool(value))


def _optional_metric_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _owner_health_reason(owner: Any) -> str:
    if not hasattr(owner, "_last_health_reason"):
        return "na"
    value = getattr(owner, "_last_health_reason")
    return str(value) if value else "na"


def _owner_bool_attr(owner: Any, name: str, default: bool) -> bool:
    if not hasattr(owner, name):
        return default
    return bool(getattr(owner, name))


_BALANCE_WORKER_VALUE_FIELDS = (
    "battery_discharge_balance_mode",
    "battery_discharge_balance_target_distribution_mode",
    "battery_discharge_balance_error_w",
    "battery_discharge_balance_max_abs_error_w",
    "battery_discharge_balance_total_discharge_w",
)

_BALANCE_WORKER_ZERO_FIELDS = (
    "battery_discharge_balance_eligible_source_count",
    "battery_discharge_balance_active_source_count",
    "battery_discharge_balance_control_candidate_count",
    "battery_discharge_balance_control_ready_count",
    "battery_discharge_balance_supported_control_source_count",
    "battery_discharge_balance_experimental_control_source_count",
)

_BALANCE_METRIC_VALUE_FIELDS = (
    "battery_discharge_balance_warning_error_w",
    "battery_discharge_balance_warn_threshold_w",
    "battery_discharge_balance_bias_mode",
    "battery_discharge_balance_bias_start_error_w",
    "battery_discharge_balance_bias_penalty_w",
    "battery_discharge_balance_coordination_support_mode",
    "battery_discharge_balance_coordination_feasibility",
    "battery_discharge_balance_coordination_start_error_w",
    "battery_discharge_balance_coordination_penalty_w",
    "battery_discharge_balance_coordination_advisory_reason",
)

_BALANCE_METRIC_BOOL_FIELDS = (
    "battery_discharge_balance_warning_active",
    "battery_discharge_balance_bias_gate_active",
    "battery_discharge_balance_coordination_policy_enabled",
    "battery_discharge_balance_coordination_gate_active",
    "battery_discharge_balance_coordination_advisory_active",
)

_VICTRON_BIAS_BOOL_FIELDS = (
    "battery_discharge_balance_victron_bias_enabled",
    "battery_discharge_balance_victron_bias_active",
    "battery_discharge_balance_victron_bias_activation_gate_active",
    "battery_discharge_balance_victron_bias_telemetry_clean",
    "battery_discharge_balance_victron_bias_overshoot_active",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_active",
    "battery_discharge_balance_victron_bias_settling_active",
    "battery_discharge_balance_victron_bias_oscillation_lockout_enabled",
    "battery_discharge_balance_victron_bias_oscillation_lockout_active",
    "battery_discharge_balance_victron_bias_auto_apply_enabled",
    "battery_discharge_balance_victron_bias_auto_apply_active",
    "battery_discharge_balance_victron_bias_auto_apply_observation_window_active",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_active",
    "battery_discharge_balance_victron_bias_rollback_enabled",
    "battery_discharge_balance_victron_bias_rollback_active",
    "battery_discharge_balance_victron_bias_safe_state_active",
)

_VICTRON_BIAS_VALUE_FIELDS = (
    "battery_discharge_balance_victron_bias_source_id",
    "battery_discharge_balance_victron_bias_topology_key",
    "battery_discharge_balance_victron_bias_activation_mode",
    "battery_discharge_balance_victron_bias_support_mode",
    "battery_discharge_balance_victron_bias_learning_profile_key",
    "battery_discharge_balance_victron_bias_learning_profile_action_direction",
    "battery_discharge_balance_victron_bias_learning_profile_site_regime",
    "battery_discharge_balance_victron_bias_learning_profile_direction",
    "battery_discharge_balance_victron_bias_learning_profile_day_phase",
    "battery_discharge_balance_victron_bias_learning_profile_reserve_phase",
    "battery_discharge_balance_victron_bias_learning_profile_ev_phase",
    "battery_discharge_balance_victron_bias_learning_profile_pv_phase",
    "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase",
    "battery_discharge_balance_victron_bias_learning_profile_sample_count",
    "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds",
    "battery_discharge_balance_victron_bias_learning_profile_estimated_gain",
    "battery_discharge_balance_victron_bias_learning_profile_overshoot_count",
    "battery_discharge_balance_victron_bias_learning_profile_settled_count",
    "battery_discharge_balance_victron_bias_learning_profile_stability_score",
    "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score",
    "battery_discharge_balance_victron_bias_learning_profile_response_variance_score",
    "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score",
    "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second",
    "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts",
    "battery_discharge_balance_victron_bias_source_error_w",
    "battery_discharge_balance_victron_bias_pid_output_w",
    "battery_discharge_balance_victron_bias_setpoint_w",
    "battery_discharge_balance_victron_bias_telemetry_clean_reason",
    "battery_discharge_balance_victron_bias_response_delay_seconds",
    "battery_discharge_balance_victron_bias_estimated_gain",
    "battery_discharge_balance_victron_bias_overshoot_count",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_reason",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_until",
    "battery_discharge_balance_victron_bias_settled_count",
    "battery_discharge_balance_victron_bias_stability_score",
    "battery_discharge_balance_victron_bias_oscillation_lockout_reason",
    "battery_discharge_balance_victron_bias_oscillation_lockout_until",
    "battery_discharge_balance_victron_bias_oscillation_direction_change_count",
    "battery_discharge_balance_victron_bias_recommended_kp",
    "battery_discharge_balance_victron_bias_recommended_ki",
    "battery_discharge_balance_victron_bias_recommended_kd",
    "battery_discharge_balance_victron_bias_recommended_deadband_watts",
    "battery_discharge_balance_victron_bias_recommended_max_abs_watts",
    "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second",
    "battery_discharge_balance_victron_bias_recommended_activation_mode",
    "battery_discharge_balance_victron_bias_recommendation_confidence",
    "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score",
    "battery_discharge_balance_victron_bias_recommendation_response_variance_score",
    "battery_discharge_balance_victron_bias_recommendation_reproducibility_score",
    "battery_discharge_balance_victron_bias_recommendation_reason",
    "battery_discharge_balance_victron_bias_recommendation_profile_key",
    "battery_discharge_balance_victron_bias_recommendation_ini_snippet",
    "battery_discharge_balance_victron_bias_recommendation_hint",
    "battery_discharge_balance_victron_bias_auto_apply_reason",
    "battery_discharge_balance_victron_bias_auto_apply_generation",
    "battery_discharge_balance_victron_bias_auto_apply_observation_window_until",
    "battery_discharge_balance_victron_bias_auto_apply_last_param",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_reason",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_until",
    "battery_discharge_balance_victron_bias_rollback_reason",
    "battery_discharge_balance_victron_bias_rollback_stable_profile_key",
    "battery_discharge_balance_victron_bias_safe_state_reason",
    "battery_discharge_balance_victron_bias_reason",
)


def _mapping_values(source: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields}


def _mapping_value_or_default(source: Mapping[str, Any], field: str, default: object) -> object:
    return source[field] if field in source else default


def _mapping_values_with_default(
    source: Mapping[str, Any],
    fields: tuple[str, ...],
    default: object,
) -> dict[str, Any]:
    return {field: _mapping_value_or_default(source, field, default) for field in fields}


def _mapping_bool_values(source: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, bool]:
    return {field: bool(source.get(field)) for field in fields}


def _state_api_operational_balance_state(
    owner: Any,
    worker_snapshot: dict[str, Any],
    last_auto_metrics: dict[str, Any],
) -> dict[str, Any]:
    state = _mapping_values(worker_snapshot, _BALANCE_WORKER_VALUE_FIELDS)
    state.update(_mapping_values_with_default(worker_snapshot, _BALANCE_WORKER_ZERO_FIELDS, 0))
    state.update(_mapping_values(last_auto_metrics, _BALANCE_METRIC_VALUE_FIELDS))
    state.update(_mapping_bool_values(last_auto_metrics, _BALANCE_METRIC_BOOL_FIELDS))
    state["battery_discharge_balance_policy_enabled"] = _owner_bool_attr(
        owner,
        "auto_battery_discharge_balance_policy_enabled",
        False,
    )
    return state


def _state_api_operational_victron_bias_state(last_auto_metrics: dict[str, Any]) -> dict[str, Any]:
    state = _mapping_values(last_auto_metrics, _VICTRON_BIAS_VALUE_FIELDS)
    state.update(_mapping_bool_values(last_auto_metrics, _VICTRON_BIAS_BOOL_FIELDS))
    return state
