# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.control import ControlCommand
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.service.auto import DbusAutoLogic
from venus_evcharger.service.control import ControlApi
from venus_evcharger.service.factory import ServiceControllerFactory
from venus_evcharger.service.runtime import RuntimeHelper
from venus_evcharger.service.state_publish import StatePublish
from venus_evcharger.service.update import UpdateCycle


class _ServiceRoleDefaults:
    _normalize_mode_func = staticmethod(lambda value: int(value))
    _mode_uses_auto_logic_func = staticmethod(lambda mode: bool(mode))
    _normalize_phase_func = staticmethod(lambda value: str(value))
    _month_window_func = staticmethod(lambda *args, **kwargs: None)
    _age_seconds_func = staticmethod(lambda _captured_at, _now: 0)
    _health_code_func = staticmethod(lambda _reason: 0)
    _phase_values_func = staticmethod(lambda *args, **kwargs: {})
    _read_version_func = staticmethod(lambda _path: "")
    _gobject_module = MagicMock()
    _script_path_value = ""
    _formatter_bundle = {}


class _RuntimeService(RuntimeHelper):
    _age_seconds_func = _ServiceRoleDefaults._age_seconds_func
    _health_code_func = _ServiceRoleDefaults._health_code_func

    def _ensure_runtime_support_controller(self):
        return None

    def _ensure_auto_input_supervisor(self):
        return None

    def _ensure_shelly_io_controller(self):
        return None


class _UpdateService(UpdateCycle):
    def _ensure_update_controller(self):
        return None


class _AutoService(DbusAutoLogic):
    _normalize_mode_func = _ServiceRoleDefaults._normalize_mode_func
    _mode_uses_auto_logic_func = _ServiceRoleDefaults._mode_uses_auto_logic_func
    _normalize_phase_func = _ServiceRoleDefaults._normalize_phase_func
    _month_window_func = _ServiceRoleDefaults._month_window_func
    _age_seconds_func = _ServiceRoleDefaults._age_seconds_func
    _health_code_func = _ServiceRoleDefaults._health_code_func
    _phase_values_func = _ServiceRoleDefaults._phase_values_func
    _read_version_func = _ServiceRoleDefaults._read_version_func

    def _ensure_dbus_input_controller(self):
        return None

    def _ensure_auto_controller(self):
        return None

    def _ensure_write_controller(self):
        return None

    def _ensure_bootstrap_controller(self):
        return None


class _StateService(StatePublish):
    _age_seconds_func = _ServiceRoleDefaults._age_seconds_func
    _health_code_func = _ServiceRoleDefaults._health_code_func

    def _ensure_state_controller(self):
        return None

    def _ensure_dbus_publisher(self):
        return None


class _ControlService(ControlApi):
    _age_seconds_func = _ServiceRoleDefaults._age_seconds_func
    _health_code_func = _ServiceRoleDefaults._health_code_func

    def _ensure_write_controller(self):
        return None

    def _ensure_dbus_publisher(self):
        return None

    def _state_summary(self):
        return "mode=1 enable=1"

    def _current_runtime_state(self):
        return {"mode": 1, "autostart": 1, "combined_battery_soc": 62.0}

    def _get_worker_snapshot(self):
        return {
            "battery_combined_soc": 62.0,
            "battery_source_count": 2,
            "battery_online_source_count": 2,
            "battery_combined_charge_power_w": 800.0,
            "battery_combined_discharge_power_w": 1200.0,
            "battery_combined_net_power_w": 400.0,
            "battery_combined_ac_power_w": 1800.0,
            "battery_combined_pv_input_power_w": 2600.0,
            "battery_combined_grid_interaction_w": -350.0,
            "battery_headroom_charge_w": 900.0,
            "battery_headroom_discharge_w": 1100.0,
            "expected_near_term_export_w": 425.0,
            "expected_near_term_import_w": 50.0,
            "battery_discharge_balance_mode": "capacity_reserve_weighted",
            "battery_discharge_balance_target_distribution_mode": "capacity_reserve_weighted",
            "battery_discharge_balance_error_w": 250.0,
            "battery_discharge_balance_max_abs_error_w": 250.0,
            "battery_discharge_balance_total_discharge_w": 1200.0,
            "battery_average_confidence": 0.75,
            "battery_battery_source_count": 1,
            "battery_hybrid_inverter_source_count": 1,
            "battery_inverter_source_count": 0,
            "battery_source_count": 2,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_discharge_balance_control_candidate_count": 1,
            "battery_discharge_balance_control_ready_count": 1,
            "battery_discharge_balance_supported_control_source_count": 0,
            "battery_discharge_balance_experimental_control_source_count": 1,
            "battery_sources": [
                {"source_id": "victron", "discharge_balance_control_support": "unsupported"},
                {"source_id": "hybrid", "discharge_balance_control_support": "experimental"},
            ],
            "battery_learning_profiles": {
                "victron": {
                    "sample_count": 2,
                    "charge_sample_count": 1,
                    "discharge_sample_count": 1,
                    "observed_max_charge_power_w": 700.0,
                    "observed_max_grid_import_w": 400.0,
                    "average_active_charge_power_w": 700.0,
                    "average_active_discharge_power_w": 900.0,
                    "average_active_power_delta_w": 100.0,
                    "typical_response_delay_seconds": 4.0,
                    "import_support_sample_count": 1,
                    "export_discharge_sample_count": 1,
                    "day_charge_sample_count": 1,
                    "night_discharge_sample_count": 1,
                    "smoothing_sample_count": 1,
                    "observed_min_discharge_soc": 35.0,
                    "observed_max_charge_soc": 85.0,
                    "direction_change_count": 1,
                },
                "hybrid": {
                    "sample_count": 3,
                    "charge_sample_count": 2,
                    "discharge_sample_count": 1,
                    "observed_max_discharge_power_w": 1400.0,
                    "observed_max_ac_power_w": 2000.0,
                    "observed_max_pv_input_power_w": 2600.0,
                    "observed_max_grid_export_w": 600.0,
                    "average_active_charge_power_w": 500.0,
                    "average_active_discharge_power_w": 1400.0,
                    "average_active_power_delta_w": 200.0,
                    "typical_response_delay_seconds": 8.0,
                    "export_charge_sample_count": 2,
                    "export_idle_sample_count": 1,
                    "day_charge_sample_count": 1,
                    "day_discharge_sample_count": 1,
                    "night_charge_sample_count": 1,
                    "smoothing_sample_count": 2,
                    "observed_min_discharge_soc": 45.0,
                    "observed_max_charge_soc": 90.0,
                    "direction_change_count": 2,
                },
            },
        }


