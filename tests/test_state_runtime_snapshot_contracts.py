# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the persisted runtime-state snapshot boundary."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.controllers import state_runtime_snapshot as snapshot_module
from venus_evcharger.controllers import state_runtime_snapshot_victron as snapshot_victron
from venus_evcharger.controllers.state_contracts import string_key_items
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_runtime_snapshot import RuntimeStateSnapshotBuilder


class TestStateRuntimeSnapshotContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SimpleNamespace()
        self.controller = RuntimeStateSnapshotBuilder(self.service, RuntimeStateNormalizer())

    def test_profile_scalar_helpers_cover_explicit_derived_and_fallback_values(self) -> None:
        sample_count = self.controller._victron_ess_balance_runtime_profile_sample_count
        self.assertEqual(sample_count({"sample_count": 7, "delay_samples": 99}), 7)
        self.assertEqual(
            sample_count(
                {
                    "delay_samples": 3,
                    "gain_samples": 4,
                    "settled_count": 5,
                    "overshoot_count": 2,
                }
            ),
            7,
        )
        self.assertEqual(sample_count({"delay_samples": -1, "gain_samples": "bad"}), 0)
        metric = self.controller._victron_ess_balance_runtime_profile_metric
        self.assertEqual(metric({"primary": 1.5}, "primary"), 1.5)
        self.assertEqual(metric({"fallback": 2.5}, "primary", "fallback"), 2.5)
        self.assertIsNone(metric({"primary": float("inf"), "fallback": 2.5}, "primary", "fallback"))

    def test_scalar_payload_groups_preserve_every_value_and_default(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp="1.1",
            auto_battery_discharge_balance_victron_bias_ki="2.2",
            auto_battery_discharge_balance_victron_bias_kd="3.3",
            auto_battery_discharge_balance_victron_bias_deadband_watts="4.4",
            auto_battery_discharge_balance_victron_bias_max_abs_watts="5.5",
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second="6.6",
            auto_battery_discharge_balance_victron_bias_activation_mode=" ON-DEMAND ",
            _victron_ess_balance_auto_apply_generation=7,
            _victron_ess_balance_auto_apply_observe_until="8.8",
            _victron_ess_balance_auto_apply_last_applied_param=" kp ",
            _victron_ess_balance_auto_apply_last_applied_at="9.9",
            _victron_ess_balance_auto_apply_suspend_until="10.1",
            _victron_ess_balance_auto_apply_suspend_reason=" guard ",
            _victron_ess_balance_oscillation_lockout_until="11.1",
            _victron_ess_balance_oscillation_lockout_reason=" oscillation ",
            _victron_ess_balance_overshoot_cooldown_until="12.2",
            _victron_ess_balance_overshoot_cooldown_reason=" overshoot ",
            _victron_ess_balance_last_stable_tuning={"kp": 13.3},
            _victron_ess_balance_last_stable_at="14.4",
            _victron_ess_balance_last_stable_profile_key=" profile-a ",
            _victron_ess_balance_conservative_tuning={"ki": 15.5},
            _victron_ess_balance_safe_state_active=1,
            _victron_ess_balance_safe_state_reason=" fallback ",
        )
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_tuning_payload(svc),
            {
                "kp": 1.1,
                "ki": 2.2,
                "kd": 3.3,
                "deadband_watts": 4.4,
                "max_abs_watts": 5.5,
                "ramp_rate_watts_per_second": 6.6,
                "activation_mode": "on-demand",
            },
        )
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_auto_apply_payload(svc),
            {
                "auto_apply_generation": 7,
                "auto_apply_observe_until": 8.8,
                "auto_apply_last_applied_param": "kp",
                "auto_apply_last_applied_at": 9.9,
                "auto_apply_suspend_until": 10.1,
                "auto_apply_suspend_reason": "guard",
            },
        )
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_safety_payload(svc),
            {
                "oscillation_lockout_until": 11.1,
                "oscillation_lockout_reason": "oscillation",
                "overshoot_cooldown_until": 12.2,
                "overshoot_cooldown_reason": "overshoot",
                "last_stable_tuning": {"kp": 13.3},
                "last_stable_at": 14.4,
                "last_stable_profile_key": "profile-a",
                "conservative_tuning": {"ki": 15.5},
                "safe_state_active": True,
                "safe_state_reason": "fallback",
            },
        )
        combined = self.controller._victron_ess_balance_runtime_adaptive_scalar_payload(svc)
        self.assertEqual(len(combined), 23)
        self.assertEqual(combined["kp"], 1.1)
        self.assertEqual(combined["auto_apply_generation"], 7)
        self.assertEqual(combined["safe_state_reason"], "fallback")

    def test_core_runtime_groups_preserve_exact_state(self) -> None:
        svc = SimpleNamespace(
            virtual_mode="2",
            virtual_autostart="1",
            virtual_enable="1",
            virtual_startstop="0",
            manual_override_until="4.5",
            _auto_mode_cutover_pending=True,
            relay_last_changed_at=6.0,
            relay_last_off_at=7.0,
            learned_charge_power_watts=800.0,
            learned_charge_power_updated_at=9.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=10.0,
            learned_charge_power_sample_count="11",
            learned_charge_power_phase="P1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions="12",
            learned_charge_power_signature_checked_session_started_at=13.0,
        )
        self.assertEqual(
            self.controller._base_runtime_state(svc),
            {
                "mode": 2,
                "autostart": 1,
                "enable": 1,
                "startstop": 0,
                "manual_override_until": 4.5,
                "auto_mode_cutover_pending": 1,
                "relay_last_changed_at": 6.0,
                "relay_last_off_at": 7.0,
            },
        )
        self.assertEqual(
            self.controller._learned_charge_power_runtime_state(svc),
            {
                "learned_charge_power_watts": 800.0,
                "learned_charge_power_updated_at": 9.0,
                "learned_charge_power_state": "stable",
                "learned_charge_power_learning_since": 10.0,
                "learned_charge_power_sample_count": 11,
                "learned_charge_power_phase": "P1",
                "learned_charge_power_voltage": 230.0,
                "learned_charge_power_signature_mismatch_sessions": 12,
                "learned_charge_power_signature_checked_session_started_at": 13.0,
            },
        )

    def test_phase_and_contactor_groups_preserve_normalized_state(self) -> None:
        svc = SimpleNamespace(
            active_phase_selection="P1_P2",
            requested_phase_selection="P1_P2_P3",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=20.0,
            _phase_switch_stable_until=21.0,
            _phase_switch_resume_relay=True,
            _phase_switch_mismatch_counts={"P1": 2},
            _phase_switch_last_mismatch_selection="P1",
            _phase_switch_last_mismatch_at=22.0,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason=" mismatch ",
            _phase_switch_lockout_at=23.0,
            _phase_switch_lockout_until=24.0,
            _contactor_fault_counts={"open": 3},
            _contactor_fault_active_reason=" suspected-open ",
            _contactor_fault_active_since=25.0,
            _contactor_lockout_reason=" welded ",
            _contactor_lockout_source=" feedback ",
            _contactor_lockout_at=26.0,
        )
        self.assertEqual(
            self.controller._phase_selection_runtime_state(svc),
            {
                "active_phase_selection": "P1_P2",
                "requested_phase_selection": "P1_P2_P3",
                "supported_phase_selections": ["P1", "P1_P2", "P1_P2_P3"],
            },
        )
        self.assertEqual(
            self.controller._phase_switch_runtime_state(svc),
            {
                "phase_switch_pending_selection": "P1_P2",
                "phase_switch_state": "stabilizing",
                "phase_switch_requested_at": 20.0,
                "phase_switch_stable_until": 21.0,
                "phase_switch_resume_relay": 1,
                "phase_switch_mismatch_counts": {"P1": 2},
                "phase_switch_last_mismatch_selection": "P1",
                "phase_switch_last_mismatch_at": 22.0,
                "phase_switch_lockout_selection": "P1_P2",
                "phase_switch_lockout_reason": " mismatch ",
                "phase_switch_lockout_at": 23.0,
                "phase_switch_lockout_until": 24.0,
            },
        )
        self.assertEqual(
            self.controller._contactor_runtime_state(svc),
            {
                "contactor_fault_counts": {"open": 3},
                "contactor_fault_active_reason": "suspected-open",
                "contactor_fault_active_since": 25.0,
                "contactor_lockout_reason": " welded ",
                "contactor_lockout_source": " feedback ",
                "contactor_lockout_at": 26.0,
            },
        )

    def test_energy_group_maps_every_worker_snapshot_field(self) -> None:
        source_keys = (
            "battery_combined_soc",
            "battery_combined_usable_capacity_wh",
            "battery_combined_charge_power_w",
            "battery_combined_discharge_power_w",
            "battery_combined_net_power_w",
            "battery_combined_ac_power_w",
            "battery_combined_pv_input_power_w",
            "battery_combined_grid_interaction_w",
            "battery_headroom_charge_w",
            "battery_headroom_discharge_w",
            "expected_near_term_export_w",
            "expected_near_term_import_w",
            "battery_discharge_balance_mode",
            "battery_discharge_balance_target_distribution_mode",
            "battery_discharge_balance_error_w",
            "battery_discharge_balance_max_abs_error_w",
            "battery_discharge_balance_total_discharge_w",
            "battery_discharge_balance_eligible_source_count",
            "battery_discharge_balance_active_source_count",
            "battery_discharge_balance_control_candidate_count",
            "battery_discharge_balance_control_ready_count",
            "battery_discharge_balance_supported_control_source_count",
            "battery_discharge_balance_experimental_control_source_count",
            "battery_average_confidence",
            "battery_source_count",
            "battery_online_source_count",
            "battery_valid_soc_source_count",
            "battery_battery_source_count",
            "battery_hybrid_inverter_source_count",
            "battery_inverter_source_count",
        )
        raw: dict[str, object] = {key: index + 1 for index, key in enumerate(source_keys)}
        raw["battery_sources"] = ["source-a"]
        raw["battery_learning_profiles"] = {"profile-a": {"count": 1}}
        state = self.controller._energy_runtime_state(SimpleNamespace(_get_worker_snapshot=lambda: raw))
        expected_keys = (
            "combined_battery_soc",
            "combined_battery_usable_capacity_wh",
            "combined_battery_charge_power_w",
            "combined_battery_discharge_power_w",
            "combined_battery_net_power_w",
            "combined_battery_ac_power_w",
            "combined_battery_pv_input_power_w",
            "combined_battery_grid_interaction_w",
            "combined_battery_headroom_charge_w",
            "combined_battery_headroom_discharge_w",
            "expected_near_term_export_w",
            "expected_near_term_import_w",
            "battery_discharge_balance_mode",
            "battery_discharge_balance_target_distribution_mode",
            "battery_discharge_balance_error_w",
            "battery_discharge_balance_max_abs_error_w",
            "battery_discharge_balance_total_discharge_w",
            "battery_discharge_balance_eligible_source_count",
            "battery_discharge_balance_active_source_count",
            "battery_discharge_balance_control_candidate_count",
            "battery_discharge_balance_control_ready_count",
            "battery_discharge_balance_supported_control_source_count",
            "battery_discharge_balance_experimental_control_source_count",
            "combined_battery_average_confidence",
            "combined_battery_source_count",
            "combined_battery_online_source_count",
            "combined_battery_valid_soc_source_count",
            "combined_battery_battery_source_count",
            "combined_battery_hybrid_inverter_source_count",
            "combined_battery_inverter_source_count",
        )
        self.assertEqual(state, dict(zip(expected_keys, range(1, 31))) | {
            "combined_battery_sources": ["source-a"],
            "combined_battery_learning_profiles": {"profile-a": {"count": 1}},
        })

    def test_topology_profile_and_learning_contracts_are_exact(self) -> None:
        svc = SimpleNamespace(
            auto_energy_sources=(SimpleNamespace(source_id=" z "), SimpleNamespace(source_id="a")),
            auto_battery_discharge_balance_victron_bias_service=" service ",
            auto_battery_discharge_balance_victron_bias_path=" /path ",
            auto_battery_discharge_balance_victron_bias_source_id=" source ",
            _victron_ess_balance_learning_profiles={"profile-a": {"sample_count": 2}},
        )
        topology = "victron-bias-learning/v2/source=source/service=service/path=/path/energy=a,z"
        self.assertEqual(self.controller._victron_ess_balance_runtime_topology_key(svc, " source "), topology)
        profile = {
            "key": "stored-key",
            "action_direction": "increase",
            "site_regime": "export",
            "direction": "positive",
            "day_phase": "day",
            "reserve_phase": "normal",
            "ev_phase": "idle",
            "pv_phase": "high",
            "battery_limit_phase": "free",
            "sample_count": 7,
            "delay_samples": 1,
            "gain_samples": 2,
            "overshoot_count": 3,
            "settled_count": 4,
            "response_delay_seconds": 5.1,
            "estimated_gain": 6.1,
            "response_delay_mad_seconds": 7.1,
            "gain_mad": 8.1,
            "stability_score": 9.1,
            "typical_response_delay_seconds": 10.1,
            "effective_gain": 11.1,
            "regime_consistency_score": 12.1,
            "response_variance_score": 13.1,
            "reproducibility_score": 14.1,
            "safe_ramp_rate_watts_per_second": 15.1,
            "preferred_bias_limit_watts": 16.1,
        }
        expected = dict(profile)
        self.assertEqual(self.controller._victron_ess_balance_runtime_profile_snapshot("profile-a", profile), expected)
        learning = self.controller._victron_ess_balance_runtime_learning_state(svc)
        self.assertEqual(learning["schema_version"], 2)
        self.assertEqual(learning["topology_key"], topology)
        self.assertEqual(learning["source_id"], "source")
        profiles = string_key_items(learning["profiles"])
        profile_snapshot = string_key_items(profiles["profile-a"])
        self.assertEqual(profile_snapshot["sample_count"], 2)

    def test_orchestration_and_json_reader_delegate_exactly_once(self) -> None:
        controller = self.controller
        groups = (
            "_base_runtime_state",
            "_learned_charge_power_runtime_state",
            "_phase_selection_runtime_state",
            "_phase_switch_runtime_state",
            "_contactor_runtime_state",
            "_energy_runtime_state",
        )
        patches = [patch.object(controller, name, return_value={name: name}) for name in groups]
        mocks = [item.start() for item in patches]
        learning = patch.object(controller, "_victron_ess_balance_runtime_learning_state", return_value={"learning": 1})
        adaptive = patch.object(controller, "_victron_ess_balance_runtime_adaptive_tuning_state", return_value={"adaptive": 2})
        learning_mock = learning.start()
        adaptive_mock = adaptive.start()
        for item in patches:
            self.addCleanup(item.stop)
        self.addCleanup(learning.stop)
        self.addCleanup(adaptive.stop)
        result = controller.build()
        for name, mock in zip(groups, mocks):
            mock.assert_called_once_with(self.service)
            self.assertEqual(result[name], name)
        learning_mock.assert_called_once_with(self.service)
        adaptive_mock.assert_called_once_with(self.service)
        self.assertEqual(result["victron_ess_balance_learning_state"], {"learning": 1})
        self.assertEqual(result["victron_ess_balance_adaptive_tuning_state"], {"adaptive": 2})

        with patch.object(snapshot_module, "read_json_object_file", return_value={"mode": 2}) as reader:
            self.assertEqual(controller._read_runtime_state_payload("/tmp/state.json"), {"mode": 2})
        reader.assert_called_once_with("/tmp/state.json")

    def test_module_helpers_normalize_boundary_values(self) -> None:
        svc = SimpleNamespace(
            auto_energy_sources=(SimpleNamespace(source_id=" a "), SimpleNamespace(source_id=""), object()),
            text=" value ",
        )
        self.assertEqual(snapshot_victron._victron_ess_balance_energy_ids(svc), ["a"])
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_string(svc, "text"), "value")
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_string(svc, "missing"), "")
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_non_negative_int(True), 1)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_non_negative_int(-2.9), 0)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_non_negative_int(3.9), 3)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_non_negative_int("3"), 0)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_attr_text(svc, "text"), "value")
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_attr_text(
                svc,
                "missing",
                fallback=" FALLBACK ",
                normalize_lower=True,
            ),
            "fallback",
        )
        profile: dict[str, object] = {"text": "value", "empty": ""}
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_text(profile, "text"), "value")
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_profile_text(profile, "empty", fallback="fallback"),
            "fallback",
        )


if __name__ == "__main__":
    unittest.main()
