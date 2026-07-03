# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronTelemetryCasesPart1:
    def test_telemetry_role_runtime_branches(self) -> None:
        harness = _TelemetryHarness()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=20.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            _victron_ess_balance_telemetry_last_command_at=100.0,
            _victron_ess_balance_telemetry_last_command_error_w=-60.0,
            _victron_ess_balance_telemetry_last_command_setpoint_w=70.0,
            _victron_ess_balance_telemetry_last_command_profile_key="profile",
            _victron_ess_balance_telemetry_command_response_recorded=False,
            _victron_ess_balance_telemetry_command_overshoot_recorded=False,
            _victron_ess_balance_telemetry_command_settled_recorded=False,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_pid_last_error_w=3.0,
            _victron_ess_balance_pid_last_output_w=4.0,
            _victron_ess_balance_pid_integral_output_w=5.0,
            _victron_ess_balance_learning_profiles={
                "profile": {
                    "delay_samples": 0,
                    "gain_samples": 0,
                    "settled_count": 0,
                    "overshoot_count": 0,
                    "stability_score": 0.0,
                    "response_variance_score": 0.0,
                }
            },
        )
        cluster = {
            "battery_combined_grid_interaction_w": -100.0,
            "battery_combined_ac_power_w": 800.0,
        }
        metrics: dict[str, object] = {}

        harness._victron_ess_balance_mark_overshoot(service, 101.0, "profile")
        self.assertEqual(service._victron_ess_balance_telemetry_overshoot_count, 1)
        self.assertEqual(harness.cooldowns[-1][1], "overshoot_detected")

        harness._update_victron_ess_balance_telemetry(service, 105.0, cluster, -10.0, metrics, "profile")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertTrue(metrics["telemetry_metrics_populated"])
        self.assertEqual(harness.refreshed_profiles[-1], "profile")
        self.assertTrue(harness.delay_updates)
        self.assertTrue(harness.gain_updates)

        service._victron_ess_balance_telemetry_last_command_profile_key = ""
        harness._record_victron_ess_balance_command(service, 111.0, 62.0, -14.0, "fallback")
        self.assertEqual(service._victron_ess_balance_telemetry_last_command_setpoint_w, 62.0)
        harness._clear_victron_ess_balance_tracking_episode(service)
        self.assertIsNone(service._victron_ess_balance_telemetry_last_command_at)
        harness._reset_victron_ess_balance_pid(service)
        self.assertEqual(service._victron_ess_balance_pid_last_output_w, 0.0)
        harness._reset_victron_ess_balance_pid_integral(service)
        self.assertEqual(service._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertAlmostEqual(harness._victron_ess_balance_stability_score_values(0, 0, None, None), 0.85)

        no_gain_updates = len(harness.gain_updates)
        harness._victron_ess_balance_maybe_record_gain(service, "profile", 0.0, 0.5)
        self.assertEqual(len(harness.gain_updates), no_gain_updates)

        overshoot_state = {"command_overshoot_recorded": False}
        harness._victron_ess_balance_maybe_mark_overshoot(service, 112.0, 12.0, overshoot_state, "profile", -8.0, 12.0, 2.0)
        self.assertTrue(overshoot_state["command_overshoot_recorded"])

        command_state = harness._victron_ess_balance_telemetry_command_state(service, "fallback")
        self.assertEqual(command_state["command_profile_key"], "fallback")


