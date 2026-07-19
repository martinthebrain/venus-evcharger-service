# SPDX-License-Identifier: GPL-3.0-or-later
"""Default and absence contracts for runtime-state snapshots."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.controllers import state_runtime_snapshot as snapshot_module
from venus_evcharger.controllers import state_runtime_snapshot_victron as snapshot_victron
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_runtime_snapshot import RuntimeStateSnapshotBuilder


class TestStateRuntimeSnapshotDefaultsContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = RuntimeStateSnapshotBuilder(SimpleNamespace(), RuntimeStateNormalizer())
        self.empty = SimpleNamespace()

    def test_adaptive_payload_group_defaults_are_complete(self) -> None:
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_tuning_payload(self.empty),
            {
                "kp": None,
                "ki": None,
                "kd": None,
                "deadband_watts": None,
                "max_abs_watts": None,
                "ramp_rate_watts_per_second": None,
                "activation_mode": "always",
            },
        )
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_auto_apply_payload(self.empty),
            {
                "auto_apply_generation": 0,
                "auto_apply_observe_until": None,
                "auto_apply_last_applied_param": "",
                "auto_apply_last_applied_at": None,
                "auto_apply_suspend_until": None,
                "auto_apply_suspend_reason": "",
            },
        )
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_safety_payload(self.empty),
            {
                "oscillation_lockout_until": None,
                "oscillation_lockout_reason": "",
                "overshoot_cooldown_until": None,
                "overshoot_cooldown_reason": "",
                "last_stable_tuning": {},
                "last_stable_at": None,
                "last_stable_profile_key": "",
                "conservative_tuning": {},
                "safe_state_active": False,
                "safe_state_reason": "",
            },
        )

    def test_learning_phase_and_contactor_defaults_are_complete(self) -> None:
        self.assertEqual(
            self.controller._learned_charge_power_runtime_state(self.empty),
            {
                "learned_charge_power_watts": None,
                "learned_charge_power_updated_at": None,
                "learned_charge_power_state": "unknown",
                "learned_charge_power_learning_since": None,
                "learned_charge_power_sample_count": 0,
                "learned_charge_power_phase": None,
                "learned_charge_power_voltage": None,
                "learned_charge_power_signature_mismatch_sessions": 0,
                "learned_charge_power_signature_checked_session_started_at": None,
            },
        )
        self.assertEqual(
            self.controller._phase_selection_runtime_state(self.empty),
            {
                "active_phase_selection": "P1",
                "requested_phase_selection": "P1",
                "supported_phase_selections": ["P1"],
            },
        )
        self.assertEqual(
            self.controller._phase_switch_runtime_state(self.empty),
            {
                "phase_switch_pending_selection": None,
                "phase_switch_state": None,
                "phase_switch_requested_at": None,
                "phase_switch_stable_until": None,
                "phase_switch_resume_relay": 0,
                "phase_switch_mismatch_counts": {},
                "phase_switch_last_mismatch_selection": None,
                "phase_switch_last_mismatch_at": None,
                "phase_switch_lockout_selection": None,
                "phase_switch_lockout_reason": "",
                "phase_switch_lockout_at": None,
                "phase_switch_lockout_until": None,
            },
        )
        self.assertEqual(
            self.controller._contactor_runtime_state(self.empty),
            {
                "contactor_fault_counts": {},
                "contactor_fault_active_reason": None,
                "contactor_fault_active_since": None,
                "contactor_lockout_reason": "",
                "contactor_lockout_source": "",
                "contactor_lockout_at": None,
            },
        )

    def test_base_state_encodes_false_cutover_as_zero(self) -> None:
        svc = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_enable=0,
            virtual_startstop=0,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            relay_last_changed_at=None,
            relay_last_off_at=None,
        )
        self.assertEqual(
            self.controller._base_runtime_state(svc),
            {
                "mode": 0,
                "autostart": 0,
                "enable": 0,
                "startstop": 0,
                "manual_override_until": 0.0,
                "auto_mode_cutover_pending": 0,
                "relay_last_changed_at": None,
                "relay_last_off_at": None,
            },
        )

    def test_empty_energy_snapshot_has_exact_defaults(self) -> None:
        expected: dict[str, object] = {
            "combined_battery_soc": None,
            "combined_battery_usable_capacity_wh": None,
            "combined_battery_charge_power_w": None,
            "combined_battery_discharge_power_w": None,
            "combined_battery_net_power_w": None,
            "combined_battery_ac_power_w": None,
            "combined_battery_pv_input_power_w": None,
            "combined_battery_grid_interaction_w": None,
            "combined_battery_headroom_charge_w": None,
            "combined_battery_headroom_discharge_w": None,
            "expected_near_term_export_w": None,
            "expected_near_term_import_w": None,
            "battery_discharge_balance_mode": None,
            "battery_discharge_balance_target_distribution_mode": None,
            "battery_discharge_balance_error_w": None,
            "battery_discharge_balance_max_abs_error_w": None,
            "battery_discharge_balance_total_discharge_w": None,
            "battery_discharge_balance_eligible_source_count": 0,
            "battery_discharge_balance_active_source_count": 0,
            "battery_discharge_balance_control_candidate_count": 0,
            "battery_discharge_balance_control_ready_count": 0,
            "battery_discharge_balance_supported_control_source_count": 0,
            "battery_discharge_balance_experimental_control_source_count": 0,
            "combined_battery_average_confidence": None,
            "combined_battery_source_count": 0,
            "combined_battery_online_source_count": 0,
            "combined_battery_valid_soc_source_count": 0,
            "combined_battery_battery_source_count": 0,
            "combined_battery_hybrid_inverter_source_count": 0,
            "combined_battery_inverter_source_count": 0,
            "combined_battery_sources": [],
            "combined_battery_learning_profiles": {},
        }
        self.assertEqual(self.controller._energy_runtime_state(self.empty), expected)
        self.assertEqual(
            self.controller._energy_runtime_state(SimpleNamespace(_get_worker_snapshot=None)),
            expected,
        )

    def test_topology_and_adaptive_state_defaults_are_exact(self) -> None:
        topology = "victron-bias-learning/v2/source=/service=/path=/energy="
        self.assertEqual(
            RuntimeStateSnapshotBuilder._victron_ess_balance_runtime_topology_key(self.empty, ""),
            topology,
        )
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_learning_state(self.empty),
            {"schema_version": 2, "topology_key": topology, "source_id": "", "profiles": {}},
        )
        adaptive = self.controller._victron_ess_balance_runtime_adaptive_tuning_state(self.empty)
        self.assertEqual(adaptive["schema_version"], 2)
        self.assertEqual(adaptive["topology_key"], topology)
        self.assertEqual(adaptive["source_id"], "")
        self.assertEqual(len(adaptive), 26)
        self.assertEqual(adaptive["activation_mode"], "always")
        self.assertFalse(adaptive["safe_state_active"])

    def test_profile_defaults_fallbacks_and_count_branches_are_exact(self) -> None:
        count = self.controller._victron_ess_balance_runtime_profile_sample_count
        self.assertEqual(count({"delay_samples": 5, "gain_samples": 4}), 5)
        self.assertEqual(count({"delay_samples": 4, "gain_samples": 5}), 5)
        self.assertEqual(count({"settled_count": 2, "overshoot_count": 4}), 6)
        self.assertEqual(count({"sample_count": True}), 1)
        self.assertEqual(count({"sample_count": -4}), 0)

        expected = {
            "key": "profile-a",
            "action_direction": "",
            "site_regime": "",
            "direction": "",
            "day_phase": "",
            "reserve_phase": "",
            "ev_phase": "",
            "pv_phase": "",
            "battery_limit_phase": "",
            "sample_count": 0,
            "delay_samples": 0,
            "gain_samples": 0,
            "overshoot_count": 0,
            "settled_count": 0,
            "response_delay_seconds": None,
            "estimated_gain": None,
            "response_delay_mad_seconds": None,
            "gain_mad": None,
            "stability_score": None,
            "typical_response_delay_seconds": None,
            "effective_gain": None,
            "regime_consistency_score": None,
            "response_variance_score": None,
            "reproducibility_score": None,
            "safe_ramp_rate_watts_per_second": None,
            "preferred_bias_limit_watts": None,
        }
        self.assertEqual(
            self.controller._victron_ess_balance_runtime_profile_snapshot("profile-a", None),
            expected,
        )
        fallback_profile: dict[str, object] = {
            "response_delay_seconds": 2.5,
            "estimated_gain": 3.5,
        }
        learning = snapshot_victron._victron_ess_balance_runtime_profile_learning_metrics(fallback_profile)
        self.assertEqual(learning["typical_response_delay_seconds"], 2.5)
        self.assertEqual(learning["effective_gain"], 3.5)

    def test_text_helpers_distinguish_missing_blank_and_lowercase_modes(self) -> None:
        svc = SimpleNamespace(blank="", mixed=" MiXeD ")
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_attr_text(svc, "missing", fallback="fallback"),
            "fallback",
        )
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_attr_text(svc, "blank", fallback="fallback"),
            "fallback",
        )
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_attr_text(svc, "mixed", normalize_lower=True),
            "mixed",
        )
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_attr_text(svc, "mixed"), "MiXeD")
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_attr_text(svc, "missing"), "")
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_text({}, "missing"), "")
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_profile_text({}, "missing", fallback="fallback"),
            "fallback",
        )

    def test_invalid_optional_phases_fall_back_to_requested_phase(self) -> None:
        svc = SimpleNamespace(
            requested_phase_selection="P1_P2",
            _phase_switch_pending_selection="invalid",
            _phase_switch_last_mismatch_selection="invalid",
            _phase_switch_lockout_selection="invalid",
        )
        state = self.controller._phase_switch_runtime_state(svc)
        self.assertEqual(state["phase_switch_pending_selection"], "P1_P2")
        self.assertEqual(state["phase_switch_last_mismatch_selection"], "P1_P2")
        self.assertEqual(state["phase_switch_lockout_selection"], "P1_P2")

    def test_topology_helper_calls_have_exact_boundary_arguments(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(snapshot_module, "_victron_ess_balance_energy_ids", return_value=["z", "a"]) as energy_ids,
            patch.object(snapshot_module, "_victron_ess_balance_runtime_string", side_effect=("service", "/path")) as text,
        ):
            result = RuntimeStateSnapshotBuilder._victron_ess_balance_runtime_topology_key(svc, " source ")
        self.assertEqual(
            result,
            "victron-bias-learning/v2/source=source/service=service/path=/path/energy=a,z",
        )
        energy_ids.assert_called_once_with(svc)
        self.assertEqual(
            text.call_args_list,
            [
                call(svc, "auto_battery_discharge_balance_victron_bias_service"),
                call(svc, "auto_battery_discharge_balance_victron_bias_path"),
            ],
        )

    def test_learning_and_adaptive_builders_forward_normalized_identity(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_source_id=" source ",
            _victron_ess_balance_learning_profiles={7: object()},
        )
        with (
            patch.object(RuntimeStateSnapshotBuilder, "_victron_ess_balance_runtime_topology_key", return_value="topology") as topology,
            patch.object(RuntimeStateSnapshotBuilder, "_victron_ess_balance_runtime_profile_snapshot", return_value={"profile": 1}) as profile,
        ):
            learning = RuntimeStateSnapshotBuilder._victron_ess_balance_runtime_learning_state(svc)
        self.assertEqual(
            learning,
            {
                "schema_version": 2,
                "topology_key": "topology",
                "source_id": "source",
                "profiles": {"7": {"profile": 1}},
            },
        )
        topology.assert_called_once_with(svc, "source")
        profile.assert_called_once_with("7", svc._victron_ess_balance_learning_profiles[7])

        with (
            patch.object(RuntimeStateSnapshotBuilder, "_victron_ess_balance_runtime_topology_key", return_value="topology") as topology,
            patch.object(RuntimeStateSnapshotBuilder, "_victron_ess_balance_runtime_adaptive_scalar_payload", return_value={"kp": 1.0}) as scalar,
        ):
            adaptive = RuntimeStateSnapshotBuilder._victron_ess_balance_runtime_adaptive_tuning_state(svc)
        self.assertEqual(
            adaptive,
            {"schema_version": 2, "topology_key": "topology", "source_id": "source", "kp": 1.0},
        )
        topology.assert_called_once_with(svc, "source")
        scalar.assert_called_once_with(svc)


if __name__ == "__main__":
    unittest.main()