class _FactoryService(_ServiceRoleDefaults, ServiceControllerFactory):
    pass


def _configured_control_service() -> _ControlService:
    service = _ControlService()
    service.config = configparser.ConfigParser()
    service.config.read_string(
        """
[DEFAULT]
Host=192.168.1.20

[Backends]
Mode=split
MeterType=template_meter
SwitchType=switch_group
ChargerType=goe_charger
"""
    )
    service.virtual_mode = 1
    service.virtual_enable = 1
    service.virtual_startstop = 0
    service.virtual_autostart = 1
    service.active_phase_selection = "P1"
    service.requested_phase_selection = "P1_P2"
    service._last_auto_state = "charging"
    service._last_auto_state_code = 3
    service._last_health_reason = ""
    service._software_update_state = "available"
    service._software_update_available = 1
    service._software_update_no_update_active = 0
    service._runtime_overrides_active = True
    service.runtime_overrides_path = "/run/runtime.ini"
    service.control_api_audit_path = "/run/control-audit.jsonl"
    service.control_api_audit_max_entries = 3
    service.control_api_idempotency_path = "/run/control-idempotency.json"
    service.control_api_idempotency_max_entries = 4
    service.control_api_rate_limit_max_requests = 15
    service.control_api_rate_limit_window_seconds = 7.5
    service.control_api_critical_cooldown_seconds = 3.0
    service.control_api_auth_token = "secret"
    service.control_api_read_token = ""
    service.control_api_control_token = ""
    service.control_api_admin_token = ""
    service.control_api_update_token = ""
    service.control_api_localhost_only = True
    service.control_api_unix_socket_path = ""
    service.auto_battery_discharge_balance_policy_enabled = True
    service.auto_battery_discharge_balance_warn_error_watts = 400.0
    service.auto_battery_discharge_balance_bias_start_error_watts = 500.0
    service.auto_battery_discharge_balance_bias_max_penalty_watts = 300.0
    service.auto_battery_discharge_balance_bias_mode = "export_only"
    service.auto_battery_discharge_balance_bias_reserve_margin_soc = 5.0
    service.auto_battery_discharge_balance_coordination_enabled = True
    service.auto_battery_discharge_balance_coordination_support_mode = "supported_only"
    service.auto_battery_discharge_balance_coordination_start_error_watts = 900.0
    service.auto_battery_discharge_balance_coordination_max_penalty_watts = 150.0
    service.auto_battery_discharge_balance_victron_bias_enabled = True
    service.auto_battery_discharge_balance_victron_bias_source_id = "victron"
    service.auto_battery_discharge_balance_victron_bias_service = "com.victronenergy.settings"
    service.auto_battery_discharge_balance_victron_bias_path = "/Settings/CGwacs/AcPowerSetPoint"
    service.auto_battery_discharge_balance_victron_bias_base_setpoint_watts = 50.0
    service.auto_battery_discharge_balance_victron_bias_deadband_watts = 100.0
    service.auto_battery_discharge_balance_victron_bias_activation_mode = "export_and_above_reserve_band"
    service.auto_battery_discharge_balance_victron_bias_support_mode = "supported_only"
    service.auto_battery_discharge_balance_victron_bias_kp = 0.2
    service.auto_battery_discharge_balance_victron_bias_ki = 0.02
    service.auto_battery_discharge_balance_victron_bias_kd = 0.0
    service.auto_battery_discharge_balance_victron_bias_integral_limit_watts = 250.0
    service.auto_battery_discharge_balance_victron_bias_max_abs_watts = 500.0
    service.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = 50.0
    service.auto_battery_discharge_balance_victron_bias_min_update_seconds = 2.0
    service.auto_battery_discharge_balance_victron_bias_auto_apply_enabled = True
    service.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence = 0.85
    service.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples = 3
    service.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score = 0.75
    service.auto_battery_discharge_balance_victron_bias_auto_apply_blend = 0.25
    service.auto_battery_discharge_balance_victron_bias_observation_window_seconds = 30.0
    service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled = True
    service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds = 120.0
    service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes = 3
    service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds = 180.0
    service.auto_battery_discharge_balance_victron_bias_rollback_enabled = True
    service.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score = 0.45
    service.auto_battery_discharge_balance_victron_bias_require_clean_phases = True
    service._last_auto_metrics = {
        "battery_discharge_balance_warning_active": 1,
        "battery_discharge_balance_warning_error_w": 250.0,
        "battery_discharge_balance_warn_threshold_w": 400.0,
        "battery_discharge_balance_bias_mode": "export_only",
        "battery_discharge_balance_bias_gate_active": 1,
        "battery_discharge_balance_bias_start_error_w": 500.0,
        "battery_discharge_balance_bias_penalty_w": 0.0,
        "battery_discharge_balance_coordination_policy_enabled": 1,
        "battery_discharge_balance_coordination_support_mode": "supported_only",
        "battery_discharge_balance_coordination_feasibility": "partial",
        "battery_discharge_balance_coordination_gate_active": 0,
        "battery_discharge_balance_coordination_start_error_w": 900.0,
        "battery_discharge_balance_coordination_penalty_w": 0.0,
        "battery_discharge_balance_coordination_advisory_active": 1,
        "battery_discharge_balance_coordination_advisory_reason": "only_some_sources_offer_a_write_path",
        "battery_discharge_balance_victron_bias_enabled": 1,
        "battery_discharge_balance_victron_bias_active": 1,
        "battery_discharge_balance_victron_bias_source_id": "victron",
        "battery_discharge_balance_victron_bias_topology_key": "victron-bias-learning/v1/source=victron/service=com.victronenergy.settings/path=/Settings/CGwacs/AcPowerSetPoint/energy=battery,hybrid",
        "battery_discharge_balance_victron_bias_activation_mode": "export_and_above_reserve_band",
        "battery_discharge_balance_victron_bias_activation_gate_active": 1,
        "battery_discharge_balance_victron_bias_support_mode": "supported_only",
        "battery_discharge_balance_victron_bias_learning_profile_key": "more_export:export:day:above_reserve_band",
        "battery_discharge_balance_victron_bias_learning_profile_action_direction": "more_export",
        "battery_discharge_balance_victron_bias_learning_profile_site_regime": "export",
        "battery_discharge_balance_victron_bias_learning_profile_direction": "export",
        "battery_discharge_balance_victron_bias_learning_profile_day_phase": "day",
        "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "above_reserve_band",
        "battery_discharge_balance_victron_bias_learning_profile_sample_count": 3,
        "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": 4.0,
        "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": 2.5,
        "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
        "battery_discharge_balance_victron_bias_learning_profile_settled_count": 3,
        "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
        "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": 60.0,
        "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": 550.0,
        "battery_discharge_balance_victron_bias_source_error_w": -320.0,
        "battery_discharge_balance_victron_bias_pid_output_w": -64.0,
        "battery_discharge_balance_victron_bias_setpoint_w": -14.0,
        "battery_discharge_balance_victron_bias_response_delay_seconds": 4.0,
        "battery_discharge_balance_victron_bias_estimated_gain": 2.5,
        "battery_discharge_balance_victron_bias_overshoot_active": 0,
        "battery_discharge_balance_victron_bias_overshoot_count": 1,
        "battery_discharge_balance_victron_bias_settling_active": 1,
        "battery_discharge_balance_victron_bias_settled_count": 3,
        "battery_discharge_balance_victron_bias_stability_score": 0.82,
        "battery_discharge_balance_victron_bias_recommended_kp": 0.23,
        "battery_discharge_balance_victron_bias_recommended_ki": 0.022,
        "battery_discharge_balance_victron_bias_recommended_kd": 0.01,
        "battery_discharge_balance_victron_bias_recommended_deadband_watts": 90.0,
        "battery_discharge_balance_victron_bias_recommended_max_abs_watts": 550.0,
        "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": 55.0,
        "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_and_above_reserve_band",
        "battery_discharge_balance_victron_bias_recommendation_confidence": 0.81,
        "battery_discharge_balance_victron_bias_recommendation_reason": "can_relax_conservatism",
        "battery_discharge_balance_victron_bias_recommendation_profile_key": "more_export:export:day:above_reserve_band",
        "battery_discharge_balance_victron_bias_recommendation_ini_snippet": (
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band"
        ),
        "battery_discharge_balance_victron_bias_recommendation_hint": (
            "Telemetry looks stable; you can cautiously relax the current "
            "Victron bias tuning (confidence 0.81)."
        ),
        "battery_discharge_balance_victron_bias_auto_apply_enabled": 1,
        "battery_discharge_balance_victron_bias_auto_apply_active": 1,
        "battery_discharge_balance_victron_bias_auto_apply_reason": "applied",
        "battery_discharge_balance_victron_bias_auto_apply_generation": 2,
        "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 1,
        "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": 1234.0,
        "battery_discharge_balance_victron_bias_auto_apply_last_param": (
            "auto_battery_discharge_balance_victron_bias_deadband_watts"
        ),
        "battery_discharge_balance_victron_bias_rollback_enabled": 1,
        "battery_discharge_balance_victron_bias_rollback_active": 0,
        "battery_discharge_balance_victron_bias_rollback_reason": "stable",
        "battery_discharge_balance_victron_bias_rollback_stable_profile_key": (
            "more_export:export:day:above_reserve_band"
        ),
        "battery_discharge_balance_victron_bias_telemetry_clean": 1,
        "battery_discharge_balance_victron_bias_telemetry_clean_reason": "clean",
        "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 1,
        "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
        "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "",
        "battery_discharge_balance_victron_bias_oscillation_lockout_until": None,
        "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 2,
        "battery_discharge_balance_victron_bias_reason": "applied",
    }
    service._victron_ess_balance_last_stable_tuning = {
        "kp": 0.2,
        "ki": 0.02,
        "kd": 0.0,
        "deadband_watts": 100.0,
        "max_abs_watts": 500.0,
        "ramp_rate_watts_per_second": 50.0,
        "activation_mode": "always",
    }
    service._victron_ess_balance_last_stable_at = 1190.0
    service._victron_ess_balance_last_stable_profile_key = "more_export:export:day:above_reserve_band"
    service._victron_ess_balance_learning_profiles = {
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
            "safe_ramp_rate_watts_per_second": 60.0,
            "preferred_bias_limit_watts": 550.0,
        }
    }
    service.companion_dbus_bridge_enabled = True
    service.companion_battery_service_enabled = True
    service.companion_pvinverter_service_enabled = True
    service.companion_grid_service_enabled = True
    service.companion_grid_authoritative_source = "huawei"
    service.companion_source_services_enabled = True
    service.companion_source_grid_services_enabled = True
    service.companion_battery_deviceinstance = 100
    service.companion_pvinverter_deviceinstance = 101
    service.companion_grid_deviceinstance = 102
    service.companion_source_battery_deviceinstance_base = 200
    service.companion_source_pvinverter_deviceinstance_base = 300
    service.companion_source_grid_deviceinstance_base = 400
    service.companion_battery_service_name = "com.victronenergy.battery.external_100"
    service.companion_pvinverter_service_name = "com.victronenergy.pvinverter.external_101"
    service.companion_grid_service_name = "com.victronenergy.grid.external_102"
    service.companion_source_battery_service_prefix = "com.victronenergy.battery.external"
    service.companion_source_pvinverter_service_prefix = "com.victronenergy.pvinverter.external"
    service.companion_source_grid_service_prefix = "com.victronenergy.grid.external"
    service.auto_use_combined_battery_soc = True
    service.auto_energy_sources = (
        EnergySourceDefinition(source_id="battery", profile_name="dbus-battery"),
        EnergySourceDefinition(source_id="hybrid", profile_name="huawei_ma_native_ap"),
    )
    service.product_name = "Venus EV Charger Service"
    service.service_name = "com.victronenergy.evcharger"
    service.connection_name = "HTTP"
    service.hardware_version = "HW-1"
    service.firmware_version = "FW-1"
    service.runtime_state_path = "/run/runtime.json"
    service.supported_phase_selections = ("P1", "P1_P2", "P1_P2_P3")
    service._dbus_publisher = MagicMock()
    service._dbus_publisher._diagnostic_counter_values.return_value = {"/Auto/State": "charging"}
    service._dbus_publisher._diagnostic_age_values.return_value = {"/Auto/LastShellyReadAge": 0.2}
    return service
