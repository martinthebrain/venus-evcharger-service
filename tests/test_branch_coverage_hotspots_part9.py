# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronSafetyCasesPart1:
    def test_victron_safety_helper_branches(self) -> None:
        components = _components()
        safety = components.safety
        recovery = components.recovery
        sources = components.sources
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=50.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=False,
            auto_battery_discharge_balance_victron_bias_rollback_enabled=False,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=15.0,
            _auto_cached_inputs_used=True,
            _phase_switch_state="switching",
            _contactor_fault_active_reason="fault",
            _contactor_lockout_reason="lockout",
            _victron_ess_balance_recent_action_changes=[None, {"at": 5.0}, {"at": 20.0, "action_direction": "more_export"}],
            _victron_ess_balance_telemetry_last_observed_at=10.0,
            _victron_ess_balance_auto_apply_last_applied_at=None,
            _victron_ess_balance_last_stable_tuning=None,
            _victron_ess_balance_conservative_tuning={"kp": 0.1},
            charging_started_at=1.0,
            virtual_startstop=1,
            learned_charge_power_watts=2300.0,
        )
        self.assertEqual(safety._victron_ess_balance_grid_window_reason(svc, {}), "grid_interaction_missing")
        self.assertEqual(
            safety._victron_ess_balance_power_window_reason(
                SimpleNamespace(_victron_ess_balance_telemetry_last_ac_power_w=0.0),
                {"battery_combined_ac_power_w": 1000.0},
            ),
            "foreign_power_event",
        )
        self.assertEqual(safety._victron_ess_balance_phase_switch_reason(svc), "phase_switch_active")
        self.assertEqual(safety._victron_ess_balance_contactor_block_reason(svc), "contactor_fault_active")
        svc._contactor_fault_active_reason = ""
        self.assertEqual(safety._victron_ess_balance_contactor_block_reason(svc), "contactor_lockout_active")
        self.assertEqual(safety._victron_ess_balance_telemetry_precheck_reason(svc), (False, "cached_inputs"))
        self.assertEqual(
            safety._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(
                    auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
                    _auto_cached_inputs_used=False,
                    _phase_switch_state="",
                    _contactor_fault_active_reason="",
                    _contactor_lockout_reason="",
                )
            ),
            (True, "clean_not_required"),
        )
        self.assertEqual(
            safety._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(
                    auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
                    _auto_cached_inputs_used=False,
                    _phase_switch_state="switching",
                    _contactor_fault_active_reason="",
                    _contactor_lockout_reason="",
                )
            ),
            (False, "phase_switch_active"),
        )
        self.assertEqual(
            safety._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(
                    auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
                    _auto_cached_inputs_used=False,
                    _phase_switch_state="",
                    _contactor_fault_active_reason="fault",
                    _contactor_lockout_reason="",
                )
            ),
            (False, "contactor_fault_active"),
        )
        self.assertIsNone(
            safety._victron_ess_balance_power_window_reason(
                SimpleNamespace(_victron_ess_balance_telemetry_last_ac_power_w=100.0),
                {"battery_combined_ac_power_w": 120.0},
            )
        )
        self.assertIsNone(
            safety._victron_ess_balance_telemetry_window_reason(
                SimpleNamespace(
                    _victron_ess_balance_telemetry_last_grid_interaction_w=10.0,
                    _victron_ess_balance_telemetry_last_ac_power_w=100.0,
                    _victron_ess_balance_telemetry_last_ev_power_w=0.0,
                    charging_started_at=None,
                    virtual_startstop=0,
                ),
                {"battery_combined_grid_interaction_w": 15.0, "battery_combined_ac_power_w": 120.0},
            )
        )
        self.assertEqual(
            safety._victron_ess_balance_telemetry_window_reason(
                SimpleNamespace(
                    _victron_ess_balance_telemetry_last_grid_interaction_w=10.0,
                    _victron_ess_balance_telemetry_last_ac_power_w=0.0,
                    _victron_ess_balance_telemetry_last_ev_power_w=0.0,
                    charging_started_at=None,
                    virtual_startstop=0,
                ),
                {"battery_combined_grid_interaction_w": 15.0, "battery_combined_ac_power_w": 1500.0},
            ),
            "foreign_power_event",
        )

        with patch.object(safety, "_victron_ess_balance_telemetry_precheck_reason", return_value=None), patch.object(
            safety, "_victron_ess_balance_telemetry_window_reason", return_value=None
        ):
            self.assertEqual(safety._victron_ess_balance_telemetry_is_clean(svc, {}, 5.0), (False, "error_inside_deadband"))

        kept = safety._victron_ess_balance_kept_action_changes([None, {"at": None}, {"at": 1.0}, {"at": 20.0}], 10.0)
        self.assertEqual(kept, [{"at": 20.0}])
        self.assertEqual(safety._victron_ess_balance_note_action_direction(svc, "idle", 30.0), 1)
        self.assertFalse(safety._victron_ess_balance_should_enter_oscillation_lockout(svc, 5))

        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 3,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_key": "profile",
        }
        recovery._victron_ess_balance_refresh_stable_tuning(svc, metrics, 40.0)
        self.assertEqual(svc._victron_ess_balance_last_stable_profile_key, "profile")
        self.assertIsNotNone(svc._victron_ess_balance_conservative_tuning)
        self.assertFalse(recovery._victron_ess_balance_has_minimum_stability(0.7))
        self.assertFalse(recovery._victron_ess_balance_should_rollback_stable_tuning(svc, {}, 41.0))
        no_cons = SimpleNamespace(
            _victron_ess_balance_conservative_tuning=None,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=50.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=200.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=20.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
        )
        recovery._victron_ess_balance_ensure_conservative_tuning(no_cons)
        self.assertEqual(no_cons._victron_ess_balance_conservative_tuning["kp"], 0.2)
        svc._victron_ess_balance_last_stable_tuning = None
        self.assertEqual(recovery._victron_ess_balance_restore_target(svc, "reason")[1], "conservative_fallback")

        with patch.object(recovery, "_victron_ess_balance_restored_activation_mode", return_value=""):
            recovery._apply_victron_ess_balance_restored_tuning(
                svc,
                {
                    "kp": 0.1,
                    "ki": 0.01,
                    "kd": 0.0,
                    "deadband_watts": 50.0,
                    "max_abs_watts": 200.0,
                    "ramp_rate_watts_per_second": 20.0,
                },
            )
        self.assertEqual(svc.auto_battery_discharge_balance_victron_bias_kp, 0.1)
        self.assertEqual(sources._victron_ess_balance_ev_power_w(svc), 2300.0)
        self.assertTrue(sources._victron_ess_balance_ev_active(svc))
        svc.charging_started_at = None
        self.assertTrue(sources._victron_ess_balance_ev_active(svc))
        svc.learned_charge_power_watts = None
        self.assertTrue(sources._victron_ess_balance_ev_active(svc))
        svc.virtual_startstop = 0
        svc.charging_started_at = 2.0
        self.assertTrue(sources._victron_ess_balance_ev_active(svc))



if __name__ == "__main__":
    unittest.main()
