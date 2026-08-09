# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed value contracts for setup-support defaults and copy boundaries."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from tests.runtime_contract_assertions import (
    assert_typed_mapping as _assert_typed_mapping,
    typed_values as _typed_values,
)

from venus_evcharger.runtime.setup_support import (
    _first_existing_version_line,
    _read_version_line,
    clone_worker_battery_sources_payload,
    clone_worker_learning_profiles_payload,
    clone_worker_status_payload,
    default_auto_metrics,
    empty_worker_snapshot,
    initialize_runtime_override_state,
    initialize_victron_balance_runtime_state,
)

AUTO_NONE = (
    "surplus", "grid", "soc", "start_threshold", "stop_threshold",
    "learned_charge_power", "stop_alpha", "surplus_volatility",
    "battery_charge_power_w", "battery_discharge_power_w",
    "battery_charge_activity_ratio", "battery_discharge_activity_ratio",
    "battery_observed_max_charge_power_w", "battery_observed_max_discharge_power_w",
    "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds",
    "battery_discharge_balance_victron_bias_learning_profile_estimated_gain",
    "battery_discharge_balance_victron_bias_learning_profile_stability_score",
    "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score",
    "battery_discharge_balance_victron_bias_learning_profile_response_variance_score",
    "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score",
    "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second",
    "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts",
    "battery_discharge_balance_victron_bias_source_error_w",
    "battery_discharge_balance_victron_bias_setpoint_w",
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
)
AUTO_INTS = (
    "battery_learning_profile_count",
    "battery_discharge_balance_coordination_policy_enabled",
    "battery_discharge_balance_coordination_gate_active",
    "battery_discharge_balance_coordination_advisory_active",
    "battery_discharge_balance_victron_bias_enabled",
    "battery_discharge_balance_victron_bias_active",
    "battery_discharge_balance_victron_bias_activation_gate_active",
    "battery_discharge_balance_victron_bias_learning_profile_sample_count",
    "battery_discharge_balance_victron_bias_learning_profile_overshoot_count",
    "battery_discharge_balance_victron_bias_learning_profile_settled_count",
    "battery_discharge_balance_victron_bias_telemetry_clean",
    "battery_discharge_balance_victron_bias_overshoot_active",
    "battery_discharge_balance_victron_bias_overshoot_count",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_active",
    "battery_discharge_balance_victron_bias_settling_active",
    "battery_discharge_balance_victron_bias_settled_count",
    "battery_discharge_balance_victron_bias_oscillation_lockout_enabled",
    "battery_discharge_balance_victron_bias_oscillation_lockout_active",
    "battery_discharge_balance_victron_bias_oscillation_direction_change_count",
    "battery_discharge_balance_victron_bias_auto_apply_enabled",
    "battery_discharge_balance_victron_bias_auto_apply_active",
    "battery_discharge_balance_victron_bias_auto_apply_generation",
    "battery_discharge_balance_victron_bias_auto_apply_observation_window_active",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_active",
    "battery_discharge_balance_victron_bias_rollback_enabled",
    "battery_discharge_balance_victron_bias_rollback_active",
    "battery_discharge_balance_victron_bias_safe_state_active",
)
AUTO_EMPTY_TEXT = (
    "battery_discharge_balance_coordination_advisory_reason",
    "battery_discharge_balance_victron_bias_source_id",
    "battery_discharge_balance_victron_bias_topology_key",
    "battery_discharge_balance_victron_bias_learning_profile_key",
    "battery_discharge_balance_victron_bias_learning_profile_action_direction",
    "battery_discharge_balance_victron_bias_learning_profile_site_regime",
    "battery_discharge_balance_victron_bias_learning_profile_direction",
    "battery_discharge_balance_victron_bias_learning_profile_day_phase",
    "battery_discharge_balance_victron_bias_learning_profile_reserve_phase",
    "battery_discharge_balance_victron_bias_learning_profile_ev_phase",
    "battery_discharge_balance_victron_bias_learning_profile_pv_phase",
    "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase",
    "battery_discharge_balance_victron_bias_overshoot_cooldown_reason",
    "battery_discharge_balance_victron_bias_oscillation_lockout_reason",
    "battery_discharge_balance_victron_bias_recommended_activation_mode",
    "battery_discharge_balance_victron_bias_recommendation_profile_key",
    "battery_discharge_balance_victron_bias_recommendation_ini_snippet",
    "battery_discharge_balance_victron_bias_recommendation_hint",
    "battery_discharge_balance_victron_bias_auto_apply_last_param",
    "battery_discharge_balance_victron_bias_auto_apply_suspend_reason",
    "battery_discharge_balance_victron_bias_rollback_stable_profile_key",
    "battery_discharge_balance_victron_bias_safe_state_reason",
)
AUTO_EXPECTED = _typed_values(
    none=AUTO_NONE + ("ev_priority_available_surplus_w",),
    integers=AUTO_INTS + ("ev_priority_active",),
    floats=(
        "battery_surplus_penalty_w",
        "battery_unadjusted_surplus_penalty_w",
        "ev_priority_credit_w",
        "ev_priority_reclaimable_charge_w",
        "ev_priority_running_load_w",
        "battery_discharge_balance_coordination_start_error_w",
        "battery_discharge_balance_coordination_penalty_w",
        "battery_discharge_balance_victron_bias_pid_output_w",
    ),
    ones=("threshold_scale",),
    empty_text=AUTO_EMPTY_TEXT,
    text={
        "state": "idle", "battery_support_mode": "idle", "profile": "normal",
        "threshold_mode": "static", "stop_alpha_stage": "base",
        "battery_discharge_balance_coordination_support_mode": "supported_only",
        "battery_discharge_balance_coordination_feasibility": "not_needed",
        "battery_discharge_balance_victron_bias_activation_mode": "always",
        "battery_discharge_balance_victron_bias_support_mode": "supported_only",
        "battery_discharge_balance_victron_bias_telemetry_clean_reason": "unknown",
        "battery_discharge_balance_victron_bias_recommendation_reason": "disabled",
        "battery_discharge_balance_victron_bias_auto_apply_reason": "disabled",
        "battery_discharge_balance_victron_bias_rollback_reason": "disabled",
        "battery_discharge_balance_victron_bias_reason": "disabled",
    },
)
AUTO_EXPECTED.update(
    {
        "ev_priority_soc": 40.0,
        "ev_priority_release_soc": 38.0,
    }
)

