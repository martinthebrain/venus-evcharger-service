# SPDX-License-Identifier: GPL-3.0-or-later
import configparser

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.service_roles_cases_common import _ControlService, _configured_control_service
from venus_evcharger.control import ControlApiAuditTrail, ControlApiIdempotencyStore, ControlApiRateLimiter, ControlCommand
from venus_evcharger.control.events import ControlApiEventBus


class _ServiceRolesControlCases:
    def test_control_api_mixin_builds_commands_and_manages_http_server(self):
        service = _ControlService()
        service._write_controller = MagicMock()
        service._write_controller.build_control_command_from_payload.return_value = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
        )
        service._control_api_server = MagicMock(bound_host="127.0.0.1", bound_port=8765)
        service.control_api_enabled = True

        command = service._control_command_from_payload({"name": "set_mode", "value": 1}, source="http")
        service._start_control_api_server()
        service._stop_control_api_server()

        self.assertEqual(command.name, "set_mode")
        service._write_controller.build_control_command_from_payload.assert_called_once_with(
            {"name": "set_mode", "value": 1},
            source="http",
        )
        service._control_api_server.start.assert_called_once_with()
        self.assertEqual(service.control_api_listen_host, "127.0.0.1")
        self.assertEqual(service.control_api_listen_port, 8765)
        service._control_api_server.stop.assert_called_once_with()

    def test_control_api_mixin_rejects_non_command_controller_payload(self):
        service = _ControlService()
        service._write_controller = MagicMock()
        service._write_controller.build_control_command_from_payload.return_value = {"name": "set_mode"}

        with self.assertRaises(TypeError):
            service._control_command_from_payload({"name": "set_mode", "value": 1}, source="http")

    def test_control_api_runtime_components_replace_invalid_cached_instances_and_reuse_valid(self):
        service = _ControlService()
        service._control_api_audit_trail_instance = "bad"
        service._control_api_idempotency_store_instance = "bad"
        service._control_api_rate_limiter_instance = "bad"
        service._control_api_event_bus_instance = "bad"

        audit_trail = service._control_api_audit_trail()
        idempotency_store = service._control_api_idempotency_store()
        rate_limiter = service._control_api_rate_limiter()
        event_bus = service._control_api_event_bus()

        self.assertIsInstance(audit_trail, ControlApiAuditTrail)
        self.assertIsInstance(idempotency_store, ControlApiIdempotencyStore)
        self.assertIsInstance(rate_limiter, ControlApiRateLimiter)
        self.assertIsInstance(event_bus, ControlApiEventBus)
        self.assertIs(service._control_api_audit_trail(), audit_trail)
        self.assertIs(service._control_api_idempotency_store(), idempotency_store)
        self.assertIs(service._control_api_rate_limiter(), rate_limiter)
        self.assertIs(service._control_api_event_bus(), event_bus)

    def test_control_api_mixin_exposes_capabilities_summary_runtime_and_recommendation_payloads(self):
        service = _configured_control_service()

        capabilities = service._control_api_capabilities_payload()
        automation = service._state_api_automation_payload()
        summary = service._state_api_summary_payload()
        runtime = service._state_api_runtime_payload()
        recommendation = service._state_api_victron_bias_recommendation_payload()

        self.assertTrue(capabilities["auth_required"])
        self.assertIn("set_mode", capabilities["command_names"])
        self.assertIn("/v1/capabilities", capabilities["endpoints"])
        self.assertIn("/v1/state/automation", capabilities["endpoints"])
        self.assertIn("/v1/state/healthz", capabilities["endpoints"])
        self.assertEqual(automation["kind"], "automation")
        self.assertEqual(automation["state"]["command_endpoint"], "/v1/control/command")
        self.assertEqual(automation["state"]["safe_write"]["if_match_header"], "If-Match")
        self.assertIn("set_mode", automation["state"]["writable"]["command_names"])
        self.assertEqual(automation["state"]["operational"]["mode"], 1)
        self.assertEqual(automation["state"]["diagnostics"]["/Auto/LastShellyReadAge"], 0.2)
        self.assertIn("/v1/events", capabilities["versioning"]["experimental_endpoints"])
        self.assertTrue(capabilities["features"]["command_audit_trail"])
        self.assertTrue(capabilities["features"]["event_kind_filters"])
        self.assertTrue(capabilities["features"]["event_retry_hints"])
        self.assertTrue(capabilities["features"]["multi_phase_selection"])
        self.assertTrue(capabilities["features"]["optimistic_concurrency"])
        self.assertTrue(capabilities["features"]["per_command_request_schemas"])
        self.assertTrue(capabilities["features"]["rate_limiting"])
        self.assertEqual(capabilities["auth_scopes"], ["control_admin", "control_basic", "read", "update_admin"])
        self.assertEqual(capabilities["command_scope_requirements"]["set_mode"], "control_basic")
        self.assertEqual(capabilities["command_scope_requirements"]["trigger_software_update"], "update_admin")
        self.assertEqual(capabilities["topology"]["charger_backend"], "goe_charger")
        self.assertEqual(capabilities["supported_phase_selections"], ["P1", "P1_P2", "P1_P2_P3"])
        self.assertEqual(capabilities["available_modes"], [0, 1, 2])
        self.assertEqual(summary["kind"], "summary")
        self.assertEqual(summary["summary"], "mode=1 enable=1")
        self.assertEqual(runtime["kind"], "runtime")
        self.assertEqual(runtime["state"]["mode"], 1)
        self.assertEqual(runtime["state"]["combined_battery_soc"], 62.0)
        self.assertEqual(recommendation["kind"], "victron-bias-recommendation")
        self.assertEqual(recommendation["state"]["active_learning_profile_key"], "more_export:export:day:above_reserve_band")
        self.assertEqual(recommendation["state"]["active_learning_profile"]["action_direction"], "more_export")
        self.assertEqual(recommendation["state"]["active_learning_profile"]["site_regime"], "export")
        self.assertEqual(recommendation["state"]["active_learning_profile"]["direction"], "export")
        self.assertEqual(recommendation["state"]["current_kp"], 0.2)
        self.assertEqual(recommendation["state"]["current_kd"], 0.0)
        self.assertEqual(recommendation["state"]["current_deadband_watts"], 100.0)
        self.assertEqual(recommendation["state"]["current_max_abs_watts"], 500.0)
        self.assertEqual(recommendation["state"]["recommended_kp"], 0.23)
        self.assertEqual(recommendation["state"]["recommended_kd"], 0.01)
        self.assertEqual(recommendation["state"]["recommended_deadband_watts"], 90.0)
        self.assertEqual(recommendation["state"]["recommended_max_abs_watts"], 550.0)
        self.assertEqual(recommendation["state"]["recommended_activation_mode"], "export_and_above_reserve_band")
        self.assertEqual(recommendation["state"]["recommendation_profile_key"], "more_export:export:day:above_reserve_band")
        self.assertTrue(recommendation["state"]["auto_apply_enabled"])
        self.assertTrue(recommendation["state"]["auto_apply_active"])
        self.assertEqual(recommendation["state"]["auto_apply_generation"], 2)
        self.assertIn("more_export:export:day:above_reserve_band", recommendation["state"]["learning_profiles"])
        self.assertEqual(
            recommendation["state"]["recommendation_ini_snippet"],
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band",
        )

    def test_control_api_dbus_diagnostics_ignores_non_mapping_publisher_payloads(self):
        service = _configured_control_service()
        service._dbus_publisher._diagnostic_counter_values.return_value = "bad"
        service._dbus_publisher._diagnostic_age_values.return_value = None

        diagnostics = service._state_api_dbus_diagnostics_payload()

        self.assertEqual(diagnostics["kind"], "dbus-diagnostics")
        self.assertEqual(diagnostics["state"], {})

    def test_control_api_mixin_operational_payload_exposes_balance_learning_and_bias_fields(self):
        service = _configured_control_service()
        operational = service._state_api_operational_payload()

        self.assertEqual(operational["kind"], "operational")
        self.assertEqual(operational["state"]["backend_mode"], "split")
        self.assertEqual(operational["state"]["auto_state"], "charging")
        service._last_auto_metrics["relay_intent"] = False
        operational_with_relay_intent = service._state_api_operational_payload()
        self.assertEqual(operational_with_relay_intent["state"]["auto_decision"]["relay_intent"], 0)
        self.assertEqual(operational["state"]["software_update_state"], "available")
        self.assertEqual(operational["state"]["runtime_overrides_path"], "/run/runtime.ini")
        self.assertEqual(operational["state"]["combined_battery_soc"], 62.0)
        self.assertEqual(operational["state"]["combined_battery_source_count"], 2)
        self.assertEqual(operational["state"]["combined_battery_charge_power_w"], 800.0)
        self.assertEqual(operational["state"]["combined_battery_discharge_power_w"], 1200.0)
        self.assertEqual(operational["state"]["combined_battery_net_power_w"], 400.0)
        self.assertEqual(operational["state"]["combined_battery_ac_power_w"], 1800.0)
        self.assertEqual(operational["state"]["combined_battery_pv_input_power_w"], 2600.0)
        self.assertEqual(operational["state"]["combined_battery_grid_interaction_w"], -350.0)
        self.assertEqual(operational["state"]["combined_battery_headroom_charge_w"], 900.0)
        self.assertEqual(operational["state"]["combined_battery_headroom_discharge_w"], 1100.0)
        self.assertEqual(operational["state"]["expected_near_term_export_w"], 425.0)
        self.assertEqual(operational["state"]["expected_near_term_import_w"], 50.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_mode"], "capacity_reserve_weighted")
        self.assertEqual(operational["state"]["battery_discharge_balance_target_distribution_mode"], "capacity_reserve_weighted")
        self.assertEqual(operational["state"]["battery_discharge_balance_error_w"], 250.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_max_abs_error_w"], 250.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_total_discharge_w"], 1200.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_eligible_source_count"], 2)
        self.assertEqual(operational["state"]["battery_discharge_balance_active_source_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_control_candidate_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_control_ready_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_supported_control_source_count"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_experimental_control_source_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_policy_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_warning_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_warning_error_w"], 250.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_warn_threshold_w"], 400.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_mode"], "export_only")
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_gate_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_start_error_w"], 500.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_penalty_w"], 0.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_policy_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_feasibility"], "partial")
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_gate_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_start_error_w"], 900.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_penalty_w"], 0.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_advisory_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_advisory_reason"], "only_some_sources_offer_a_write_path")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_topology_key"], "victron-bias-learning/v1/source=victron/service=com.victronenergy.settings/path=/Settings/CGwacs/AcPowerSetPoint/energy=battery,hybrid")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_activation_mode"], "export_and_above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_activation_gate_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_support_mode"], "supported_only")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_key"], "more_export:export:day:above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_action_direction"], "more_export")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_site_regime"], "export")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_direction"], "export")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_day_phase"], "day")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_reserve_phase"], "above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_source_error_w"], -320.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_pid_output_w"], -64.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_setpoint_w"], -14.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_response_delay_seconds"], 4.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_estimated_gain"], 2.5)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_overshoot_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_overshoot_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_settling_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_settled_count"], 3)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_stability_score"], 0.82)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "clean")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_oscillation_lockout_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_oscillation_lockout_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_oscillation_direction_change_count"], 2)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second"], 60.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts"], 550.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_ki"], 0.022)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_kd"], 0.01)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 90.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"], 55.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_activation_mode"], "export_and_above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommendation_confidence"], 0.81)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommendation_reason"], "can_relax_conservatism")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommendation_profile_key"], "more_export:export:day:above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommendation_ini_snippet"], "AutoBatteryDischargeBalanceVictronBiasKp=0.23\nAutoBatteryDischargeBalanceVictronBiasKi=0.022\nAutoBatteryDischargeBalanceVictronBiasKd=0.01\nAutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\nAutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\nAutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\nAutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommendation_hint"], "Telemetry looks stable; you can cautiously relax the current Victron bias tuning (confidence 0.81).")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_reason"], "applied")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_generation"], 2)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"], 1234.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_last_param"], "auto_battery_discharge_balance_victron_bias_deadband_watts")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_reason"], "stable")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_stable_profile_key"], "more_export:export:day:above_reserve_band")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_reason"], "applied")
        self.assertEqual(operational["state"]["combined_battery_average_confidence"], 0.75)
        self.assertEqual(operational["state"]["combined_battery_battery_source_count"], 1)
        self.assertEqual(operational["state"]["combined_battery_hybrid_inverter_source_count"], 1)
        self.assertEqual(operational["state"]["combined_battery_inverter_source_count"], 0)
        self.assertEqual(operational["state"]["combined_battery_learning_profile_count"], 2)
        self.assertEqual(operational["state"]["combined_battery_observed_max_charge_power_w"], 700.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_discharge_power_w"], 1400.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_ac_power_w"], 2000.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_pv_input_power_w"], 2600.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_grid_import_w"], 400.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_grid_export_w"], 600.0)
        self.assertEqual(operational["state"]["combined_battery_average_active_charge_power_w"], 566.6666666666666)
        self.assertEqual(operational["state"]["combined_battery_average_active_discharge_power_w"], 1150.0)
        self.assertAlmostEqual(operational["state"]["combined_battery_average_active_power_delta_w"], 166.66666666666666)
        self.assertAlmostEqual(operational["state"]["combined_battery_power_smoothing_ratio"], 0.8179824561403508)
        self.assertEqual(operational["state"]["combined_battery_typical_response_delay_seconds"], 6.0)
        self.assertAlmostEqual(operational["state"]["combined_battery_support_bias"], -0.2)
        self.assertAlmostEqual(operational["state"]["combined_battery_day_support_bias"], -1.0 / 3.0)
        self.assertEqual(operational["state"]["combined_battery_night_support_bias"], 0.0)
        self.assertEqual(operational["state"]["combined_battery_import_support_bias"], 1.0)
        self.assertAlmostEqual(operational["state"]["combined_battery_export_bias"], 1.0 / 3.0)
        self.assertEqual(operational["state"]["combined_battery_battery_first_export_bias"], 0.0)
        self.assertEqual(operational["state"]["combined_battery_reserve_band_floor_soc"], 45.0)
        self.assertEqual(operational["state"]["combined_battery_reserve_band_ceiling_soc"], 85.0)
        self.assertEqual(operational["state"]["combined_battery_reserve_band_width_soc"], 40.0)
        self.assertEqual(operational["state"]["combined_battery_direction_change_count"], 3)

    def test_control_api_operational_payload_ignores_non_mapping_worker_snapshot(self):
        service = _configured_control_service()
        service._get_worker_snapshot = lambda: ["not-a-mapping"]

        operational = service._state_api_operational_payload()

        self.assertIsNone(operational["state"]["combined_battery_soc"])
        self.assertEqual(operational["state"]["combined_battery_source_count"], 0)
        self.assertEqual(operational["state"]["combined_battery_learning_profile_count"], 0)

    def test_control_api_mixin_state_config_and_health_payloads(self):
        service = _configured_control_service()
        diagnostics = service._state_api_dbus_diagnostics_payload()
        topology = service._state_api_topology_payload()
        update = service._state_api_update_payload()
        config_effective = service._state_api_config_effective_payload()
        health = service._state_api_health_payload()
        healthz = service._state_api_healthz_payload()
        version = service._state_api_version_payload()
        build = service._state_api_build_payload()
        contracts = service._state_api_contracts_payload()
        snapshot = service._state_api_event_snapshot_payload()

        self.assertEqual(diagnostics["kind"], "dbus-diagnostics")
        self.assertEqual(diagnostics["state"]["/Auto/State"], "charging")
        self.assertEqual(diagnostics["state"]["/Auto/LastShellyReadAge"], 0.2)
        self.assertEqual(topology["kind"], "topology")
        self.assertEqual(topology["state"]["charger_backend"], "goe_charger")
        self.assertEqual(update["kind"], "update")
        self.assertEqual(config_effective["kind"], "config-effective")
        self.assertEqual(config_effective["state"]["runtime_overrides_path"], "/run/runtime.ini")
        self.assertEqual(config_effective["state"]["control_api_audit_path"], "/run/control-audit.jsonl")
        self.assertEqual(config_effective["state"]["control_api_idempotency_path"], "/run/control-idempotency.json")
        self.assertEqual(config_effective["state"]["control_api_rate_limit_max_requests"], 15)
        self.assertEqual(config_effective["state"]["control_api_rate_limit_window_seconds"], 7.5)
        self.assertEqual(config_effective["state"]["control_api_critical_cooldown_seconds"], 3.0)
        self.assertTrue(config_effective["state"]["companion_dbus_bridge_enabled"])
        self.assertTrue(config_effective["state"]["companion_source_services_enabled"])
        self.assertTrue(config_effective["state"]["companion_grid_service_enabled"])
        self.assertEqual(config_effective["state"]["companion_grid_authoritative_source"], "huawei")
        self.assertTrue(config_effective["state"]["companion_source_grid_services_enabled"])
        self.assertEqual(config_effective["state"]["companion_battery_deviceinstance"], 100)
        self.assertEqual(config_effective["state"]["companion_grid_deviceinstance"], 102)
        self.assertEqual(config_effective["state"]["companion_source_battery_deviceinstance_base"], 200)
        self.assertEqual(config_effective["state"]["companion_source_grid_deviceinstance_base"], 400)
        self.assertTrue(config_effective["state"]["auto_use_combined_battery_soc"])
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_policy_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_warn_error_watts"], 400.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_start_error_watts"], 500.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_max_penalty_watts"], 300.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_mode"], "export_only")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_reserve_margin_soc"], 5.0)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_coordination_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_coordination_start_error_watts"], 900.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_coordination_max_penalty_watts"], 150.0)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_service"], "com.victronenergy.settings")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_path"], "/Settings/CGwacs/AcPowerSetPoint")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_base_setpoint_watts"], 50.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_deadband_watts"], 100.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_support_mode"], "supported_only")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_kp"], 0.2)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_ki"], 0.02)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_kd"], 0.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_integral_limit_watts"], 250.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_max_abs_watts"], 500.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second"], 50.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_min_update_seconds"], 2.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_observation_window_seconds"], 30.0)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds"], 120.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes"], 3)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds"], 180.0)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_rollback_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_rollback_min_stability_score"], 0.45)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_require_clean_phases"])
        self.assertEqual(config_effective["state"]["auto_energy_source_ids"], ["battery", "hybrid"])
        self.assertEqual(config_effective["state"]["auto_energy_source_profiles"], {"battery": "dbus-battery", "hybrid": "huawei_ma_native_ap"})
        self.assertEqual(config_effective["state"]["auto_energy_source_profile_details"]["hybrid"]["vendor_name"], "Huawei")
        self.assertEqual(config_effective["state"]["auto_energy_source_profile_details"]["hybrid"]["platform"], "MA")
        self.assertEqual(config_effective["state"]["auto_energy_source_profile_details"]["hybrid"]["access_mode"], "native_ap")
        self.assertEqual(config_effective["state"]["companion_pvinverter_service_name"], "com.victronenergy.pvinverter.external_101")
        self.assertEqual(config_effective["state"]["companion_grid_service_name"], "com.victronenergy.grid.external_102")
        self.assertEqual(config_effective["state"]["companion_source_pvinverter_service_prefix"], "com.victronenergy.pvinverter.external")
        self.assertEqual(config_effective["state"]["companion_source_grid_service_prefix"], "com.victronenergy.grid.external")
        self.assertEqual(health["kind"], "health")
        self.assertEqual(health["state"]["command_audit_entries"], 0)
        self.assertEqual(health["state"]["command_audit_path"], "/run/control-audit.jsonl")
        self.assertEqual(health["state"]["idempotency_entries"], 0)
        self.assertEqual(health["state"]["idempotency_path"], "/run/control-idempotency.json")
        self.assertFalse(health["state"]["update_stale"])
        self.assertEqual(healthz["kind"], "healthz")
        self.assertTrue(healthz["state"]["alive"])
        self.assertEqual(version["kind"], "version")
        self.assertEqual(version["state"]["service_version"], "FW-1")
        self.assertEqual(build["kind"], "build")
        self.assertEqual(build["state"]["hardware_version"], "HW-1")
        self.assertEqual(contracts["kind"], "contracts")
        self.assertEqual(contracts["state"]["openapi_endpoint"], "/v1/openapi.json")
        self.assertIn("summary", snapshot)
        self.assertIn("health", snapshot)
        self.assertTrue(service._control_api_state_token())

    def test_control_api_payloads_prefer_config_over_conflicting_legacy_backend_attributes(self):
        service = _configured_control_service()
        config = configparser.ConfigParser()
        config["Backends"] = {
            "Mode": "split",
            "MeterType": "template_meter",
            "SwitchType": "template_switch",
            "ChargerType": "smartevse_charger",
        }
        service.config = config
        service.backend_mode = "combined"
        service.meter_backend_type = "shelly_combined"
        service.switch_backend_type = "shelly_combined"
        service.charger_backend_type = None

        topology = service._state_api_topology_payload()
        capabilities = service._control_api_capabilities_payload()
        config_effective = service._state_api_config_effective_payload()
        operational = service._state_api_operational_payload()

        self.assertEqual(topology["state"]["backend_mode"], "split")
        self.assertEqual(topology["state"]["meter_backend"], "template_meter")
        self.assertEqual(topology["state"]["switch_backend"], "template_switch")
        self.assertEqual(topology["state"]["charger_backend"], "smartevse_charger")
        self.assertEqual(capabilities["topology"]["charger_backend"], "smartevse_charger")
        self.assertEqual(config_effective["state"]["switch_backend"], "template_switch")
        self.assertEqual(operational["state"]["charger_backend"], "smartevse_charger")

    def test_control_api_mixin_health_payload_uses_stale_callback_and_event_bus_is_reused(self):
        service = _ControlService()
        service._last_health_reason = "init"
        service._is_update_stale = lambda now: now >= 0.0
        service.control_api_audit_path = "/run/control-audit.jsonl"
        service.control_api_audit_max_entries = 2
        service.control_api_idempotency_path = "/run/control-idempotency.json"
        service.control_api_idempotency_max_entries = 2

        health = service._state_api_health_payload()
        event_bus_a = service._control_api_event_bus()
        event_bus_b = service._control_api_event_bus()
        audit_a = service._control_api_audit_trail()
        audit_b = service._control_api_audit_trail()
        idem_a = service._control_api_idempotency_store()
        idem_b = service._control_api_idempotency_store()
        rate_a = service._control_api_rate_limiter()
        rate_b = service._control_api_rate_limiter()

        self.assertTrue(health["state"]["update_stale"])
        self.assertIs(event_bus_a, event_bus_b)
        self.assertIs(audit_a, audit_b)
        self.assertIs(idem_a, idem_b)
        self.assertIs(rate_a, rate_b)

    def test_control_api_health_payload_treats_non_callable_stale_hook_as_fresh(self):
        service = _ControlService()
        service._last_health_reason = "init"
        service._is_update_stale = "not-callable"
        service.control_api_audit_path = "/run/control-audit.jsonl"
        service.control_api_audit_max_entries = 2
        service.control_api_idempotency_path = "/run/control-idempotency.json"
        service.control_api_idempotency_max_entries = 2

        health = service._state_api_health_payload()

        self.assertFalse(health["state"]["update_stale"])

    def test_control_api_state_token_changes_when_snapshot_changes(self):
        service = _ControlService()
        first_token = service._control_api_state_token()
        service.virtual_mode = 2
        second_token = service._control_api_state_token()
        self.assertNotEqual(first_token, second_token)

    def test_control_api_mixin_records_runtime_only_command_audit_entries(self):
        service = _ControlService()
        service.control_api_audit_path = "/run/control-audit.jsonl"
        service.control_api_audit_max_entries = 2
        service.control_api_idempotency_path = "/run/control-idempotency.json"
        service.control_api_idempotency_max_entries = 2
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http", command_id="cmd-1")

        entry = service._record_control_api_command_audit(
            command=command,
            result={"status": "applied", "accepted": True},
            error=None,
            replayed=False,
            scope="control",
            client_host="127.0.0.1",
            status_code=200,
        )

        self.assertEqual(entry["seq"], 1)
        self.assertEqual(entry["command"]["name"], "set_mode")
        self.assertEqual(service._control_api_audit_trail().count(), 1)
        service._control_api_idempotency_store().put("idem-1", "fp", 200, {"ok": True})
        self.assertEqual(service._control_api_idempotency_store().count(), 1)

    def test_control_api_mixin_audit_payload_helpers_cover_dict_and_none_inputs(self):
        self.assertEqual(_ControlService._audit_command_payload({"name": "set_mode"}, "http"), {"name": "set_mode"})
        self.assertEqual(_ControlService._audit_command_payload(None, "http"), {})
        self.assertEqual(_ControlService._audit_result_payload({"status": "applied"}), {"status": "applied"})
        self.assertEqual(_ControlService._audit_result_payload(None), {})
        object_payload = _ControlService._audit_result_payload(
            SimpleNamespace(
                status="applied",
                accepted=True,
                applied=True,
                persisted=False,
                reversible_failure=False,
                external_side_effect_started=True,
                detail="ok",
            )
        )
        self.assertEqual(object_payload["status"], "applied")
        self.assertTrue(object_payload["external_side_effect_started"])

    def test_control_api_mixin_skips_disabled_server_and_can_create_one(self):
        service = _ControlService()
        service.control_api_enabled = False
        service._start_control_api_server()
        self.assertFalse(hasattr(service, "_control_api_server"))

        service.control_api_enabled = True
        service.control_api_host = "127.0.0.1"
        service.control_api_port = 8765
        service.control_api_auth_token = "token"

        fake_server = MagicMock(bound_host="127.0.0.1", bound_port=8765)
        with patch("venus_evcharger.service.control.LocalControlApiHttpServer", return_value=fake_server) as factory:
            service._start_control_api_server()

        factory.assert_called_once_with(
            service,
            host="127.0.0.1",
            port=8765,
            auth_token="token",
            read_token="",
            control_token="",
            admin_token="",
            update_token="",
            localhost_only=True,
            unix_socket_path="",
        )
        fake_server.start.assert_called_once_with()
        empty_service = _ControlService()
        empty_service._stop_control_api_server()
