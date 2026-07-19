# SPDX-License-Identifier: GPL-3.0-or-later
"""Operational local State API contract helpers."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from venus_evcharger.core.contracts_basic import (
    finite_float_or_none,
    non_negative_float_or_none,
    non_negative_int,
    normalize_binary_flag,
    normalized_auto_state_pair,
)
from venus_evcharger.core.contracts_outward import normalized_software_update_state_fields
from venus_evcharger.core.contracts_state_shared import (
    _normalized_text,
    _optional_float,
    normalized_state_api_kind,
)


def normalized_state_api_operational_decision_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state, state_code = normalized_auto_state_pair(raw.get("state"), None)
    return {
        "reason": _normalized_text(raw.get("reason"), "na"),
        "state": state,
        "state_code": state_code,
        "relay_intent": _normalized_relay_intent(raw.get("relay_intent")),
        "surplus_watts": finite_float_or_none(raw.get("surplus_watts")),
        "grid_watts": finite_float_or_none(raw.get("grid_watts")),
        "soc_percent": finite_float_or_none(raw.get("soc_percent")),
        "start_threshold_watts": finite_float_or_none(raw.get("start_threshold_watts")),
        "stop_threshold_watts": finite_float_or_none(raw.get("stop_threshold_watts")),
        "profile": _normalized_text(raw.get("profile")),
        "threshold_mode": _normalized_text(raw.get("threshold_mode")),
    }


def _normalized_relay_intent(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return -1
    text = str(value).strip()
    if text in {"0", "1"}:
        return int(text)
    return -1


_TEXT_DEFAULT_FIELDS: tuple[tuple[str, str], ...] = (
    ("active_phase_selection", "P1"),
    ("requested_phase_selection", "P1"),
    ("backend_mode", "combined"),
    ("meter_backend", "na"),
    ("switch_backend", "na"),
    ("charger_backend", "na"),
    ("fault_reason", "na"),
)

_TEXT_FIELDS = (
    "runtime_overrides_path",
    "battery_discharge_balance_mode",
    "battery_discharge_balance_target_distribution_mode",
    "battery_discharge_balance_bias_mode",
    "battery_discharge_balance_coordination_support_mode",
    "battery_discharge_balance_coordination_feasibility",
    "battery_discharge_balance_coordination_advisory_reason",
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
    "battery_discharge_balance_victron_bias_telemetry_clean_reason",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_reason",
    "battery_discharge_balance_victron_bias_oscillation_lockout_reason",
    "battery_discharge_balance_victron_bias_recommended_activation_mode",
    "battery_discharge_balance_victron_bias_recommendation_reason",
    "battery_discharge_balance_victron_bias_recommendation_profile_key",
    "battery_discharge_balance_victron_bias_recommendation_ini_snippet",
    "battery_discharge_balance_victron_bias_recommendation_hint",
    "battery_discharge_balance_victron_bias_auto_apply_reason",
    "battery_discharge_balance_victron_bias_auto_apply_last_param",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_reason",
    "battery_discharge_balance_victron_bias_rollback_reason",
    "battery_discharge_balance_victron_bias_rollback_stable_profile_key",
    "battery_discharge_balance_victron_bias_safe_state_reason",
    "battery_discharge_balance_victron_bias_reason",
)

_BINARY_FIELDS = (
    "enable",
    "startstop",
    "autostart",
    "fault_active",
    "runtime_overrides_active",
    "battery_discharge_balance_policy_enabled",
    "battery_discharge_balance_warning_active",
    "battery_discharge_balance_bias_gate_active",
    "battery_discharge_balance_coordination_policy_enabled",
    "battery_discharge_balance_coordination_gate_active",
    "battery_discharge_balance_coordination_advisory_active",
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

_NON_NEGATIVE_INT_FIELDS = (
    "mode",
    "combined_battery_source_count",
    "combined_battery_online_source_count",
    "battery_discharge_balance_eligible_source_count",
    "battery_discharge_balance_active_source_count",
    "battery_discharge_balance_control_candidate_count",
    "battery_discharge_balance_control_ready_count",
    "battery_discharge_balance_supported_control_source_count",
    "battery_discharge_balance_experimental_control_source_count",
    "battery_discharge_balance_victron_bias_learning_profile_sample_count",
    "battery_discharge_balance_victron_bias_learning_profile_overshoot_count",
    "battery_discharge_balance_victron_bias_learning_profile_settled_count",
    "battery_discharge_balance_victron_bias_overshoot_count",
    "battery_discharge_balance_victron_bias_settled_count",
    "battery_discharge_balance_victron_bias_oscillation_direction_change_count",
    "battery_discharge_balance_victron_bias_auto_apply_generation",
    "combined_battery_battery_source_count",
    "combined_battery_hybrid_inverter_source_count",
    "combined_battery_inverter_source_count",
    "combined_battery_learning_profile_count",
    "combined_battery_direction_change_count",
)

_NON_NEGATIVE_FLOAT_FIELDS = (
    "combined_battery_soc",
    "combined_battery_charge_power_w",
    "combined_battery_discharge_power_w",
    "combined_battery_pv_input_power_w",
    "combined_battery_headroom_charge_w",
    "combined_battery_headroom_discharge_w",
    "expected_near_term_export_w",
    "expected_near_term_import_w",
    "battery_discharge_balance_error_w",
    "battery_discharge_balance_max_abs_error_w",
    "battery_discharge_balance_total_discharge_w",
    "battery_discharge_balance_warning_error_w",
    "battery_discharge_balance_warn_threshold_w",
    "battery_discharge_balance_bias_start_error_w",
    "battery_discharge_balance_bias_penalty_w",
    "battery_discharge_balance_coordination_start_error_w",
    "battery_discharge_balance_coordination_penalty_w",
    "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds",
    "battery_discharge_balance_victron_bias_learning_profile_estimated_gain",
    "battery_discharge_balance_victron_bias_learning_profile_stability_score",
    "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score",
    "battery_discharge_balance_victron_bias_learning_profile_response_variance_score",
    "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score",
    "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second",
    "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts",
    "battery_discharge_balance_victron_bias_response_delay_seconds",
    "battery_discharge_balance_victron_bias_estimated_gain",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_until",
    "battery_discharge_balance_victron_bias_stability_score",
    "battery_discharge_balance_victron_bias_oscillation_lockout_until",
    "battery_discharge_balance_victron_bias_recommended_kp",
    "battery_discharge_balance_victron_bias_recommended_ki",
    "battery_discharge_balance_victron_bias_recommended_kd",
    "battery_discharge_balance_victron_bias_recommended_deadband_watts",
    "battery_discharge_balance_victron_bias_recommended_max_abs_watts",
    "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second",
    "battery_discharge_balance_victron_bias_recommendation_confidence",
    "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score",
    "battery_discharge_balance_victron_bias_recommendation_response_variance_score",
    "battery_discharge_balance_victron_bias_recommendation_reproducibility_score",
    "battery_discharge_balance_victron_bias_auto_apply_observation_window_until",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_until",
    "combined_battery_average_confidence",
    "combined_battery_observed_max_charge_power_w",
    "combined_battery_observed_max_discharge_power_w",
    "combined_battery_observed_max_ac_power_w",
    "combined_battery_observed_max_pv_input_power_w",
    "combined_battery_observed_max_grid_import_w",
    "combined_battery_observed_max_grid_export_w",
    "combined_battery_average_active_charge_power_w",
    "combined_battery_average_active_discharge_power_w",
    "combined_battery_average_active_power_delta_w",
    "combined_battery_power_smoothing_ratio",
    "combined_battery_typical_response_delay_seconds",
    "combined_battery_reserve_band_floor_soc",
    "combined_battery_reserve_band_ceiling_soc",
    "combined_battery_reserve_band_width_soc",
)

_OPTIONAL_FLOAT_FIELDS = (
    "combined_battery_net_power_w",
    "combined_battery_ac_power_w",
    "combined_battery_grid_interaction_w",
    "combined_battery_support_bias",
    "combined_battery_day_support_bias",
    "combined_battery_night_support_bias",
    "combined_battery_import_support_bias",
    "combined_battery_export_bias",
    "combined_battery_battery_first_export_bias",
)

_FINITE_FLOAT_FIELDS = (
    "battery_discharge_balance_victron_bias_source_error_w",
    "battery_discharge_balance_victron_bias_pid_output_w",
    "battery_discharge_balance_victron_bias_setpoint_w",
)


def _normalized_field_values(
    raw: Mapping[str, Any],
    fields: tuple[str, ...],
    normalizer: Callable[[Any], Any],
) -> dict[str, Any]:
    return {field: normalizer(raw.get(field)) for field in fields}


def _normalized_text_default_values(raw: Mapping[str, Any]) -> dict[str, str]:
    return {field: _normalized_text(raw.get(field), default) for field, default in _TEXT_DEFAULT_FIELDS}


def _normalized_operational_special_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    auto_state, auto_state_code = normalized_auto_state_pair(raw.get("auto_state"), None)
    software_update_state, software_update_state_code, software_update_available, software_update_no_update_active = (
        normalized_software_update_state_fields(
            raw.get("software_update_state"),
            raw.get("software_update_available"),
            raw.get("software_update_no_update_active"),
        )
    )
    auto_decision = raw.get("auto_decision")
    return {
        "auto_state": auto_state,
        "auto_state_code": auto_state_code,
        "software_update_state": software_update_state,
        "software_update_state_code": software_update_state_code,
        "software_update_available": software_update_available,
        "software_update_no_update_active": software_update_no_update_active,
        "auto_decision": normalized_state_api_operational_decision_fields(
            auto_decision if isinstance(auto_decision, Mapping) else {}
        ),
    }


def normalized_state_api_operational_state_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = _normalized_operational_special_fields(raw)
    state.update(_normalized_text_default_values(raw))
    state.update(_normalized_field_values(raw, _TEXT_FIELDS, _normalized_text))
    state.update(_normalized_field_values(raw, _BINARY_FIELDS, normalize_binary_flag))
    state.update(_normalized_field_values(raw, _NON_NEGATIVE_INT_FIELDS, non_negative_int))
    state.update(_normalized_field_values(raw, _NON_NEGATIVE_FLOAT_FIELDS, non_negative_float_or_none))
    state.update(_normalized_field_values(raw, _OPTIONAL_FLOAT_FIELDS, _optional_float))
    state.update(_normalized_field_values(raw, _FINITE_FLOAT_FIELDS, finite_float_or_none))
    return state


def normalized_state_api_operational_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", True))),
        "api_version": "v1",
        "kind": normalized_state_api_kind(raw.get("kind"), default="operational"),
        "state": normalized_state_api_operational_state_fields(state if isinstance(state, Mapping) else {}),
    }