BALANCE_EXPECTED = _typed_values(
    none=(
        "_victron_ess_balance_pid_last_at", "_victron_ess_balance_last_write_at",
        "_victron_ess_balance_last_setpoint_w", "_victron_ess_balance_auto_apply_observe_until",
        "_victron_ess_balance_auto_apply_last_applied_at", "_victron_ess_balance_oscillation_lockout_until",
        "_victron_ess_balance_last_stable_at", "_victron_ess_balance_auto_apply_suspend_until",
        "_victron_ess_balance_telemetry_last_command_at", "_victron_ess_balance_telemetry_last_command_setpoint_w",
        "_victron_ess_balance_telemetry_last_command_error_w", "_victron_ess_balance_telemetry_response_delay_seconds",
        "_victron_ess_balance_telemetry_estimated_gain", "_victron_ess_balance_telemetry_last_observed_error_w",
        "_victron_ess_balance_telemetry_last_observed_at", "_victron_ess_balance_overshoot_cooldown_until",
        "_victron_ess_balance_telemetry_stability_score", "_victron_ess_balance_telemetry_last_grid_interaction_w",
        "_victron_ess_balance_telemetry_last_ac_power_w", "_victron_ess_balance_telemetry_last_ev_power_w",
    ),
    false=(
        "_victron_ess_balance_safe_state_active", "_victron_ess_balance_telemetry_command_response_recorded",
        "_victron_ess_balance_telemetry_command_overshoot_recorded", "_victron_ess_balance_telemetry_command_settled_recorded",
        "_victron_ess_balance_telemetry_overshoot_active", "_victron_ess_balance_telemetry_settling_active",
    ),
    integers=(
        "_victron_ess_balance_auto_apply_generation", "_victron_ess_balance_telemetry_delay_samples",
        "_victron_ess_balance_telemetry_gain_samples", "_victron_ess_balance_telemetry_overshoot_count",
        "_victron_ess_balance_telemetry_settled_count",
    ),
    floats=(
        "_victron_ess_balance_pid_last_error_w", "_victron_ess_balance_pid_integral_output_w",
        "_victron_ess_balance_pid_last_output_w",
    ),
    empty_dicts=(
        "_victron_ess_balance_learning_profiles", "_victron_ess_balance_last_stable_tuning",
        "_victron_ess_balance_conservative_tuning",
    ),
    empty_lists=("_victron_ess_balance_recent_action_changes",),
    empty_text=(
        "_victron_ess_balance_active_learning_profile_key", "_victron_ess_balance_active_learning_profile_action_direction",
        "_victron_ess_balance_active_learning_profile_site_regime", "_victron_ess_balance_active_learning_profile_direction",
        "_victron_ess_balance_active_learning_profile_day_phase", "_victron_ess_balance_active_learning_profile_reserve_phase",
        "_victron_ess_balance_active_learning_profile_ev_phase", "_victron_ess_balance_active_learning_profile_pv_phase",
        "_victron_ess_balance_active_learning_profile_battery_limit_phase", "_victron_ess_balance_auto_apply_last_applied_param",
        "_victron_ess_balance_last_action_direction", "_victron_ess_balance_oscillation_lockout_reason",
        "_victron_ess_balance_last_stable_profile_key", "_victron_ess_balance_auto_apply_suspend_reason",
        "_victron_ess_balance_safe_state_reason", "_victron_ess_balance_telemetry_last_command_profile_key",
        "_victron_ess_balance_overshoot_cooldown_reason",
    ),
)

