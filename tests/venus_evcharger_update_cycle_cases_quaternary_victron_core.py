# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class _UpdateCycleQuaternaryVictronCoreCases:
    def test_victron_ess_balance_bias_applies_pid_output_and_tracks_setpoint(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
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
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _auto_cached_inputs_used=False,
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
                    {
                        "source_id": "hybrid",
                        "online": True,
                    },
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.victron_ess_balance.components.writer, "write_setpoint", return_value=True) as write_mock:
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)

        write_mock.assert_called_once_with(
            service,
            -50.0,
            intent="tracking",
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_source_error_w"], -500.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_pid_output_w"], -100.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_setpoint_w"], -50.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"], "applied")

    def test_victron_ess_balance_bias_restores_base_setpoint_when_auto_mode_stops(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
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
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={},
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=-50.0,
            _victron_ess_balance_pid_last_error_w=-500.0,
            _victron_ess_balance_pid_last_at=90.0,
            _victron_ess_balance_pid_integral_output_w=-25.0,
            _victron_ess_balance_pid_last_output_w=-100.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.victron_ess_balance.components.writer, "write_setpoint", return_value=True) as write_mock:
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, False)

        write_mock.assert_called_once_with(
            service,
            50.0,
            intent="restore",
        )
        self.assertIsNone(service._victron_ess_balance_last_setpoint_w)
        self.assertEqual(service._victron_ess_balance_pid_last_output_w, 0.0)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"],
            "auto-mode-inactive-restored",
        )

    def test_victron_ess_balance_bias_support_mode_blocks_experimental_when_requested(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="supported_only",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _auto_cached_inputs_used=False,
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -400.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "experimental",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.victron_ess_balance.components.writer, "write_setpoint", return_value=True) as write_mock:
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            self.assertEqual(
                service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"],
                "victron-source-support-blocked",
            )
            write_mock.assert_not_called()
            service.auto_battery_discharge_balance_victron_bias_support_mode = "allow_experimental"
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 101.0, True)

        write_mock.assert_called_once_with(
            service,
            -30.0,
            intent="tracking",
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"], "applied")

    def test_victron_ess_balance_bias_learns_response_delay_and_gain(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
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
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
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

        with patch.object(controller.components.victron_ess_balance.components.writer, "write_setpoint", return_value=True):
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -200.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 104.0, True)

        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_response_delay_seconds"],
            4.0,
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_estimated_gain"], 3.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_active"], 0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_count"], 0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_settling_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_settled_count"], 0)
        self.assertIsNotNone(service._last_auto_metrics["battery_discharge_balance_victron_bias_stability_score"])

    def test_victron_ess_balance_bias_detects_overshoot(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
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
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
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

        with patch.object(controller.components.victron_ess_balance.components.writer, "write_setpoint", return_value=True):
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = 150.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 103.0, True)

        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_count"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_settling_active"], 0)
        self.assertLess(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_stability_score"],
            1.0,
        )

    def test_victron_ess_balance_bias_recommends_more_relaxed_tuning_when_stable(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_telemetry_response_delay_seconds=4.0,
            _victron_ess_balance_telemetry_estimated_gain=2.5,
            _victron_ess_balance_telemetry_stability_score=0.9,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_settled_count=3,
            _victron_ess_balance_telemetry_delay_samples=2,
            _victron_ess_balance_telemetry_gain_samples=2,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        metrics = controller.components.victron_ess_balance.components.executor._victron_ess_balance_default_metrics()
        controller.components.victron_ess_balance.components.recommendation._populate_victron_ess_balance_telemetry_metrics(service, metrics)

        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_ki"], 0.022)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kd"], 0.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 80.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"],
            55.0,
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_activation_mode"], "always")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommendation_reason"], "can_relax_conservatism")
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommendation_ini_snippet"],
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=80\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=always",
        )
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommendation_hint"],
            "Telemetry looks stable; you can cautiously relax the current "
            "Victron bias tuning (confidence 0.90).",
        )
        self.assertGreater(metrics["battery_discharge_balance_victron_bias_recommendation_confidence"], 0.6)

    def test_victron_ess_balance_bias_recommends_more_conservative_tuning_on_overshoot(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_telemetry_response_delay_seconds=3.0,
            _victron_ess_balance_telemetry_estimated_gain=2.0,
            _victron_ess_balance_telemetry_stability_score=0.4,
            _victron_ess_balance_telemetry_overshoot_count=1,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_delay_samples=2,
            _victron_ess_balance_telemetry_gain_samples=2,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        metrics = controller.components.victron_ess_balance.components.executor._victron_ess_balance_default_metrics()
        controller.components.victron_ess_balance.components.recommendation._populate_victron_ess_balance_telemetry_metrics(service, metrics)

        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kp"], 0.16)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_ki"], 0.01)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"],
            35.0,
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommendation_reason"], "overshoot_risk")
        self.assertIn(
            "AutoBatteryDischargeBalanceVictronBiasKp=0.16",
            metrics["battery_discharge_balance_victron_bias_recommendation_ini_snippet"],
        )
        self.assertIn("overshoot risk", metrics["battery_discharge_balance_victron_bias_recommendation_hint"].lower())

    def test_victron_ess_balance_bias_skips_learning_when_telemetry_is_not_clean(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
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
            _auto_cached_inputs_used=False,
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

        with patch.object(controller.components.victron_ess_balance.components.writer, "write_setpoint", return_value=True):
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 100.0, True)
            service._auto_cached_inputs_used = True
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -250.0
            controller.components.victron_ess_balance.apply_victron_ess_balance_bias(service, 104.0, True)

        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 0)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"],
            "cached_inputs",
        )
        self.assertIsNone(service._last_auto_metrics["battery_discharge_balance_victron_bias_response_delay_seconds"])
