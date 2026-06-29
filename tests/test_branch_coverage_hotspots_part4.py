# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronApplyCasesPart2:
    def test_victron_apply_state_wrapper_methods_delegate_to_profile_helpers(self) -> None:
        from venus_evcharger.update.victron_ess_balance_apply import _UpdateCycleVictronEssBalanceApply

        controller = _UpdateCycleVictronEssBalanceApply()
        service = SimpleNamespace()

        with patch("venus_evcharger.update.victron_ess_balance_apply._reset_victron_ess_balance_pid_state") as reset_pid:
            controller._reset_victron_ess_balance_pid(service)
            reset_pid.assert_called_once_with(service)

        with patch(
            "venus_evcharger.update.victron_ess_balance_apply._reset_victron_ess_balance_pid_integral_state"
        ) as reset_integral:
            controller._reset_victron_ess_balance_pid_integral(service, aggressive=True)
            reset_integral.assert_called_once_with(service, True)

        with patch("venus_evcharger.update.victron_ess_balance_apply._record_victron_ess_balance_tracking_command") as record:
            controller._record_victron_ess_balance_command(service, 10.0, 75.0, -25.0, "profile")
            record.assert_called_once_with(service, 10.0, 75.0, -25.0, "profile")

        with patch(
            "venus_evcharger.update.victron_ess_balance_apply._clear_victron_ess_balance_tracking_episode_state"
        ) as clear_episode:
            controller._clear_victron_ess_balance_tracking_episode(service)
            clear_episode.assert_called_once_with(service)

    def test_victron_apply_prepare_and_telemetry_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=20.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=0.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=100.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="svc",
            auto_battery_discharge_balance_victron_bias_path="/path",
            auto_energy_sources=(SimpleNamespace(source_id="alpha"),),
            _last_auto_metrics={},
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _victron_ess_balance_last_setpoint_w=None,
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_telemetry_last_command_at=100.0,
            _victron_ess_balance_telemetry_last_command_error_w=-60.0,
            _victron_ess_balance_telemetry_last_command_setpoint_w=70.0,
            _victron_ess_balance_telemetry_last_command_profile_key="profile",
            _victron_ess_balance_learning_profiles={"profile": {"delay_samples": 0, "gain_samples": 0, "settled_count": 0, "overshoot_count": 0}},
        )
        metrics = controller._victron_ess_balance_default_metrics()
        cluster = {
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_combined_grid_interaction_w": -150.0,
            "battery_combined_ac_power_w": 800.0,
            "expected_near_term_export_w": 120.0,
            "expected_near_term_import_w": 0.0,
            "battery_combined_pv_input_power_w": 300.0,
            "battery_sources": [
                {
                    "source_id": "victron",
                    "online": True,
                    "soc": 60.0,
                    "discharge_balance_reserve_floor_soc": 35.0,
                    "discharge_balance_error_w": -60.0,
                    "discharge_balance_control_connector_type": "dbus",
                    "discharge_balance_control_support": "supported",
                },
                {"source_id": "hybrid", "online": True},
            ],
        }
        controller._victron_ess_balance_ev_power_w = MagicMock(return_value=0.0)
        controller._victron_ess_balance_telemetry_is_clean = MagicMock(return_value=(True, "clean"))
        controller._populate_victron_ess_balance_telemetry_metrics = MagicMock()

        command_state = {
            "command_at": 100.0,
            "command_error_w": -60.0,
            "command_setpoint_w": 70.0,
            "command_profile_key": "profile",
            "command_response_recorded": False,
            "command_overshoot_recorded": False,
            "command_settled_recorded": False,
        }
        overshoot_active, settling_active = controller._victron_ess_balance_process_clean_episode(
            service,
            105.0,
            -10.0,
            command_state,
            10.0,
            50.0,
            20.0,
        )
        self.assertFalse(overshoot_active)
        self.assertFalse(settling_active)

        service._victron_ess_balance_telemetry_last_command_at = None
        controller._update_victron_ess_balance_telemetry(service, 105.5, cluster, -12.0, metrics, "profile")
        self.assertFalse(service._victron_ess_balance_telemetry_settling_active)
        service._victron_ess_balance_telemetry_last_command_at = 100.0
        controller._update_victron_ess_balance_telemetry(service, 106.0, cluster, -10.0, metrics, "profile")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "clean")
        self.assertEqual(service._victron_ess_balance_telemetry_last_observed_error_w, -10.0)

        with patch.object(controller, "_prepare_victron_ess_balance_learning_state", return_value={"key": "profile"}), patch.object(
            controller, "_victron_ess_balance_safety_block_reason", return_value="blocked"
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_profile(service, 100.0, cluster, cluster["battery_sources"][0], -60.0, metrics),
                (None, "blocked"),
            )

        with patch.object(controller, "_prepare_victron_ess_balance_learning_state", return_value={"key": "profile"}), patch.object(
            controller, "_victron_ess_balance_safety_block_reason", return_value=""
        ), patch.object(controller, "_victron_ess_balance_activation_allowed", return_value=False):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_profile(service, 100.0, cluster, cluster["battery_sources"][0], -60.0, metrics),
                (None, "activation-mode-blocked"),
            )

    def test_victron_apply_composite_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=False,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=False,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=20.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=0.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=100.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_service="svc",
            auto_battery_discharge_balance_victron_bias_path="/path",
            _last_auto_metrics={},
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
        )
        controller.apply_victron_ess_balance_bias(service, 10.0, True)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_enabled"], 0)

        service.auto_battery_discharge_balance_victron_bias_enabled = True
        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_state",
            return_value=({}, None, None, "blocked"),
        ), patch.object(controller, "_restore_victron_ess_balance_base_setpoint") as restore_base:
            controller.apply_victron_ess_balance_bias(service, 11.0, True)
            restore_base.assert_called_once()

        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_state",
            return_value=({"cluster": 1}, 25.0, "profile", ""),
        ), patch.object(controller, "_apply_victron_ess_balance_tracking") as apply_tracking:
            controller.apply_victron_ess_balance_bias(service, 12.0, True)
            apply_tracking.assert_called_once()

        metrics = {}
        learning_profile = {"key": "profile", "action_direction": "more_export"}
        with patch.object(controller, "_victron_ess_balance_learning_profile", return_value=learning_profile), patch.object(
            controller, "_merge_victron_ess_balance_learning_profile_metrics"
        ) as merge_metrics, patch.object(controller, "_victron_ess_balance_refresh_stable_tuning") as refresh_tuning, patch.object(
            controller, "_victron_ess_balance_note_action_direction", return_value=2
        ) as note_direction, patch.object(controller, "_populate_victron_ess_balance_runtime_safety_metrics") as populate_safety:
            returned = controller._prepare_victron_ess_balance_learning_state(service, 20.0, {"c": 1}, {"source_id": "victron"}, -20.0, metrics)
            self.assertEqual(returned, learning_profile)
            merge_metrics.assert_called_once()
            refresh_tuning.assert_called_once()
            note_direction.assert_called_once()
            populate_safety.assert_called_once()
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_oscillation_direction_change_count"], 2)

        with patch.object(controller, "_maybe_auto_apply_victron_ess_balance_recommendation") as maybe_auto_apply, patch.object(
            controller, "_merge_victron_ess_balance_metrics"
        ) as merge_metrics:
            controller._finalize_victron_ess_balance_metrics(service, 13.0, {})
            maybe_auto_apply.assert_called_once()
            merge_metrics.assert_called_once()

        metrics = {}
        with patch.object(controller, "_victron_ess_balance_pid_output", return_value=12.0):
            self.assertEqual(controller._victron_ess_balance_tracking_setpoint(service, 14.0, -40.0, metrics), 62.0)
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "tracking")

        service._victron_ess_balance_last_setpoint_w = 60.0
        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            metrics = {}
            controller._victron_ess_balance_apply_write_outcome(service, 15.0, 70.0, -20.0, "profile", metrics)
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "applied")

        with patch.object(controller, "_victron_ess_balance_update_tracking_telemetry") as update_telemetry, patch.object(
            controller, "_victron_ess_balance_tracking_write_state"
        ) as write_state, patch.object(controller, "_finalize_victron_ess_balance_metrics") as finalize_metrics:
            controller._apply_victron_ess_balance_tracking(service, 16.0, {"cluster": 1}, -20.0, "profile", {})
            update_telemetry.assert_called_once()
            write_state.assert_called_once()
            finalize_metrics.assert_called_once()

        with patch.object(controller, "_populate_victron_ess_balance_telemetry_metrics"), patch.object(
            controller, "_maybe_auto_apply_victron_ess_balance_recommendation"
        ), patch.object(controller, "_merge_victron_ess_balance_metrics"), patch.object(
            controller, "_victron_ess_balance_should_write", return_value=True
        ), patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            metrics = {}
            service._victron_ess_balance_last_setpoint_w = 70.0
            controller._restore_victron_ess_balance_base_setpoint(service, 17.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked-restored")