OVERRIDE_EXPECTED = _typed_values(
    none=(
        "_runtime_state_serialized", "_runtime_overrides_serialized",
        "_runtime_overrides_last_saved_at", "_runtime_overrides_pending_serialized",
        "_runtime_overrides_pending_values", "_runtime_overrides_pending_text",
        "_runtime_overrides_pending_due_at", "_last_auto_audit_key", "_last_auto_audit_event_at",
    ),
    false=("_runtime_overrides_active",),
    empty_dicts=("_runtime_overrides_values",),
    floats=("_last_auto_audit_cleanup_at",),
    ones=("runtime_overrides_write_min_interval_seconds", "_dbus_live_publish_interval_seconds"),
    fives=("_dbus_slow_publish_interval_seconds",),
)

SNAPSHOT_EXPECTED = _typed_values(
    none=(
        "pm_captured_at", "pm_status", "pv_captured_at", "pv_power", "battery_captured_at",
        "battery_soc", "battery_combined_soc", "battery_combined_usable_capacity_wh",
        "battery_combined_charge_power_w", "battery_combined_discharge_power_w",
        "battery_combined_net_power_w", "battery_combined_ac_power_w", "battery_headroom_charge_w",
        "battery_headroom_discharge_w", "expected_near_term_export_w", "expected_near_term_import_w",
        "battery_discharge_balance_error_w", "battery_discharge_balance_max_abs_error_w",
        "battery_discharge_balance_total_discharge_w", "grid_captured_at", "grid_power",
    ),
    false=("pm_confirmed", "auto_mode_active"),
    integers=(
        "battery_discharge_balance_eligible_source_count", "battery_discharge_balance_active_source_count",
        "battery_discharge_balance_control_candidate_count", "battery_discharge_balance_control_ready_count",
        "battery_discharge_balance_supported_control_source_count",
        "battery_discharge_balance_experimental_control_source_count", "battery_source_count",
        "battery_online_source_count", "battery_valid_soc_source_count",
    ),
    floats=("captured_at",),
    empty_text=("battery_discharge_balance_mode", "battery_discharge_balance_target_distribution_mode"),
    empty_dicts=("battery_learning_profiles",),
    empty_lists=("battery_sources",),
)


