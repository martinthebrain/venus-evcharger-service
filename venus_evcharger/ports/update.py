# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any


from venus_evcharger.core.contracts import (
    non_negative_int,
    normalize_binary_flag,
    normalize_learning_state,
    normalize_optional_binary_state,
    normalized_auto_state_pair,
)

from .base import _BaseServicePort

class UpdateCyclePort(_BaseServicePort):
    """Expose the update-cycle surface needed by ``UpdateCycleController``."""

    _ALLOWED_ATTRS = {
        "_startup_manual_target",
        "auto_policy",
        "virtual_mode",
        "auto_shelly_soft_fail_seconds",
        "auto_contactor_fault_latch_count",
        "auto_contactor_fault_latch_seconds",
        "_last_health_reason",
        "_last_health_code",
        "_last_auto_state",
        "_last_auto_state_code",
        "_last_status_source",
        "_last_charger_fault_active",
        "_last_charger_transport_reason",
        "_last_charger_transport_source",
        "_last_charger_transport_detail",
        "_last_charger_transport_at",
        "_charger_retry_reason",
        "_charger_retry_source",
        "_charger_retry_until",
        "charging_started_at",
        "energy_at_start",
        "virtual_startstop",
        "virtual_enable",
        "phase",
        "voltage_mode",
        "max_current",
        "virtual_set_current",
        "last_status",
        "_last_pm_status",
        "_last_pm_status_at",
        "_last_pm_status_confirmed",
        "_last_confirmed_pm_status",
        "_last_confirmed_pm_status_at",
        "_last_voltage",
        "auto_input_cache_seconds",
        "_auto_cached_inputs_used",
        "_error_state",
        "_last_pv_value",
        "_last_pv_at",
        "_last_grid_value",
        "_last_grid_at",
        "_last_battery_soc_value",
        "_last_battery_soc_at",
        "_last_energy_cluster",
        "auto_audit_log",
        "_last_auto_metrics",
        "charging_threshold_watts",
        "idle_status",
        "min_current",
        "_last_successful_update_at",
        "_last_recovery_attempt_at",
        "last_update",
        "_contactor_fault_counts",
        "_contactor_fault_active_reason",
        "_contactor_fault_active_since",
        "_contactor_lockout_reason",
        "_contactor_lockout_source",
        "_contactor_lockout_at",
        "service_name",
        "_dbusservice",
        "learned_charge_power_watts",
        "learned_charge_power_updated_at",
        "learned_charge_power_state",
        "learned_charge_power_learning_since",
        "learned_charge_power_sample_count",
        "learned_charge_power_phase",
        "learned_charge_power_voltage",
        "learned_charge_power_signature_mismatch_sessions",
        "learned_charge_power_signature_checked_session_started_at",
        "learned_charge_power_confidence",
        "learned_charge_power_stability_score",
        "learned_charge_power_reason",
        "learned_charge_power_detail",
        "auto_learn_charge_power_enabled",
        "auto_learn_charge_power_min_watts",
        "auto_learn_charge_power_alpha",
        "auto_learn_charge_power_start_delay_seconds",
        "auto_learn_charge_power_window_seconds",
        "auto_learn_charge_power_max_age_seconds",
        "relay_sync_timeout_seconds",
        "_relay_sync_expected_state",
        "_relay_sync_requested_at",
        "_relay_sync_deadline_at",
        "_relay_sync_failure_reported",
        "_ignore_min_offtime_once",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "auto_stop_condition_reason",
        "auto_month_windows",
        "auto_scheduled_enabled_days",
        "auto_scheduled_night_start_delay_seconds",
        "auto_scheduled_latest_end_time",
        "auto_scheduled_night_current_amps",
        "requested_phase_selection",
        "active_phase_selection",
        "supported_phase_selections",
        "_phase_switch_pending_selection",
        "_phase_switch_state",
        "_phase_switch_requested_at",
        "_phase_switch_stable_until",
        "_phase_switch_resume_relay",
        "_phase_switch_mismatch_active",
        "_phase_switch_mismatch_counts",
        "_phase_switch_last_mismatch_selection",
        "_phase_switch_last_mismatch_at",
        "_phase_switch_lockout_selection",
        "_phase_switch_lockout_reason",
        "_phase_switch_lockout_at",
        "_phase_switch_lockout_until",
        "_auto_phase_target_candidate",
        "_auto_phase_target_since",
        "software_update_repo_root",
        "software_update_install_script",
        "software_update_restart_script",
        "software_update_no_update_file",
        "software_update_log_path",
        "software_update_repo_slug",
        "software_update_channel",
        "software_update_manifest_source",
        "software_update_version_source",
        "_software_update_current_version",
        "_software_update_available_version",
        "_software_update_available",
        "_software_update_state",
        "_software_update_detail",
        "_software_update_last_check_at",
        "_software_update_last_run_at",
        "_software_update_last_result",
        "_software_update_process",
        "_software_update_process_log_handle",
        "_software_update_run_requested_at",
        "_software_update_no_update_active",
        "_software_update_next_check_at",
        "_software_update_boot_auto_due_at",
        "auto_battery_discharge_balance_victron_bias_enabled",
        "auto_battery_discharge_balance_victron_bias_source_id",
        "auto_battery_discharge_balance_victron_bias_service",
        "auto_battery_discharge_balance_victron_bias_path",
        "auto_battery_discharge_balance_victron_bias_base_setpoint_watts",
        "auto_battery_discharge_balance_victron_bias_deadband_watts",
        "auto_battery_discharge_balance_victron_bias_activation_mode",
        "auto_battery_discharge_balance_victron_bias_support_mode",
        "auto_battery_discharge_balance_victron_bias_kp",
        "auto_battery_discharge_balance_victron_bias_ki",
        "auto_battery_discharge_balance_victron_bias_kd",
        "auto_battery_discharge_balance_victron_bias_integral_limit_watts",
        "auto_battery_discharge_balance_victron_bias_max_abs_watts",
        "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
        "auto_battery_discharge_balance_victron_bias_min_update_seconds",
        "auto_battery_discharge_balance_victron_bias_auto_apply_enabled",
        "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence",
        "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples",
        "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score",
        "auto_battery_discharge_balance_victron_bias_auto_apply_blend",
        "auto_battery_discharge_balance_victron_bias_observation_window_seconds",
        "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled",
        "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds",
        "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes",
        "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds",
        "auto_battery_discharge_balance_victron_bias_rollback_enabled",
        "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score",
        "auto_battery_discharge_balance_victron_bias_require_clean_phases",
        "_victron_ess_balance_pid_last_error_w",
        "_victron_ess_balance_pid_last_at",
        "_victron_ess_balance_pid_integral_output_w",
        "_victron_ess_balance_pid_last_output_w",
        "_victron_ess_balance_last_write_at",
        "_victron_ess_balance_last_setpoint_w",
        "_victron_ess_balance_active_learning_profile_key",
        "_victron_ess_balance_active_learning_profile_action_direction",
        "_victron_ess_balance_active_learning_profile_site_regime",
        "_victron_ess_balance_active_learning_profile_direction",
        "_victron_ess_balance_active_learning_profile_day_phase",
        "_victron_ess_balance_active_learning_profile_reserve_phase",
        "_victron_ess_balance_learning_profiles",
        "_victron_ess_balance_auto_apply_generation",
        "_victron_ess_balance_auto_apply_observe_until",
        "_victron_ess_balance_auto_apply_last_applied_param",
        "_victron_ess_balance_auto_apply_last_applied_at",
        "_victron_ess_balance_recent_action_changes",
        "_victron_ess_balance_last_action_direction",
        "_victron_ess_balance_oscillation_lockout_until",
        "_victron_ess_balance_oscillation_lockout_reason",
        "_victron_ess_balance_last_stable_tuning",
        "_victron_ess_balance_last_stable_at",
        "_victron_ess_balance_last_stable_profile_key",
        "_victron_ess_balance_telemetry_last_command_at",
        "_victron_ess_balance_telemetry_last_command_setpoint_w",
        "_victron_ess_balance_telemetry_last_command_error_w",
        "_victron_ess_balance_telemetry_last_command_profile_key",
        "_victron_ess_balance_telemetry_command_response_recorded",
        "_victron_ess_balance_telemetry_command_overshoot_recorded",
        "_victron_ess_balance_telemetry_command_settled_recorded",
        "_victron_ess_balance_telemetry_response_delay_seconds",
        "_victron_ess_balance_telemetry_delay_samples",
        "_victron_ess_balance_telemetry_estimated_gain",
        "_victron_ess_balance_telemetry_gain_samples",
        "_victron_ess_balance_telemetry_last_observed_error_w",
        "_victron_ess_balance_telemetry_last_observed_at",
        "_victron_ess_balance_telemetry_overshoot_active",
        "_victron_ess_balance_telemetry_overshoot_count",
        "_victron_ess_balance_telemetry_settling_active",
        "_victron_ess_balance_telemetry_settled_count",
        "_victron_ess_balance_telemetry_stability_score",
        "dbus_method_timeout_seconds",
    }

    _ALLOWED_METHODS = {
        "_mode_uses_auto_logic",
        "_queue_relay_command",
        "_mark_failure",
        "_mark_recovery",
        "_warning_throttled",
        "_ensure_observability_state",
        "_publish_energy_time_measurements",
        "_publish_config_paths",
        "_publish_diagnostic_paths",
        "_time_now",
        "_save_runtime_state",
        "_watchdog_recover",
        "_ensure_auto_input_helper_process",
        "_source_retry_ready",
        "_source_retry_remaining",
        "_delay_source_retry",
        "_refresh_auto_input_snapshot",
        "_get_worker_snapshot",
        "_set_health",
        "_publish_live_measurements",
        "_bump_update_index",
        "_safe_float",
        "_publish_local_pm_status",
        "_auto_decide_relay",
        "_state_summary",
        "_peek_pending_relay_command",
        "_apply_phase_selection",
        "_phase_selection_requires_pause",
        "_get_system_bus",
        "_reset_system_bus",
    }

    _MUTABLE_ATTRS = {
        "_startup_manual_target",
        "_last_health_reason",
        "_last_health_code",
        "_last_auto_state",
        "_last_auto_state_code",
        "_last_status_source",
        "_last_charger_fault_active",
        "_last_charger_transport_reason",
        "_last_charger_transport_source",
        "_last_charger_transport_detail",
        "_last_charger_transport_at",
        "_charger_retry_reason",
        "_charger_retry_source",
        "_charger_retry_until",
        "charging_started_at",
        "energy_at_start",
        "virtual_startstop",
        "last_status",
        "_last_pm_status",
        "_last_pm_status_at",
        "_last_pm_status_confirmed",
        "_last_confirmed_pm_status",
        "_last_confirmed_pm_status_at",
        "_last_voltage",
        "_auto_cached_inputs_used",
        "_last_pv_value",
        "_last_pv_at",
        "_last_grid_value",
        "_last_grid_at",
        "_last_battery_soc_value",
        "_last_battery_soc_at",
        "_last_successful_update_at",
        "_last_recovery_attempt_at",
        "last_update",
        "_contactor_fault_counts",
        "_contactor_fault_active_reason",
        "_contactor_fault_active_since",
        "_contactor_lockout_reason",
        "_contactor_lockout_source",
        "_contactor_lockout_at",
        "learned_charge_power_watts",
        "learned_charge_power_updated_at",
        "learned_charge_power_state",
        "learned_charge_power_learning_since",
        "learned_charge_power_sample_count",
        "learned_charge_power_phase",
        "learned_charge_power_voltage",
        "learned_charge_power_signature_mismatch_sessions",
        "learned_charge_power_signature_checked_session_started_at",
        "learned_charge_power_confidence",
        "learned_charge_power_stability_score",
        "learned_charge_power_reason",
        "learned_charge_power_detail",
        "_relay_sync_expected_state",
        "_relay_sync_requested_at",
        "_relay_sync_deadline_at",
        "_relay_sync_failure_reported",
        "_ignore_min_offtime_once",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "auto_stop_condition_reason",
        "requested_phase_selection",
        "active_phase_selection",
        "supported_phase_selections",
        "_phase_switch_pending_selection",
        "_phase_switch_state",
        "_phase_switch_requested_at",
        "_phase_switch_stable_until",
        "_phase_switch_resume_relay",
        "_phase_switch_mismatch_active",
        "_phase_switch_mismatch_counts",
        "_phase_switch_last_mismatch_selection",
        "_phase_switch_last_mismatch_at",
        "_phase_switch_lockout_selection",
        "_phase_switch_lockout_reason",
        "_phase_switch_lockout_at",
        "_phase_switch_lockout_until",
        "_auto_phase_target_candidate",
        "_auto_phase_target_since",
        "_software_update_current_version",
        "_software_update_available_version",
        "_software_update_available",
        "_software_update_state",
        "_software_update_detail",
        "_software_update_last_check_at",
        "_software_update_last_run_at",
        "_software_update_last_result",
        "_software_update_process",
        "_software_update_process_log_handle",
        "_software_update_run_requested_at",
        "_software_update_no_update_active",
        "_software_update_next_check_at",
        "_software_update_boot_auto_due_at",
        "_victron_ess_balance_pid_last_error_w",
        "_victron_ess_balance_pid_last_at",
        "_victron_ess_balance_pid_integral_output_w",
        "_victron_ess_balance_pid_last_output_w",
        "_victron_ess_balance_last_write_at",
        "_victron_ess_balance_last_setpoint_w",
        "_victron_ess_balance_active_learning_profile_key",
        "_victron_ess_balance_active_learning_profile_direction",
        "_victron_ess_balance_active_learning_profile_day_phase",
        "_victron_ess_balance_active_learning_profile_reserve_phase",
        "_victron_ess_balance_learning_profiles",
        "_victron_ess_balance_telemetry_last_command_at",
        "_victron_ess_balance_telemetry_last_command_setpoint_w",
        "_victron_ess_balance_telemetry_last_command_error_w",
        "_victron_ess_balance_telemetry_last_command_profile_key",
        "_victron_ess_balance_telemetry_command_response_recorded",
        "_victron_ess_balance_telemetry_command_overshoot_recorded",
        "_victron_ess_balance_telemetry_command_settled_recorded",
        "_victron_ess_balance_telemetry_response_delay_seconds",
        "_victron_ess_balance_telemetry_delay_samples",
        "_victron_ess_balance_telemetry_estimated_gain",
        "_victron_ess_balance_telemetry_gain_samples",
        "_victron_ess_balance_telemetry_last_observed_error_w",
        "_victron_ess_balance_telemetry_last_observed_at",
        "_victron_ess_balance_telemetry_overshoot_active",
        "_victron_ess_balance_telemetry_overshoot_count",
        "_victron_ess_balance_telemetry_settling_active",
        "_victron_ess_balance_telemetry_settled_count",
        "_victron_ess_balance_telemetry_stability_score",
    }

    def __init__(self, service: Any) -> None:
        super().__init__(service)

    @property
    def _startup_manual_target(self) -> bool | None:
        return normalize_optional_binary_state(getattr(self._service, "_startup_manual_target", None))

    @_startup_manual_target.setter
    def _startup_manual_target(self, value: object) -> None:
        self._service._startup_manual_target = normalize_optional_binary_state(value)

    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: object) -> None:
        normalize_mode = getattr(self._service, "_normalize_mode", None)
        self._service.virtual_mode = (
            normalize_mode(value) if callable(normalize_mode) else non_negative_int(value)
        )

    @property
    def virtual_startstop(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_startstop", 1), default=1)

    @virtual_startstop.setter
    def virtual_startstop(self, value: object) -> None:
        self._service.virtual_startstop = normalize_binary_flag(value)

    @property
    def virtual_enable(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_enable", 1), default=1)

    @virtual_enable.setter
    def virtual_enable(self, value: object) -> None:
        self._service.virtual_enable = normalize_binary_flag(value)

    @property
    def _last_auto_state(self) -> str:
        state, _code = normalized_auto_state_pair(
            getattr(self._service, "_last_auto_state", "idle"),
            getattr(self._service, "_last_auto_state_code", 0),
        )
        return state

    @_last_auto_state.setter
    def _last_auto_state(self, value: object) -> None:
        state, code = normalized_auto_state_pair(value, getattr(self._service, "_last_auto_state_code", 0))
        self._service._last_auto_state = state
        self._service._last_auto_state_code = code

    @property
    def _last_auto_state_code(self) -> int:
        _state, code = normalized_auto_state_pair(
            getattr(self._service, "_last_auto_state", "idle"),
            getattr(self._service, "_last_auto_state_code", 0),
        )
        return code

    @_last_auto_state_code.setter
    def _last_auto_state_code(self, value: object) -> None:
        state, code = normalized_auto_state_pair(getattr(self._service, "_last_auto_state", "idle"), value)
        self._service._last_auto_state = state
        self._service._last_auto_state_code = code

    @property
    def _last_pm_status_confirmed(self) -> bool:
        return bool(getattr(self._service, "_last_pm_status_confirmed", False))

    @_last_pm_status_confirmed.setter
    def _last_pm_status_confirmed(self, value: object) -> None:
        self._service._last_pm_status_confirmed = bool(value)

    @property
    def learned_charge_power_state(self) -> str:
        return normalize_learning_state(getattr(self._service, "learned_charge_power_state", "unknown"))

    @learned_charge_power_state.setter
    def learned_charge_power_state(self, value: object) -> None:
        self._service.learned_charge_power_state = normalize_learning_state(value)
