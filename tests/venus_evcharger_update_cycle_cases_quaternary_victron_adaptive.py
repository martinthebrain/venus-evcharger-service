# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class _UpdateCycleQuaternaryVictronAdaptiveCases:
    def test_victron_ess_balance_bias_enters_oscillation_lockout_after_repeated_direction_changes(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=True,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=120.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes=2,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds=90.0,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_combined_grid_interaction_w": -300.0,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.victron_ess_balance.components.writer, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = 400.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 101.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -350.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 102.0, True)

        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_oscillation_lockout_active"],
            1,
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_oscillation_lockout_reason"],
            "direction_change_oscillation",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"],
            "overshoot-cooldown-active-restored",
        )

    def test_victron_ess_balance_bias_auto_apply_is_stepwise_and_holds_observation_window(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=True,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.8,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=2,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.75,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.5,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=30.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            _victron_ess_balance_auto_apply_generation=0,
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        metrics = controller.components.victron_ess_balance.components.executor._victron_ess_balance_default_metrics()
        metrics.update(
            {
                "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                "battery_discharge_balance_victron_bias_learning_profile_sample_count": 3,
                "battery_discharge_balance_victron_bias_recommendation_profile_key": "more_export:export:day:above_reserve_band",
                "battery_discharge_balance_victron_bias_learning_profile_key": "more_export:export:day:above_reserve_band",
                "battery_discharge_balance_victron_bias_recommended_deadband_watts": 80.0,
                "battery_discharge_balance_victron_bias_recommended_max_abs_watts": 600.0,
                "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": 60.0,
                "battery_discharge_balance_victron_bias_recommended_kp": 0.25,
                "battery_discharge_balance_victron_bias_recommended_ki": 0.03,
                "battery_discharge_balance_victron_bias_recommended_kd": 0.01,
                "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_only",
            }
        )

        controller.components.victron_ess_balance.components.adaptive._maybe_auto_apply_victron_ess_balance_recommendation(service, metrics, 100.0)
        first_deadband = service.auto_battery_discharge_balance_victron_bias_deadband_watts

        self.assertEqual(first_deadband, 90.0)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_auto_apply_last_param"],
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "applied_step")

        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = ""
        controller.components.victron_ess_balance.components.adaptive._maybe_auto_apply_victron_ess_balance_recommendation(service, metrics, 110.0)

        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_deadband_watts, first_deadband)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "observation_window_active")

    def test_victron_ess_balance_bias_rolls_back_to_last_stable_tuning_when_observation_turns_unstable(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=True,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.8,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=2,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.75,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.5,
            auto_battery_discharge_balance_victron_bias_deadband_watts=90.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=550.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=55.0,
            auto_battery_discharge_balance_victron_bias_kp=0.23,
            auto_battery_discharge_balance_victron_bias_ki=0.022,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_activation_mode="export_only",
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=0.45,
            _victron_ess_balance_auto_apply_observe_until=120.0,
            _victron_ess_balance_last_stable_tuning={
                "kp": 0.2,
                "ki": 0.02,
                "kd": 0.0,
                "deadband_watts": 100.0,
                "max_abs_watts": 500.0,
                "ramp_rate_watts_per_second": 50.0,
                "activation_mode": "always",
            },
            _victron_ess_balance_last_stable_profile_key="more_export:export:day:above_reserve_band",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        metrics = controller.components.victron_ess_balance.components.executor._victron_ess_balance_default_metrics()
        metrics.update(
            {
                "battery_discharge_balance_victron_bias_overshoot_active": 1,
                "battery_discharge_balance_victron_bias_stability_score": 0.3,
            }
        )

        controller.components.victron_ess_balance.components.adaptive._maybe_auto_apply_victron_ess_balance_recommendation(service, metrics, 110.0)

        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_kp, 0.2)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_deadband_watts, 100.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_active"], 1)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_reason"], "unstable_observation_window")

    def test_victron_ess_balance_bias_learns_profiled_telemetry_for_export_day_above_reserve(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            _last_auto_metrics={},
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            dbus_method_timeout_seconds=1.0,
            _get_system_bus=MagicMock(),
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -450.0,
                "battery_combined_pv_input_power_w": 1800.0,
                "expected_near_term_export_w": 600.0,
                "expected_near_term_import_w": 0.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "soc": 62.0,
                        "discharge_balance_reserve_floor_soc": 35.0,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
            _victron_ess_balance_learning_profiles={},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.victron_ess_balance.components.writer, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -320.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 104.0, True)

        profile = service._victron_ess_balance_learning_profiles[
            "more_export:export:day:above_reserve_band:ev_idle:pv_strong:mid_band"
        ]
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_key"],
            "more_export:export:day:above_reserve_band:ev_idle:pv_strong:mid_band",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_action_direction"],
            "more_export",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_site_regime"],
            "export",
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_direction"], "export")
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_day_phase"], "day")
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_reserve_phase"],
            "above_reserve_band",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_ev_phase"],
            "ev_idle",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_pv_phase"],
            "pv_strong",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase"],
            "mid_band",
        )
        self.assertEqual(profile["delay_samples"], 1)
        self.assertEqual(profile["gain_samples"], 1)
        self.assertGreater(profile["response_delay_seconds"], 0.0)
        self.assertGreater(profile["estimated_gain"], 0.0)

    def test_victron_ess_balance_bias_enters_overshoot_cooldown_and_suspends_auto_apply(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.05,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=30.0,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.victron_ess_balance.components.writer, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = 250.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 103.0, True)

        self.assertTrue(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_active"])
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_reason"],
            "overshoot_detected",
        )
        self.assertEqual(service._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_reason"],
            "overshoot_cooldown",
        )

    def test_victron_ess_balance_bias_rejects_learning_when_grid_or_ev_jumps(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _charger_estimated_power_w=200.0,
            _victron_ess_balance_telemetry_last_grid_interaction_w=-200.0,
            _victron_ess_balance_telemetry_last_ac_power_w=400.0,
            _victron_ess_balance_telemetry_last_ev_power_w=200.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        clean, reason = controller.components.victron_ess_balance.components.safety._victron_ess_balance_telemetry_is_clean(
            service,
            {
                "battery_combined_grid_interaction_w": -900.0,
                "battery_combined_ac_power_w": 400.0,
            },
            -500.0,
        )
        self.assertFalse(clean)
        self.assertEqual(reason, "grid_unstable")

        service._charger_estimated_power_w = 800.0
        clean, reason = controller.components.victron_ess_balance.components.safety._victron_ess_balance_telemetry_is_clean(
            service,
            {
                "battery_combined_grid_interaction_w": -220.0,
                "battery_combined_ac_power_w": 400.0,
            },
            -500.0,
        )
        self.assertFalse(clean)
        self.assertEqual(reason, "ev_load_jump")

    def test_victron_ess_balance_bias_prefers_active_profile_for_recommendation(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_active_learning_profile_key="more_export:export:day:above_reserve_band",
            _victron_ess_balance_learning_profiles={
                "more_export:export:day:above_reserve_band": {
                    "key": "more_export:export:day:above_reserve_band",
                    "action_direction": "more_export",
                    "site_regime": "export",
                    "direction": "export",
                    "day_phase": "day",
                    "reserve_phase": "above_reserve_band",
                    "response_delay_seconds": 4.0,
                    "delay_samples": 2,
                    "estimated_gain": 2.5,
                    "gain_samples": 2,
                    "overshoot_count": 0,
                    "settled_count": 3,
                    "stability_score": 0.9,
                }
            },
            _victron_ess_balance_telemetry_response_delay_seconds=12.0,
            _victron_ess_balance_telemetry_estimated_gain=0.5,
            _victron_ess_balance_telemetry_stability_score=0.2,
            _victron_ess_balance_telemetry_overshoot_count=3,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_delay_samples=4,
            _victron_ess_balance_telemetry_gain_samples=4,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        metrics = controller.components.victron_ess_balance.components.executor._victron_ess_balance_default_metrics()
        controller.components.victron_ess_balance.components.recommendation._populate_victron_ess_balance_telemetry_metrics(service, metrics)

        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommendation_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommendation_reason"], "can_relax_conservatism")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kd"], 0.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 90.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommended_activation_mode"],
            "export_and_above_reserve_band",
        )