class RuntimeSetupSupportContractTests(unittest.TestCase):
    def test_version_reading_handles_paths_files_and_order(self) -> None:
        with patch("venus_evcharger.runtime.setup_support.os.path.isfile", return_value=True) as exists:
            self.assertEqual(_read_version_line(""), "")
        exists.assert_not_called()
        with patch(
            "venus_evcharger.runtime.setup_support.os.path.isfile",
            return_value=False,
        ) as exists:
            self.assertEqual(_read_version_line("/missing"), "")
        exists.assert_called_once_with("/missing")
        opener = mock_open(read_data=" 2.4.1 \nignored\n")
        with (
            patch("venus_evcharger.runtime.setup_support.os.path.isfile", return_value=True),
            patch("builtins.open", opener),
        ):
            self.assertEqual(_read_version_line("/version"), "2.4.1")
        opener.assert_called_once_with("/version", "r", encoding="utf-8")
        with (
            patch("venus_evcharger.runtime.setup_support.os.path.isfile", return_value=True),
            patch("builtins.open", side_effect=OSError("blocked")),
        ):
            self.assertEqual(_read_version_line("/version"), "")
        with patch(
            "venus_evcharger.runtime.setup_support._read_version_line",
            side_effect=("", "2.4.1", "unused"),
        ) as read:
            self.assertEqual(_first_existing_version_line(("a", "b", "c")), "2.4.1")
        self.assertEqual(read.call_args_list, [unittest.mock.call("a"), unittest.mock.call("b")])
        self.assertEqual(_first_existing_version_line(()), "")

    def test_default_auto_metrics_are_complete_and_typed(self) -> None:
        first = default_auto_metrics()
        second = default_auto_metrics()
        _assert_typed_mapping(self, first, AUTO_EXPECTED)
        self.assertIsNot(first, second)

    def test_victron_balance_defaults_are_complete_typed_and_independent(self) -> None:
        first = SimpleNamespace()
        second = SimpleNamespace()
        initialize_victron_balance_runtime_state(first)
        initialize_victron_balance_runtime_state(second)
        _assert_typed_mapping(self, vars(first), BALANCE_EXPECTED)
        self.assertIsNot(first._victron_ess_balance_learning_profiles, second._victron_ess_balance_learning_profiles)
        self.assertIsNot(first._victron_ess_balance_recent_action_changes, second._victron_ess_balance_recent_action_changes)

    def test_runtime_override_defaults_are_complete_typed_and_independent(self) -> None:
        first = SimpleNamespace()
        second = SimpleNamespace()
        initialize_runtime_override_state(first)
        initialize_runtime_override_state(second)
        _assert_typed_mapping(self, vars(first), OVERRIDE_EXPECTED)
        self.assertIsNot(first._runtime_overrides_values, second._runtime_overrides_values)

    def test_empty_worker_snapshot_is_complete_typed_and_independent(self) -> None:
        first = empty_worker_snapshot()
        second = empty_worker_snapshot()
        _assert_typed_mapping(self, first, SNAPSHOT_EXPECTED)
        self.assertIsNot(first["battery_sources"], second["battery_sources"])
        self.assertIsNot(first["battery_learning_profiles"], second["battery_learning_profiles"])

    def test_snapshot_copy_helpers_clone_only_owned_mutable_payloads(self) -> None:
        status = {"output": True}
        snapshot: dict[str, object] = {"pm_status": status}
        clone_worker_status_payload(snapshot)
        self.assertEqual(snapshot["pm_status"], status)
        self.assertIsNot(snapshot["pm_status"], status)
        status_scalar_snapshot: dict[str, object] = {"pm_status": "unknown"}
        clone_worker_status_payload(status_scalar_snapshot)
        self.assertEqual(status_scalar_snapshot, {"pm_status": "unknown"})

        source = {"id": "battery"}
        sources: list[object] = [source, "offline"]
        snapshot = {"battery_sources": sources}
        clone_worker_battery_sources_payload(snapshot)
        self.assertIsNot(snapshot["battery_sources"], sources)
        cloned_sources = snapshot["battery_sources"]
        if not isinstance(cloned_sources, list):
            self.fail("battery_sources clone must remain a list")
        self.assertIsNot(cloned_sources[0], source)
        self.assertEqual(snapshot["battery_sources"], sources)
        sources_scalar_snapshot: dict[str, object] = {"battery_sources": None}
        clone_worker_battery_sources_payload(sources_scalar_snapshot)
        self.assertEqual(sources_scalar_snapshot, {"battery_sources": None})

        profile = {"samples": 3}
        profiles: dict[object, object] = {7: profile, "raw": "value"}
        snapshot = {"battery_learning_profiles": profiles}
        clone_worker_learning_profiles_payload(snapshot)
        self.assertEqual(snapshot["battery_learning_profiles"], {"7": profile, "raw": "value"})
        cloned_profiles = snapshot["battery_learning_profiles"]
        if not isinstance(cloned_profiles, dict):
            self.fail("battery_learning_profiles clone must remain a dict")
        self.assertIsNot(cloned_profiles["7"], profile)
        profiles_scalar_snapshot: dict[str, object] = {"battery_learning_profiles": []}
        clone_worker_learning_profiles_payload(profiles_scalar_snapshot)
        self.assertEqual(profiles_scalar_snapshot, {"battery_learning_profiles": []})


if __name__ == "__main__":
    unittest.main()
