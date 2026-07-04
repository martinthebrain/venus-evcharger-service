# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import hashlib
import json

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.service_roles_cases_common import _ControlService, _configured_control_service
from venus_evcharger.control import ControlApiAuditTrail, ControlApiIdempotencyStore, ControlApiRateLimiter, ControlCommand
from venus_evcharger.control.events import ControlApiEventBus
from venus_evcharger.service.control_state_operational import (
    _last_health_reason,
    _software_update_state,
    _state_api_operational_core_state,
    _state_api_operational_payload,
    _state_api_operational_state,
)
from venus_evcharger.service.control_state_config import (
    _as_float,
    _as_int,
    _config_effective_energy_sources,
    _config_effective_energy_source_ids,
    _config_effective_energy_source_profile_details,
    _config_effective_energy_source_profiles,
    _config_effective_state_base,
    _state_api_config_effective_state,
)
from venus_evcharger.service.control_state_operational_support import (
    _mapping_values_with_default,
    _optional_metric_text,
    _relay_intent_value,
    _state_api_operational_auto_decision_state,
    _state_api_operational_balance_state,
    _state_api_operational_energy_state,
    _worker_snapshot,
)
from venus_evcharger.service.control_state_victron import (
    _state_api_victron_bias_recommendation_payload,
    _state_api_victron_bias_core_state,
    _state_api_victron_bias_state,
    _state_api_victron_active_learning_profile,
    _state_api_victron_bias_adaptive_tuning,
    _state_api_victron_bias_learning_state,
)
from venus_evcharger.service.control_state_meta import _callable_module_attr, _configured_phase_selections
from venus_evcharger.energy import energy_source_profile_details


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

    def test_control_api_server_factory_attr_contract_requires_callable(self):
        def factory() -> None:
            return None

        module = SimpleNamespace(factory=factory, not_factory="bad")

        self.assertIs(_callable_module_attr(module, "factory"), factory)
        with self.assertRaisesRegex(TypeError, "not_factory is not callable"):
            _callable_module_attr(module, "not_factory")

    def test_control_api_configured_phase_selection_contract(self):
        self.assertEqual(_configured_phase_selections(SimpleNamespace()), ("P1",))
        self.assertEqual(_configured_phase_selections(SimpleNamespace(supported_phase_selections=())), ("P1",))
        self.assertEqual(
            _configured_phase_selections(SimpleNamespace(supported_phase_selections=("P1", "P1_P2"))),
            ("P1", "P1_P2"),
        )

    def test_control_api_config_effective_energy_source_contracts(self):
        sources = (
            SimpleNamespace(source_id="battery", profile_name="dbus-battery"),
            SimpleNamespace(source_id="hybrid", profile_name="huawei_ma_native_ap"),
            SimpleNamespace(source_id="missing_profile"),
            SimpleNamespace(source_id="", profile_name="ignored"),
            SimpleNamespace(profile_name="missing-id"),
        )

        self.assertEqual(_config_effective_energy_source_ids(sources), ["battery", "hybrid", "missing_profile", "", ""])
        self.assertEqual(
            _config_effective_energy_source_profiles(sources),
            {"battery": "dbus-battery", "hybrid": "huawei_ma_native_ap", "missing_profile": ""},
        )
        details = _config_effective_energy_source_profile_details(sources)
        self.assertEqual(set(details), {"battery", "hybrid", "missing_profile"})
        self.assertEqual(details["battery"]["connector_type"], "dbus")
        self.assertEqual(details["battery"]["role"], "battery")
        self.assertEqual(details["hybrid"]["vendor_name"], "Huawei")
        self.assertEqual(details["hybrid"]["access_mode"], "native_ap")
        self.assertEqual(details["missing_profile"], {})

    def test_control_api_config_effective_energy_sources_rejects_invalid_containers(self):
        self.assertEqual(
            _config_effective_energy_sources(SimpleNamespace(auto_energy_sources="battery")),
            {
                "auto_use_combined_battery_soc": True,
                "auto_energy_source_ids": [],
                "auto_energy_source_profiles": {},
                "auto_energy_source_profile_details": {},
                "auto_energy_source_count": 0,
            },
        )
        self.assertEqual(
            _config_effective_energy_sources(SimpleNamespace(auto_energy_sources=42, auto_use_combined_battery_soc=0)),
            {
                "auto_use_combined_battery_soc": False,
                "auto_energy_source_ids": [],
                "auto_energy_source_profiles": {},
                "auto_energy_source_profile_details": {},
                "auto_energy_source_count": 0,
            },
        )
        self.assertEqual(
            _config_effective_energy_sources(SimpleNamespace(auto_energy_sources=None)),
            {
                "auto_use_combined_battery_soc": True,
                "auto_energy_source_ids": [],
                "auto_energy_source_profiles": {},
                "auto_energy_source_profile_details": {},
                "auto_energy_source_count": 0,
            },
        )

    def test_control_api_config_effective_state_base_backend_roles_are_explicit(self):
        owner = SimpleNamespace()
        with (
            patch("venus_evcharger.service.control_state_config.backend_mode_for_service", return_value="mode") as mode,
            patch("venus_evcharger.service.control_state_config.backend_type_for_service", return_value="type")
            as backend_type,
        ):
            state = _config_effective_state_base(owner)

        mode.assert_called_once_with(owner, "combined")
        self.assertEqual(
            [call.args for call in backend_type.call_args_list],
            [(owner, "meter", "na"), (owner, "switch", "na"), (owner, "charger", "na")],
        )
        self.assertEqual(state["backend_mode"], "mode")
        self.assertEqual(state["meter_backend"], "type")
        self.assertEqual(state["switch_backend"], "type")
        self.assertEqual(state["charger_backend"], "type")

    def test_control_api_config_effective_token_configuration_uses_stripped_values(self):
        owner = SimpleNamespace(
            control_api_read_token="   ",
            control_api_control_token=" token ",
            control_api_admin_token="\n",
            control_api_update_token=123,
        )

        state = _state_api_config_effective_state(owner)

        self.assertFalse(state["control_api_read_token_configured"])
        self.assertTrue(state["control_api_control_token_configured"])
        self.assertFalse(state["control_api_admin_token_configured"])
        self.assertTrue(state["control_api_update_token_configured"])

    def test_control_api_config_effective_numeric_converter_contracts(self):
        self.assertEqual(_as_int("12"), 12)
        self.assertEqual(_as_int(b"13"), 13)
        self.assertEqual(_as_float("14.5"), 14.5)
        self.assertEqual(_as_float(bytearray(b"15.5")), 15.5)

        with self.assertRaisesRegex(TypeError, "integer-compatible value, got list"):
            _as_int([])
        with self.assertRaisesRegex(TypeError, "float-compatible value, got list"):
            _as_float([])

    def test_control_api_config_effective_payload_envelope_contract(self):
        service = _configured_control_service()

        self.assertEqual(
            service._state_api_config_effective_payload(),
            {
                "ok": True,
                "api_version": "v1",
                "kind": "config-effective",
                "state": _state_api_config_effective_state(service),
            },
        )

    def test_control_api_config_effective_default_state_contract(self):
        self.assertEqual(
            _state_api_config_effective_state(SimpleNamespace()),
            {
                "deviceinstance": 0,
                "host": "",
                "phase": "L1",
                "service_name": "",
                "connection_name": "",
                "runtime_state_path": "",
                "runtime_overrides_path": "",
                "max_current": 0.0,
                "min_current": 0.0,
                "auto_daytime_only": False,
                "auto_scheduled_enabled_days": "",
                "auto_scheduled_latest_end_time": "",
                "auto_scheduled_night_current_amps": 0.0,
                "backend_mode": "combined",
                "meter_backend": "na",
                "switch_backend": "na",
                "charger_backend": "na",
                "control_api_enabled": False,
                "control_api_host": "127.0.0.1",
                "control_api_port": 0,
                "control_api_localhost_only": True,
                "control_api_unix_socket_path": "",
                "control_api_audit_path": "",
                "control_api_idempotency_path": "",
                "control_api_rate_limit_max_requests": 0,
                "control_api_rate_limit_window_seconds": 0.0,
                "control_api_critical_cooldown_seconds": 0.0,
                "control_api_read_token_configured": False,
                "control_api_control_token_configured": False,
                "control_api_admin_token_configured": False,
                "control_api_update_token_configured": False,
                "companion_dbus_bridge_enabled": False,
                "companion_battery_service_enabled": False,
                "companion_pvinverter_service_enabled": False,
                "companion_grid_service_enabled": False,
                "companion_grid_authoritative_source": "",
                "companion_grid_hold_seconds": 0.0,
                "companion_grid_smoothing_alpha": 1.0,
                "companion_grid_smoothing_max_jump_watts": 0.0,
                "companion_source_services_enabled": False,
                "companion_source_grid_services_enabled": False,
                "companion_source_grid_hold_seconds": 0.0,
                "companion_source_grid_smoothing_alpha": 1.0,
                "companion_source_grid_smoothing_max_jump_watts": 0.0,
                "companion_battery_deviceinstance": 0,
                "companion_pvinverter_deviceinstance": 0,
                "companion_grid_deviceinstance": 0,
                "companion_source_battery_deviceinstance_base": 0,
                "companion_source_pvinverter_deviceinstance_base": 0,
                "companion_source_grid_deviceinstance_base": 0,
                "companion_battery_service_name": "",
                "companion_pvinverter_service_name": "",
                "companion_grid_service_name": "",
                "companion_source_battery_service_prefix": "",
                "companion_source_pvinverter_service_prefix": "",
                "companion_source_grid_service_prefix": "",
                "auto_battery_discharge_balance_policy_enabled": False,
                "auto_battery_discharge_balance_warn_error_watts": 0.0,
                "auto_battery_discharge_balance_bias_start_error_watts": 0.0,
                "auto_battery_discharge_balance_bias_max_penalty_watts": 0.0,
                "auto_battery_discharge_balance_bias_mode": "always",
                "auto_battery_discharge_balance_bias_reserve_margin_soc": 0.0,
                "auto_battery_discharge_balance_coordination_enabled": False,
                "auto_battery_discharge_balance_coordination_support_mode": "supported_only",
                "auto_battery_discharge_balance_coordination_start_error_watts": 0.0,
                "auto_battery_discharge_balance_coordination_max_penalty_watts": 0.0,
                "auto_battery_discharge_balance_victron_bias_enabled": False,
                "auto_battery_discharge_balance_victron_bias_source_id": "",
                "auto_battery_discharge_balance_victron_bias_service": "",
                "auto_battery_discharge_balance_victron_bias_path": "",
                "auto_battery_discharge_balance_victron_bias_base_setpoint_watts": 0.0,
                "auto_battery_discharge_balance_victron_bias_deadband_watts": 0.0,
                "auto_battery_discharge_balance_victron_bias_activation_mode": "always",
                "auto_battery_discharge_balance_victron_bias_support_mode": "allow_experimental",
                "auto_battery_discharge_balance_victron_bias_kp": 0.0,
                "auto_battery_discharge_balance_victron_bias_ki": 0.0,
                "auto_battery_discharge_balance_victron_bias_kd": 0.0,
                "auto_battery_discharge_balance_victron_bias_integral_limit_watts": 0.0,
                "auto_battery_discharge_balance_victron_bias_max_abs_watts": 0.0,
                "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second": 0.0,
                "auto_battery_discharge_balance_victron_bias_min_update_seconds": 0.0,
                "auto_battery_discharge_balance_victron_bias_auto_apply_enabled": False,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence": 0.0,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples": 0,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score": 0.0,
                "auto_battery_discharge_balance_victron_bias_auto_apply_blend": 0.0,
                "auto_battery_discharge_balance_victron_bias_observation_window_seconds": 0.0,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled": False,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds": 0.0,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes": 0,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds": 0.0,
                "auto_battery_discharge_balance_victron_bias_rollback_enabled": False,
                "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score": 0.0,
                "auto_battery_discharge_balance_victron_bias_require_clean_phases": False,
                "auto_use_combined_battery_soc": True,
                "auto_energy_source_ids": [],
                "auto_energy_source_profiles": {},
                "auto_energy_source_profile_details": {},
                "auto_energy_source_count": 0,
            },
        )

    def test_control_api_config_effective_configured_state_contract(self):
        service = _configured_control_service()
        state = _state_api_config_effective_state(service)
        expected_profile_details = {
            "battery": dict(energy_source_profile_details("dbus-battery")),
            "hybrid": dict(energy_source_profile_details("huawei_ma_native_ap")),
        }

        self.assertEqual(
            state,
            {
                "deviceinstance": 0,
                "host": "",
                "phase": "L1",
                "service_name": "com.victronenergy.evcharger",
                "connection_name": "HTTP",
                "runtime_state_path": "/run/runtime.json",
                "runtime_overrides_path": "/run/runtime.ini",
                "max_current": 0.0,
                "min_current": 0.0,
                "auto_daytime_only": False,
                "auto_scheduled_enabled_days": "",
                "auto_scheduled_latest_end_time": "",
                "auto_scheduled_night_current_amps": 0.0,
                "backend_mode": "split",
                "meter_backend": "template_meter",
                "switch_backend": "switch_group",
                "charger_backend": "goe_charger",
                "control_api_enabled": False,
                "control_api_host": "127.0.0.1",
                "control_api_port": 0,
                "control_api_localhost_only": True,
                "control_api_unix_socket_path": "",
                "control_api_audit_path": "/run/control-audit.jsonl",
                "control_api_idempotency_path": "/run/control-idempotency.json",
                "control_api_rate_limit_max_requests": 15,
                "control_api_rate_limit_window_seconds": 7.5,
                "control_api_critical_cooldown_seconds": 3.0,
                "control_api_read_token_configured": False,
                "control_api_control_token_configured": False,
                "control_api_admin_token_configured": False,
                "control_api_update_token_configured": False,
                "companion_dbus_bridge_enabled": True,
                "companion_battery_service_enabled": True,
                "companion_pvinverter_service_enabled": True,
                "companion_grid_service_enabled": True,
                "companion_grid_authoritative_source": "huawei",
                "companion_grid_hold_seconds": 0.0,
                "companion_grid_smoothing_alpha": 1.0,
                "companion_grid_smoothing_max_jump_watts": 0.0,
                "companion_source_services_enabled": True,
                "companion_source_grid_services_enabled": True,
                "companion_source_grid_hold_seconds": 0.0,
                "companion_source_grid_smoothing_alpha": 1.0,
                "companion_source_grid_smoothing_max_jump_watts": 0.0,
                "companion_battery_deviceinstance": 100,
                "companion_pvinverter_deviceinstance": 101,
                "companion_grid_deviceinstance": 102,
                "companion_source_battery_deviceinstance_base": 200,
                "companion_source_pvinverter_deviceinstance_base": 300,
                "companion_source_grid_deviceinstance_base": 400,
                "companion_battery_service_name": "com.victronenergy.battery.external_100",
                "companion_pvinverter_service_name": "com.victronenergy.pvinverter.external_101",
                "companion_grid_service_name": "com.victronenergy.grid.external_102",
                "companion_source_battery_service_prefix": "com.victronenergy.battery.external",
                "companion_source_pvinverter_service_prefix": "com.victronenergy.pvinverter.external",
                "companion_source_grid_service_prefix": "com.victronenergy.grid.external",
                "auto_battery_discharge_balance_policy_enabled": True,
                "auto_battery_discharge_balance_warn_error_watts": 400.0,
                "auto_battery_discharge_balance_bias_start_error_watts": 500.0,
                "auto_battery_discharge_balance_bias_max_penalty_watts": 300.0,
                "auto_battery_discharge_balance_bias_mode": "export_only",
                "auto_battery_discharge_balance_bias_reserve_margin_soc": 5.0,
                "auto_battery_discharge_balance_coordination_enabled": True,
                "auto_battery_discharge_balance_coordination_support_mode": "supported_only",
                "auto_battery_discharge_balance_coordination_start_error_watts": 900.0,
                "auto_battery_discharge_balance_coordination_max_penalty_watts": 150.0,
                "auto_battery_discharge_balance_victron_bias_enabled": True,
                "auto_battery_discharge_balance_victron_bias_source_id": "victron",
                "auto_battery_discharge_balance_victron_bias_service": "com.victronenergy.settings",
                "auto_battery_discharge_balance_victron_bias_path": "/Settings/CGwacs/AcPowerSetPoint",
                "auto_battery_discharge_balance_victron_bias_base_setpoint_watts": 50.0,
                "auto_battery_discharge_balance_victron_bias_deadband_watts": 100.0,
                "auto_battery_discharge_balance_victron_bias_activation_mode": "export_and_above_reserve_band",
                "auto_battery_discharge_balance_victron_bias_support_mode": "supported_only",
                "auto_battery_discharge_balance_victron_bias_kp": 0.2,
                "auto_battery_discharge_balance_victron_bias_ki": 0.02,
                "auto_battery_discharge_balance_victron_bias_kd": 0.0,
                "auto_battery_discharge_balance_victron_bias_integral_limit_watts": 250.0,
                "auto_battery_discharge_balance_victron_bias_max_abs_watts": 500.0,
                "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second": 50.0,
                "auto_battery_discharge_balance_victron_bias_min_update_seconds": 2.0,
                "auto_battery_discharge_balance_victron_bias_auto_apply_enabled": True,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence": 0.85,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples": 3,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score": 0.75,
                "auto_battery_discharge_balance_victron_bias_auto_apply_blend": 0.25,
                "auto_battery_discharge_balance_victron_bias_observation_window_seconds": 30.0,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled": True,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds": 120.0,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes": 3,
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds": 180.0,
                "auto_battery_discharge_balance_victron_bias_rollback_enabled": True,
                "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score": 0.45,
                "auto_battery_discharge_balance_victron_bias_require_clean_phases": True,
                "auto_use_combined_battery_soc": True,
                "auto_energy_source_ids": ["battery", "hybrid"],
                "auto_energy_source_profiles": {"battery": "dbus-battery", "hybrid": "huawei_ma_native_ap"},
                "auto_energy_source_profile_details": expected_profile_details,
                "auto_energy_source_count": 2,
            },
        )

    def test_control_api_capabilities_resolves_backend_roles_with_explicit_contract(self):
        service = _ControlService()

        with (
            patch("venus_evcharger.service.control_state_meta.backend_mode_for_service", return_value="combined")
            as backend_mode,
            patch("venus_evcharger.service.control_state_meta.backend_type_for_service", return_value="stub_backend")
            as backend_type,
        ):
            capabilities = service._control_api_capabilities_payload()

        backend_mode.assert_called_once_with(service, "combined")
        self.assertEqual(
            [call.args for call in backend_type.call_args_list],
            [
                (service, "meter", "na"),
                (service, "switch", "na"),
                (service, "charger", "na"),
            ],
        )
        self.assertEqual(
            capabilities["topology"],
            {
                "backend_mode": "combined",
                "meter_backend": "stub_backend",
                "switch_backend": "stub_backend",
                "charger_backend": "stub_backend",
            },
        )

    def test_control_api_operational_support_helper_contracts(self):
        owner = SimpleNamespace(
            _last_health_reason="surplus-ok",
            auto_battery_discharge_balance_policy_enabled=True,
            _get_worker_snapshot=lambda: {1: "one", "battery_combined_soc": 55.5},
        )
        self.assertEqual(_worker_snapshot(owner), {"1": "one", "battery_combined_soc": 55.5})
        self.assertEqual(_worker_snapshot(SimpleNamespace()), {})
        self.assertEqual(_worker_snapshot(SimpleNamespace(_get_worker_snapshot=lambda: ["bad"])), {})
        self.assertEqual(_relay_intent_value(None), -1)
        self.assertEqual(_relay_intent_value(False), 0)
        self.assertEqual(_relay_intent_value(True), 1)
        self.assertEqual(_optional_metric_text(None), "")
        self.assertEqual(_optional_metric_text(" profile "), "profile")
        self.assertEqual(_mapping_values_with_default({"a": 1}, ("a", "b"), 9), {"a": 1, "b": 9})

        auto_decision = _state_api_operational_auto_decision_state(
            owner,
            {
                "relay_intent": True,
                "surplus": "123.5",
                "grid": "-20",
                "soc": 64,
                "start_threshold": 1400,
                "stop_threshold": 300,
                "profile": " day ",
                "threshold_mode": " hysteresis ",
            },
            "charging",
            3,
        )
        self.assertEqual(
            auto_decision,
            {
                "auto_decision": {
                    "reason": "surplus-ok",
                    "state": "charging",
                    "state_code": 3,
                    "relay_intent": 1,
                    "surplus_watts": 123.5,
                    "grid_watts": -20.0,
                    "soc_percent": 64.0,
                    "start_threshold_watts": 1400.0,
                    "stop_threshold_watts": 300.0,
                    "profile": "day",
                    "threshold_mode": "hysteresis",
                },
            },
        )
        self.assertEqual(
            _state_api_operational_auto_decision_state(SimpleNamespace(), {}, "idle", 0),
            {
                "auto_decision": {
                    "reason": "na",
                    "state": "idle",
                    "state_code": 0,
                    "relay_intent": -1,
                    "surplus_watts": None,
                    "grid_watts": None,
                    "soc_percent": None,
                    "start_threshold_watts": None,
                    "stop_threshold_watts": None,
                    "profile": "",
                    "threshold_mode": "",
                },
            },
        )
        self.assertEqual(
            _state_api_operational_auto_decision_state(SimpleNamespace(_last_health_reason=""), {}, "idle", 0)[
                "auto_decision"
            ]["reason"],
            "na",
        )

        worker_snapshot = {
            "battery_combined_soc": 10.0,
            "battery_source_count": 11,
            "battery_online_source_count": 12,
            "battery_combined_charge_power_w": 13.0,
            "battery_combined_discharge_power_w": 14.0,
            "battery_combined_net_power_w": 15.0,
            "battery_combined_ac_power_w": 16.0,
            "battery_combined_pv_input_power_w": 17.0,
            "battery_combined_grid_interaction_w": 18.0,
            "battery_headroom_charge_w": 19.0,
            "battery_headroom_discharge_w": 20.0,
            "expected_near_term_export_w": 21.0,
            "expected_near_term_import_w": 22.0,
            "battery_average_confidence": 0.23,
            "battery_battery_source_count": 24,
            "battery_hybrid_inverter_source_count": 25,
            "battery_inverter_source_count": 26,
        }
        learning_summary = {
            "profile_count": 30,
            "observed_max_charge_power_w": 31.0,
            "observed_max_discharge_power_w": 32.0,
            "observed_max_ac_power_w": 33.0,
            "observed_max_pv_input_power_w": 34.0,
            "observed_max_grid_import_w": 35.0,
            "observed_max_grid_export_w": 36.0,
            "average_active_charge_power_w": 37.0,
            "average_active_discharge_power_w": 38.0,
            "average_active_power_delta_w": 39.0,
            "power_smoothing_ratio": 0.4,
            "typical_response_delay_seconds": 41.0,
            "support_bias": 0.42,
            "day_support_bias": 0.43,
            "night_support_bias": 0.44,
            "import_support_bias": 0.45,
            "export_bias": 0.46,
            "battery_first_export_bias": 0.47,
            "reserve_band_floor_soc": 48.0,
            "reserve_band_ceiling_soc": 49.0,
            "reserve_band_width_soc": 50.0,
            "direction_change_count": 51,
        }
        energy_state = _state_api_operational_energy_state(worker_snapshot, learning_summary)
        self.assertEqual(
            energy_state,
            {
                "combined_battery_soc": 10.0,
                "combined_battery_source_count": 11,
                "combined_battery_online_source_count": 12,
                "combined_battery_charge_power_w": 13.0,
                "combined_battery_discharge_power_w": 14.0,
                "combined_battery_net_power_w": 15.0,
                "combined_battery_ac_power_w": 16.0,
                "combined_battery_pv_input_power_w": 17.0,
                "combined_battery_grid_interaction_w": 18.0,
                "combined_battery_headroom_charge_w": 19.0,
                "combined_battery_headroom_discharge_w": 20.0,
                "expected_near_term_export_w": 21.0,
                "expected_near_term_import_w": 22.0,
                "combined_battery_average_confidence": 0.23,
                "combined_battery_battery_source_count": 24,
                "combined_battery_hybrid_inverter_source_count": 25,
                "combined_battery_inverter_source_count": 26,
                "combined_battery_learning_profile_count": 30,
                "combined_battery_observed_max_charge_power_w": 31.0,
                "combined_battery_observed_max_discharge_power_w": 32.0,
                "combined_battery_observed_max_ac_power_w": 33.0,
                "combined_battery_observed_max_pv_input_power_w": 34.0,
                "combined_battery_observed_max_grid_import_w": 35.0,
                "combined_battery_observed_max_grid_export_w": 36.0,
                "combined_battery_average_active_charge_power_w": 37.0,
                "combined_battery_average_active_discharge_power_w": 38.0,
                "combined_battery_average_active_power_delta_w": 39.0,
                "combined_battery_power_smoothing_ratio": 0.4,
                "combined_battery_typical_response_delay_seconds": 41.0,
                "combined_battery_support_bias": 0.42,
                "combined_battery_day_support_bias": 0.43,
                "combined_battery_night_support_bias": 0.44,
                "combined_battery_import_support_bias": 0.45,
                "combined_battery_export_bias": 0.46,
                "combined_battery_battery_first_export_bias": 0.47,
                "combined_battery_reserve_band_floor_soc": 48.0,
                "combined_battery_reserve_band_ceiling_soc": 49.0,
                "combined_battery_reserve_band_width_soc": 50.0,
                "combined_battery_direction_change_count": 51,
                "combined_battery_learning_summary": learning_summary,
            },
        )
        empty_energy_state = _state_api_operational_energy_state({}, {})
        self.assertEqual(empty_energy_state["combined_battery_source_count"], 0)
        self.assertEqual(empty_energy_state["combined_battery_online_source_count"], 0)
        self.assertEqual(empty_energy_state["combined_battery_battery_source_count"], 0)
        self.assertEqual(empty_energy_state["combined_battery_hybrid_inverter_source_count"], 0)
        self.assertEqual(empty_energy_state["combined_battery_inverter_source_count"], 0)
        self.assertEqual(empty_energy_state["combined_battery_learning_profile_count"], 0)
        self.assertEqual(empty_energy_state["combined_battery_direction_change_count"], 0)

        balance_state = _state_api_operational_balance_state(
            owner,
            {
                "battery_discharge_balance_mode": "reserve",
                "battery_discharge_balance_target_distribution_mode": "weighted",
                "battery_discharge_balance_error_w": 1.0,
                "battery_discharge_balance_max_abs_error_w": 2.0,
                "battery_discharge_balance_total_discharge_w": 3.0,
                "battery_discharge_balance_eligible_source_count": 4,
                "battery_discharge_balance_active_source_count": 5,
                "battery_discharge_balance_control_candidate_count": 6,
                "battery_discharge_balance_control_ready_count": 7,
                "battery_discharge_balance_supported_control_source_count": 8,
                "battery_discharge_balance_experimental_control_source_count": 9,
            },
            {
                "battery_discharge_balance_warning_error_w": 10.0,
                "battery_discharge_balance_warn_threshold_w": 11.0,
                "battery_discharge_balance_bias_mode": "export",
                "battery_discharge_balance_bias_start_error_w": 12.0,
                "battery_discharge_balance_bias_penalty_w": 13.0,
                "battery_discharge_balance_coordination_support_mode": "supported",
                "battery_discharge_balance_coordination_feasibility": "full",
                "battery_discharge_balance_coordination_start_error_w": 14.0,
                "battery_discharge_balance_coordination_penalty_w": 15.0,
                "battery_discharge_balance_coordination_advisory_reason": "reason",
                "battery_discharge_balance_warning_active": 1,
                "battery_discharge_balance_bias_gate_active": 0,
                "battery_discharge_balance_coordination_policy_enabled": True,
                "battery_discharge_balance_coordination_gate_active": False,
                "battery_discharge_balance_coordination_advisory_active": True,
            },
        )
        self.assertEqual(
            balance_state,
            {
                "battery_discharge_balance_mode": "reserve",
                "battery_discharge_balance_target_distribution_mode": "weighted",
                "battery_discharge_balance_error_w": 1.0,
                "battery_discharge_balance_max_abs_error_w": 2.0,
                "battery_discharge_balance_total_discharge_w": 3.0,
                "battery_discharge_balance_eligible_source_count": 4,
                "battery_discharge_balance_active_source_count": 5,
                "battery_discharge_balance_control_candidate_count": 6,
                "battery_discharge_balance_control_ready_count": 7,
                "battery_discharge_balance_supported_control_source_count": 8,
                "battery_discharge_balance_experimental_control_source_count": 9,
                "battery_discharge_balance_warning_error_w": 10.0,
                "battery_discharge_balance_warn_threshold_w": 11.0,
                "battery_discharge_balance_bias_mode": "export",
                "battery_discharge_balance_bias_start_error_w": 12.0,
                "battery_discharge_balance_bias_penalty_w": 13.0,
                "battery_discharge_balance_coordination_support_mode": "supported",
                "battery_discharge_balance_coordination_feasibility": "full",
                "battery_discharge_balance_coordination_start_error_w": 14.0,
                "battery_discharge_balance_coordination_penalty_w": 15.0,
                "battery_discharge_balance_coordination_advisory_reason": "reason",
                "battery_discharge_balance_warning_active": True,
                "battery_discharge_balance_bias_gate_active": False,
                "battery_discharge_balance_coordination_policy_enabled": True,
                "battery_discharge_balance_coordination_gate_active": False,
                "battery_discharge_balance_coordination_advisory_active": True,
                "battery_discharge_balance_policy_enabled": True,
            },
        )
        empty_balance_state = _state_api_operational_balance_state(SimpleNamespace(), {}, {})
        self.assertEqual(empty_balance_state["battery_discharge_balance_eligible_source_count"], 0)
        self.assertEqual(empty_balance_state["battery_discharge_balance_active_source_count"], 0)
        self.assertEqual(empty_balance_state["battery_discharge_balance_control_candidate_count"], 0)
        self.assertEqual(empty_balance_state["battery_discharge_balance_control_ready_count"], 0)
        self.assertEqual(empty_balance_state["battery_discharge_balance_supported_control_source_count"], 0)
        self.assertEqual(empty_balance_state["battery_discharge_balance_experimental_control_source_count"], 0)
        self.assertIs(empty_balance_state["battery_discharge_balance_policy_enabled"], False)

    def test_control_api_operational_core_state_contracts(self):
        owner = SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            active_phase_selection="P1_P2",
            requested_phase_selection="P1_P2_P3",
            _runtime_overrides_active=True,
            runtime_overrides_path="/run/overrides.ini",
        )
        with (
            patch("venus_evcharger.service.control_state_operational.backend_mode_for_service", return_value="split")
            as backend_mode,
            patch("venus_evcharger.service.control_state_operational.backend_type_for_service") as backend_type,
        ):
            backend_type.side_effect = ["meter-backend", "switch-backend", "charger-backend"]
            state = _state_api_operational_core_state(owner, "charging", 3, "fault", 1, "available", 4, 1, 0)

        backend_mode.assert_called_once_with(owner, "combined")
        self.assertEqual(
            [call.args for call in backend_type.call_args_list],
            [
                (owner, "meter", "na"),
                (owner, "switch", "na"),
                (owner, "charger", "na"),
            ],
        )
        self.assertEqual(
            state,
            {
                "mode": 2,
                "enable": 1,
                "startstop": 1,
                "autostart": 1,
                "active_phase_selection": "P1_P2",
                "requested_phase_selection": "P1_P2_P3",
                "backend_mode": "split",
                "meter_backend": "meter-backend",
                "switch_backend": "switch-backend",
                "charger_backend": "charger-backend",
                "auto_state": "charging",
                "auto_state_code": 3,
                "fault_active": 1,
                "fault_reason": "fault",
                "software_update_state": "available",
                "software_update_state_code": 4,
                "software_update_available": 1,
                "software_update_no_update_active": 0,
                "runtime_overrides_active": True,
                "runtime_overrides_path": "/run/overrides.ini",
            },
        )
        with (
            patch("venus_evcharger.service.control_state_operational.backend_mode_for_service", return_value="combined"),
            patch("venus_evcharger.service.control_state_operational.backend_type_for_service", return_value="na"),
        ):
            default_state = _state_api_operational_core_state(
                SimpleNamespace(),
                "idle",
                0,
                "",
                0,
                "idle",
                0,
                0,
                0,
            )
        self.assertEqual(default_state["mode"], 0)
        self.assertEqual(default_state["enable"], 0)
        self.assertEqual(default_state["startstop"], 0)
        self.assertEqual(default_state["autostart"], 0)
        self.assertEqual(default_state["active_phase_selection"], "P1")
        self.assertEqual(default_state["requested_phase_selection"], "P1")
        self.assertIs(default_state["runtime_overrides_active"], False)
        self.assertEqual(default_state["runtime_overrides_path"], "")

    def test_control_api_operational_state_delegates_with_explicit_arguments(self):
        owner = SimpleNamespace()
        worker_snapshot = {"battery": "snapshot"}
        auto_metrics = {"relay_intent": True}
        learning_summary = {"profile_count": 1}

        with (
            patch("venus_evcharger.service.control_state_operational._state_api_operational_core_state") as core_state,
            patch(
                "venus_evcharger.service.control_state_operational._state_api_operational_auto_decision_state",
                return_value={"auto_decision": {"state_code": 7}},
            ) as auto_decision,
            patch(
                "venus_evcharger.service.control_state_operational._state_api_operational_energy_state",
                return_value={"energy": "ok"},
            ) as energy_state,
            patch(
                "venus_evcharger.service.control_state_operational._state_api_operational_balance_state",
                return_value={"balance": "ok"},
            ) as balance_state,
            patch(
                "venus_evcharger.service.control_state_operational._state_api_operational_victron_bias_state",
                return_value={"bias": "ok"},
            ) as bias_state,
        ):
            core_state.return_value = {"core": "ok"}
            state = _state_api_operational_state(
                owner,
                worker_snapshot,
                auto_metrics,
                "charging",
                7,
                "fault",
                1,
                ("available", 4, 1, 0),
                learning_summary,
            )

        core_state.assert_called_once_with(owner, "charging", 7, "fault", 1, "available", 4, 1, 0)
        auto_decision.assert_called_once_with(owner, auto_metrics, "charging", 7)
        energy_state.assert_called_once_with(worker_snapshot, learning_summary)
        balance_state.assert_called_once_with(owner, worker_snapshot, auto_metrics)
        bias_state.assert_called_once_with(auto_metrics)
        self.assertEqual(
            state,
            {
                "core": "ok",
                "auto_decision": {"state_code": 7},
                "energy": "ok",
                "balance": "ok",
                "bias": "ok",
            },
        )

    def test_control_api_operational_payload_orchestrates_defaults_and_metrics(self):
        owner = SimpleNamespace(_last_auto_metrics=["bad"])

        with (
            patch("venus_evcharger.service.control_state_operational._worker_snapshot", return_value={"snapshot": 1})
            as worker_snapshot,
            patch(
                "venus_evcharger.service.control_state_operational._worker_learning_summary",
                return_value={"learning": 2},
            ) as learning_summary,
            patch("venus_evcharger.service.control_state_operational._state_api_operational_state", return_value={})
            as state_builder,
        ):
            payload = _state_api_operational_payload(owner)

        worker_snapshot.assert_called_once_with(owner)
        learning_summary.assert_called_once_with({"snapshot": 1})
        state_builder.assert_called_once_with(owner, {"snapshot": 1}, {}, "idle", 0, "", 0, ("idle", 0, 0, 0), {"learning": 2})
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["kind"], "operational")
        self.assertEqual(payload["state"], {})

    def test_control_api_operational_payload_orchestrates_explicit_owner_state(self):
        owner = SimpleNamespace(
            _last_auto_state="charging",
            _last_auto_state_code=7,
            _last_health_reason="charger-fault",
            _software_update_state="available",
            _software_update_available=True,
            _software_update_no_update_active=True,
            _last_auto_metrics={"relay_intent": True},
        )

        with (
            patch("venus_evcharger.service.control_state_operational._worker_snapshot", return_value={"snapshot": 1}),
            patch(
                "venus_evcharger.service.control_state_operational._worker_learning_summary",
                return_value={"learning": 2},
            ),
            patch("venus_evcharger.service.control_state_operational._state_api_operational_state", return_value={})
            as state_builder,
        ):
            payload = _state_api_operational_payload(owner)

        state_builder.assert_called_once_with(
            owner,
            {"snapshot": 1},
            {"relay_intent": True},
            "charging",
            7,
            "charger-fault",
            1,
            ("available-blocked", 4, 1, 1),
            {"learning": 2},
        )
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["kind"], "operational")

    def test_control_api_operational_payload_fallback_helper_contracts(self):
        self.assertEqual(_last_health_reason(SimpleNamespace()), "")
        self.assertEqual(_last_health_reason(SimpleNamespace(_last_health_reason=None)), "")
        self.assertEqual(_last_health_reason(SimpleNamespace(_last_health_reason="charger-fault")), "charger-fault")
        self.assertEqual(_software_update_state(SimpleNamespace()), "idle")
        self.assertEqual(_software_update_state(SimpleNamespace(_software_update_state="available")), "available")

    def test_control_api_victron_bias_profile_and_learning_state_contracts(self):
        metrics = {
            "battery_discharge_balance_victron_bias_learning_profile_key": "profile-key",
            "battery_discharge_balance_victron_bias_learning_profile_action_direction": "more_export",
            "battery_discharge_balance_victron_bias_learning_profile_site_regime": "export",
            "battery_discharge_balance_victron_bias_learning_profile_direction": "charge",
            "battery_discharge_balance_victron_bias_learning_profile_day_phase": "day",
            "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "above",
            "battery_discharge_balance_victron_bias_learning_profile_ev_phase": "ev-off",
            "battery_discharge_balance_victron_bias_learning_profile_pv_phase": "pv-high",
            "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase": "limit-open",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 11,
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": 12.5,
            "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": 13.5,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 14,
            "battery_discharge_balance_victron_bias_learning_profile_settled_count": 15,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.16,
            "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score": 0.17,
            "battery_discharge_balance_victron_bias_learning_profile_response_variance_score": 0.18,
            "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score": 0.19,
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": 20.5,
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": 21.5,
            "battery_discharge_balance_victron_bias_topology_key": "topology",
            "battery_discharge_balance_victron_bias_source_id": "source",
        }

        self.assertEqual(
            _state_api_victron_active_learning_profile(metrics),
            {
                "key": "profile-key",
                "action_direction": "more_export",
                "site_regime": "export",
                "direction": "charge",
                "day_phase": "day",
                "reserve_phase": "above",
                "ev_phase": "ev-off",
                "pv_phase": "pv-high",
                "battery_limit_phase": "limit-open",
                "sample_count": 11,
                "response_delay_seconds": 12.5,
                "estimated_gain": 13.5,
                "overshoot_count": 14,
                "settled_count": 15,
                "stability_score": 0.16,
                "regime_consistency_score": 0.17,
                "response_variance_score": 0.18,
                "reproducibility_score": 0.19,
                "safe_ramp_rate_watts_per_second": 20.5,
                "preferred_bias_limit_watts": 21.5,
            },
        )
        profiles = {"profile-key": {"sample_count": 11}}
        self.assertEqual(
            _state_api_victron_bias_learning_state(metrics, profiles),
            {
                "schema_version": 2,
                "topology_key": "topology",
                "source_id": "source",
                "profiles": profiles,
            },
        )

    def test_control_api_victron_bias_adaptive_tuning_contract(self):
        owner = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp=1.1,
            auto_battery_discharge_balance_victron_bias_ki=1.2,
            auto_battery_discharge_balance_victron_bias_kd=1.3,
            auto_battery_discharge_balance_victron_bias_deadband_watts=1.4,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=1.5,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=1.6,
            _victron_ess_balance_last_stable_tuning={"kp": 0.9},
            _victron_ess_balance_last_stable_at=123.0,
            _victron_ess_balance_last_stable_profile_key="stable-profile",
            _victron_ess_balance_conservative_tuning={"kp": 0.5},
        )
        metrics = {
            "battery_discharge_balance_victron_bias_topology_key": "topology",
            "battery_discharge_balance_victron_bias_source_id": "source",
            "battery_discharge_balance_victron_bias_activation_mode": "activation",
            "battery_discharge_balance_victron_bias_auto_apply_generation": 2,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": 3.0,
            "battery_discharge_balance_victron_bias_auto_apply_last_param": "kp",
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": 4.0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "oscillation",
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": 5.0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "overshoot",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_until": 6.0,
            "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": "suspend",
            "battery_discharge_balance_victron_bias_safe_state_active": True,
            "battery_discharge_balance_victron_bias_safe_state_reason": "safe",
        }

        self.assertEqual(
            _state_api_victron_bias_adaptive_tuning(owner, metrics),
            {
                "schema_version": 2,
                "topology_key": "topology",
                "source_id": "source",
                "kp": 1.1,
                "ki": 1.2,
                "kd": 1.3,
                "deadband_watts": 1.4,
                "max_abs_watts": 1.5,
                "ramp_rate_watts_per_second": 1.6,
                "activation_mode": "activation",
                "auto_apply_generation": 2,
                "auto_apply_observe_until": 3.0,
                "auto_apply_last_applied_param": "kp",
                "oscillation_lockout_until": 4.0,
                "oscillation_lockout_reason": "oscillation",
                "overshoot_cooldown_until": 5.0,
                "overshoot_cooldown_reason": "overshoot",
                "last_stable_tuning": {"kp": 0.9},
                "last_stable_at": 123.0,
                "last_stable_profile_key": "stable-profile",
                "conservative_tuning": {"kp": 0.5},
                "auto_apply_suspend_until": 6.0,
                "auto_apply_suspend_reason": "suspend",
                "safe_state_active": True,
                "safe_state_reason": "safe",
            },
        )

    def test_control_api_victron_bias_adaptive_tuning_defaults_contract(self):
        self.assertEqual(
            _state_api_victron_bias_adaptive_tuning(SimpleNamespace(), {}),
            {
                "schema_version": 2,
                "topology_key": None,
                "source_id": None,
                "kp": 0.0,
                "ki": 0.0,
                "kd": 0.0,
                "deadband_watts": 0.0,
                "max_abs_watts": 0.0,
                "ramp_rate_watts_per_second": 0.0,
                "activation_mode": None,
                "auto_apply_generation": None,
                "auto_apply_observe_until": None,
                "auto_apply_last_applied_param": None,
                "oscillation_lockout_until": None,
                "oscillation_lockout_reason": None,
                "overshoot_cooldown_until": None,
                "overshoot_cooldown_reason": None,
                "last_stable_tuning": {},
                "last_stable_at": None,
                "last_stable_profile_key": "",
                "conservative_tuning": {},
                "auto_apply_suspend_until": None,
                "auto_apply_suspend_reason": None,
                "safe_state_active": None,
                "safe_state_reason": None,
            },
        )

    def test_control_api_victron_bias_core_state_mapping_contract(self):
        owner = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp=1.1,
            auto_battery_discharge_balance_victron_bias_ki=1.2,
            auto_battery_discharge_balance_victron_bias_kd=1.3,
            auto_battery_discharge_balance_victron_bias_deadband_watts=1.4,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=1.5,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=1.6,
        )
        value_expectations = {
            "source_id": ("battery_discharge_balance_victron_bias_source_id", "value-1"),
            "topology_key": ("battery_discharge_balance_victron_bias_topology_key", "value-2"),
            "support_mode": ("battery_discharge_balance_victron_bias_support_mode", "value-3"),
            "activation_mode": ("battery_discharge_balance_victron_bias_activation_mode", "value-4"),
            "active_learning_profile_key": ("battery_discharge_balance_victron_bias_learning_profile_key", "profile-key"),
            "recommended_kp": ("battery_discharge_balance_victron_bias_recommended_kp", "value-6"),
            "recommended_ki": ("battery_discharge_balance_victron_bias_recommended_ki", "value-7"),
            "recommended_kd": ("battery_discharge_balance_victron_bias_recommended_kd", "value-8"),
            "recommended_deadband_watts": (
                "battery_discharge_balance_victron_bias_recommended_deadband_watts",
                "value-9",
            ),
            "recommended_max_abs_watts": (
                "battery_discharge_balance_victron_bias_recommended_max_abs_watts",
                "value-10",
            ),
            "recommended_ramp_rate_watts_per_second": (
                "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second",
                "value-11",
            ),
            "recommended_activation_mode": (
                "battery_discharge_balance_victron_bias_recommended_activation_mode",
                "value-12",
            ),
            "recommendation_confidence": (
                "battery_discharge_balance_victron_bias_recommendation_confidence",
                "value-13",
            ),
            "recommendation_regime_consistency_score": (
                "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score",
                "value-14",
            ),
            "recommendation_response_variance_score": (
                "battery_discharge_balance_victron_bias_recommendation_response_variance_score",
                "value-15",
            ),
            "recommendation_reproducibility_score": (
                "battery_discharge_balance_victron_bias_recommendation_reproducibility_score",
                "value-16",
            ),
            "recommendation_reason": ("battery_discharge_balance_victron_bias_recommendation_reason", "value-17"),
            "recommendation_profile_key": (
                "battery_discharge_balance_victron_bias_recommendation_profile_key",
                "value-18",
            ),
            "recommendation_hint": ("battery_discharge_balance_victron_bias_recommendation_hint", "value-19"),
            "recommendation_ini_snippet": (
                "battery_discharge_balance_victron_bias_recommendation_ini_snippet",
                "value-20",
            ),
            "telemetry_clean_reason": ("battery_discharge_balance_victron_bias_telemetry_clean_reason", "value-21"),
            "response_delay_seconds": ("battery_discharge_balance_victron_bias_response_delay_seconds", 22.0),
            "estimated_gain": ("battery_discharge_balance_victron_bias_estimated_gain", 23.0),
            "overshoot_count": ("battery_discharge_balance_victron_bias_overshoot_count", 24),
            "overshoot_cooldown_reason": (
                "battery_discharge_balance_victron_bias_overshoot_cooldown_reason",
                "value-25",
            ),
            "overshoot_cooldown_until": ("battery_discharge_balance_victron_bias_overshoot_cooldown_until", 26.0),
            "settled_count": ("battery_discharge_balance_victron_bias_settled_count", 27),
            "stability_score": ("battery_discharge_balance_victron_bias_stability_score", 28.0),
            "oscillation_lockout_reason": (
                "battery_discharge_balance_victron_bias_oscillation_lockout_reason",
                "value-29",
            ),
            "oscillation_lockout_until": (
                "battery_discharge_balance_victron_bias_oscillation_lockout_until",
                30.0,
            ),
            "oscillation_direction_change_count": (
                "battery_discharge_balance_victron_bias_oscillation_direction_change_count",
                31,
            ),
            "auto_apply_reason": ("battery_discharge_balance_victron_bias_auto_apply_reason", "value-32"),
            "auto_apply_generation": ("battery_discharge_balance_victron_bias_auto_apply_generation", 33),
            "auto_apply_observation_window_until": (
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_until",
                34.0,
            ),
            "auto_apply_last_param": ("battery_discharge_balance_victron_bias_auto_apply_last_param", "value-35"),
            "auto_apply_suspend_reason": (
                "battery_discharge_balance_victron_bias_auto_apply_suspend_reason",
                "value-36",
            ),
            "auto_apply_suspend_until": ("battery_discharge_balance_victron_bias_auto_apply_suspend_until", 37.0),
            "rollback_reason": ("battery_discharge_balance_victron_bias_rollback_reason", "value-38"),
            "rollback_stable_profile_key": (
                "battery_discharge_balance_victron_bias_rollback_stable_profile_key",
                "value-39",
            ),
            "safe_state_reason": ("battery_discharge_balance_victron_bias_safe_state_reason", "value-40"),
            "controller_reason": ("battery_discharge_balance_victron_bias_reason", "value-41"),
        }
        bool_expectations = {
            "enabled": ("battery_discharge_balance_victron_bias_enabled", True),
            "active": ("battery_discharge_balance_victron_bias_active", True),
            "activation_gate_active": ("battery_discharge_balance_victron_bias_activation_gate_active", True),
            "telemetry_clean": ("battery_discharge_balance_victron_bias_telemetry_clean", True),
            "overshoot_active": ("battery_discharge_balance_victron_bias_overshoot_active", True),
            "overshoot_cooldown_active": ("battery_discharge_balance_victron_bias_overshoot_cooldown_active", True),
            "settling_active": ("battery_discharge_balance_victron_bias_settling_active", True),
            "oscillation_lockout_enabled": (
                "battery_discharge_balance_victron_bias_oscillation_lockout_enabled",
                True,
            ),
            "oscillation_lockout_active": ("battery_discharge_balance_victron_bias_oscillation_lockout_active", True),
            "auto_apply_enabled": ("battery_discharge_balance_victron_bias_auto_apply_enabled", True),
            "auto_apply_active": ("battery_discharge_balance_victron_bias_auto_apply_active", True),
            "auto_apply_observation_window_active": (
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_active",
                True,
            ),
            "auto_apply_suspend_active": ("battery_discharge_balance_victron_bias_auto_apply_suspend_active", True),
            "rollback_enabled": ("battery_discharge_balance_victron_bias_rollback_enabled", True),
            "rollback_active": ("battery_discharge_balance_victron_bias_rollback_active", True),
            "safe_state_active": ("battery_discharge_balance_victron_bias_safe_state_active", True),
        }
        profile_metrics = {
            "battery_discharge_balance_victron_bias_learning_profile_action_direction": "more_export",
            "battery_discharge_balance_victron_bias_learning_profile_site_regime": "export",
            "battery_discharge_balance_victron_bias_learning_profile_direction": "charge",
            "battery_discharge_balance_victron_bias_learning_profile_day_phase": "day",
            "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "above",
            "battery_discharge_balance_victron_bias_learning_profile_ev_phase": "ev-off",
            "battery_discharge_balance_victron_bias_learning_profile_pv_phase": "pv-high",
            "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase": "limit-open",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 11,
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": 12.5,
            "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": 13.5,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 14,
            "battery_discharge_balance_victron_bias_learning_profile_settled_count": 15,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.16,
            "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score": 0.17,
            "battery_discharge_balance_victron_bias_learning_profile_response_variance_score": 0.18,
            "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score": 0.19,
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": 20.5,
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": 21.5,
        }
        metrics = {source: expected for source, expected in value_expectations.values()}
        metrics.update({source: value for source, value in bool_expectations.values()})
        metrics.update(profile_metrics)

        state = _state_api_victron_bias_core_state(owner, metrics)

        self.assertEqual(state["current_kp"], 1.1)
        self.assertEqual(state["current_ki"], 1.2)
        self.assertEqual(state["current_kd"], 1.3)
        self.assertEqual(state["current_deadband_watts"], 1.4)
        self.assertEqual(state["current_max_abs_watts"], 1.5)
        self.assertEqual(state["current_ramp_rate_watts_per_second"], 1.6)
        for output_key, (_source_key, expected_value) in value_expectations.items():
            self.assertEqual(state[output_key], expected_value)
        for output_key, (_source_key, expected_value) in bool_expectations.items():
            self.assertIs(state[output_key], expected_value)
        self.assertEqual(state["active_learning_profile"]["key"], "profile-key")
        self.assertEqual(state["active_learning_profile"]["preferred_bias_limit_watts"], 21.5)

        default_state = _state_api_victron_bias_core_state(SimpleNamespace(), {})
        self.assertEqual(default_state["current_kp"], 0.0)
        self.assertEqual(default_state["current_ki"], 0.0)
        self.assertEqual(default_state["current_kd"], 0.0)
        self.assertEqual(default_state["current_deadband_watts"], 0.0)
        self.assertEqual(default_state["current_max_abs_watts"], 0.0)
        self.assertEqual(default_state["current_ramp_rate_watts_per_second"], 0.0)

    def test_control_api_victron_bias_payload_and_state_orchestration_contracts(self):
        owner = SimpleNamespace(
            _last_auto_metrics=["bad"],
            _victron_ess_balance_learning_profiles=["bad"],
        )
        with patch("venus_evcharger.service.control_state_victron._state_api_victron_bias_state", return_value={}) as state:
            payload = _state_api_victron_bias_recommendation_payload(owner)

        state.assert_called_once_with(owner, {}, {})
        self.assertEqual(payload, {"ok": True, "api_version": "v1", "kind": "victron-bias-recommendation", "state": {}})

        metrics = {"metric": 1}
        profiles = {"profile": {"sample_count": 2}}
        with (
            patch(
                "venus_evcharger.service.control_state_victron._state_api_victron_bias_core_state",
                return_value={"core": "ok"},
            ) as core,
            patch(
                "venus_evcharger.service.control_state_victron._state_api_victron_bias_learning_state",
                return_value={"learning": "ok"},
            ) as learning,
            patch(
                "venus_evcharger.service.control_state_victron._state_api_victron_bias_adaptive_tuning",
                return_value={"adaptive": "ok"},
            ) as adaptive,
        ):
            combined = _state_api_victron_bias_state(owner, metrics, profiles)

        core.assert_called_once_with(owner, metrics)
        learning.assert_called_once_with(metrics, profiles)
        adaptive.assert_called_once_with(owner, metrics)
        self.assertEqual(
            combined,
            {
                "core": "ok",
                "learning_state": {"learning": "ok"},
                "adaptive_tuning": {"adaptive": "ok"},
                "learning_profiles": profiles,
            },
        )

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
        self.assertEqual(automation["state"]["events_endpoint"], "/v1/events")
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
        self.assertEqual(health["ok"], True)
        self.assertEqual(health["api_version"], "v1")
        self.assertEqual(health["state"]["command_audit_entries"], 0)
        self.assertEqual(health["state"]["command_audit_path"], "/run/control-audit.jsonl")
        self.assertEqual(health["state"]["idempotency_entries"], 0)
        self.assertEqual(health["state"]["idempotency_path"], "/run/control-idempotency.json")
        self.assertIs(health["state"]["update_stale"], False)
        self.assertEqual(
            healthz,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "healthz",
                "state": {
                    "alive": True,
                    "control_api_enabled": False,
                    "control_api_running": False,
                },
            },
        )
        self.assertEqual(
            version,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "version",
                "state": {
                    "service_version": "FW-1",
                    "api_version": "v1",
                    "product_name": "Venus EV Charger Service",
                    "service_name": "com.victronenergy.evcharger",
                },
            },
        )
        self.assertEqual(
            build,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "build",
                "state": {
                    "product_name": "Venus EV Charger Service",
                    "hardware_version": "HW-1",
                    "firmware_version": "FW-1",
                    "connection_name": "HTTP",
                    "runtime_state_path": "/run/runtime.json",
                },
            },
        )
        self.assertEqual(contracts["ok"], True)
        self.assertEqual(contracts["api_version"], "v1")
        self.assertEqual(contracts["kind"], "contracts")
        self.assertEqual(
            set(contracts["state"]),
            {
                "active_api_version",
                "openapi_endpoint",
                "capabilities_endpoint",
                "versioning_document",
                "control_document",
                "state_document",
                "stable_endpoints",
                "experimental_endpoints",
            },
        )
        self.assertEqual(contracts["state"]["active_api_version"], "v1")
        self.assertEqual(contracts["state"]["openapi_endpoint"], "/v1/openapi.json")
        self.assertEqual(contracts["state"]["capabilities_endpoint"], "/v1/capabilities")
        self.assertEqual(contracts["state"]["versioning_document"], "API_VERSIONING.md")
        self.assertEqual(contracts["state"]["control_document"], "CONTROL_API.md")
        self.assertEqual(contracts["state"]["state_document"], "STATE_API.md")
        self.assertIn("/v1/state/automation", contracts["state"]["stable_endpoints"])
        self.assertEqual(contracts["state"]["experimental_endpoints"], ["/v1/events"])
        self.assertIn("summary", snapshot)
        self.assertIn("health", snapshot)
        self.assertTrue(service._control_api_state_token())

    def test_control_api_meta_payload_defaults_are_empty_and_single_phase(self):
        service = _ControlService()

        version = service._state_api_version_payload()
        build = service._state_api_build_payload()
        health = service._state_api_health_payload()
        capabilities = service._control_api_capabilities_payload()

        self.assertEqual(
            version,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "version",
                "state": {
                    "service_version": "",
                    "api_version": "v1",
                    "product_name": "",
                    "service_name": "",
                },
            },
        )
        self.assertEqual(
            build,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "build",
                "state": {
                    "product_name": "",
                    "hardware_version": "",
                    "firmware_version": "",
                    "connection_name": "",
                    "runtime_state_path": "",
                },
            },
        )
        self.assertEqual(capabilities["supported_phase_selections"], ["P1"])
        self.assertFalse(capabilities["features"]["multi_phase_selection"])
        self.assertTrue(capabilities["features"]["phase_selection_write"])
        self.assertTrue(capabilities["localhost_only"])
        self.assertEqual(
            capabilities["topology"],
            {
                "backend_mode": "combined",
                "meter_backend": "na",
                "switch_backend": "na",
                "charger_backend": "na",
            },
        )
        self.assertEqual(health["state"]["health_reason"], "init")
        self.assertEqual(health["state"]["health_code"], 0)
        self.assertIs(health["state"]["fault_active"], False)
        self.assertEqual(health["state"]["fault_reason"], "")
        self.assertIs(health["state"]["runtime_overrides_active"], False)
        self.assertIs(health["state"]["control_api_enabled"], False)
        self.assertIs(health["state"]["control_api_running"], False)
        self.assertIs(health["state"]["control_api_localhost_only"], True)
        self.assertIs(health["state"]["update_stale"], False)

    def test_control_api_meta_payloads_report_active_control_surface(self):
        service = _ControlService()
        service.control_api_enabled = True
        service._control_api_server = object()
        service.control_api_read_token = "read"
        service.control_api_control_token = ""
        service.control_api_auth_token = ""
        service.control_api_localhost_only = False
        service.control_api_listen_host = "0.0.0.0"
        service.control_api_listen_port = 9090
        service.control_api_bound_unix_socket_path = "/run/control.sock"
        service._runtime_overrides_active = True
        service._software_update_current_version = " 2.0.0 "
        service.product_name = " Product "
        service.service_name = " service.name "

        healthz = service._state_api_healthz_payload()
        health = service._state_api_health_payload()
        version = service._state_api_version_payload()
        capabilities = service._control_api_capabilities_payload()

        self.assertEqual(
            healthz,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "healthz",
                "state": {
                    "alive": True,
                    "control_api_enabled": True,
                    "control_api_running": True,
                },
            },
        )
        self.assertEqual(version["state"]["service_version"], "2.0.0")
        self.assertEqual(version["state"]["product_name"], "Product")
        self.assertEqual(version["state"]["service_name"], "service.name")
        self.assertIs(health["state"]["runtime_overrides_active"], True)
        self.assertIs(health["state"]["control_api_enabled"], True)
        self.assertIs(health["state"]["control_api_running"], True)
        self.assertEqual(health["state"]["listen_host"], "0.0.0.0")
        self.assertEqual(health["state"]["listen_port"], 9090)
        self.assertEqual(health["state"]["unix_socket_path"], "/run/control.sock")
        self.assertIs(health["state"]["control_api_localhost_only"], False)
        self.assertTrue(capabilities["auth_required"])
        self.assertTrue(capabilities["read_auth_required"])
        self.assertFalse(capabilities["control_auth_required"])
        self.assertFalse(capabilities["localhost_only"])
        self.assertEqual(capabilities["unix_socket_path"], "/run/control.sock")

        service.control_api_read_token = ""
        service.control_api_control_token = "control"
        capabilities = service._control_api_capabilities_payload()
        self.assertTrue(capabilities["auth_required"])
        self.assertTrue(capabilities["read_auth_required"])
        self.assertTrue(capabilities["control_auth_required"])

        service.supported_phase_selections = ("P1", "P1_P2")
        capabilities = service._control_api_capabilities_payload()
        self.assertEqual(capabilities["supported_phase_selections"], ["P1", "P1_P2"])
        self.assertTrue(capabilities["features"]["multi_phase_selection"])

    def test_control_api_meta_payload_contracts_are_complete_enough_for_clients(self):
        service = _configured_control_service()

        automation = service._state_api_automation_payload()
        health = service._state_api_health_payload()
        capabilities = service._control_api_capabilities_payload()
        snapshot = service._state_api_event_snapshot_payload()
        expected_state_endpoints = [
            "/v1/state/automation",
            "/v1/state/build",
            "/v1/state/config-effective",
            "/v1/state/contracts",
            "/v1/state/dbus-diagnostics",
            "/v1/state/health",
            "/v1/state/healthz",
            "/v1/state/operational",
            "/v1/state/runtime",
            "/v1/state/summary",
            "/v1/state/topology",
            "/v1/state/update",
            "/v1/state/version",
            "/v1/state/victron-bias-recommendation",
        ]
        expected_stable_endpoints = [
            "/v1/capabilities",
            "/v1/control/command",
            "/v1/control/health",
            "/v1/openapi.json",
            *expected_state_endpoints,
        ]
        expected_endpoints = [
            "/v1/capabilities",
            "/v1/control/command",
            "/v1/control/health",
            "/v1/events",
            "/v1/openapi.json",
            *expected_state_endpoints,
        ]

        self.assertEqual(
            set(automation["state"]),
            {
                "state_token",
                "command_endpoint",
                "events_endpoint",
                "state_endpoints",
                "safe_write",
                "writable",
                "operational",
                "auto_decision",
                "health",
                "topology",
                "diagnostics",
            },
        )
        self.assertEqual(
            automation["state"]["safe_write"],
            {
                "if_match_header": "If-Match",
                "state_token_header": "X-State-Token",
                "idempotency_key_header": "Idempotency-Key",
                "command_id_header": "X-Command-Id",
                "recommended_flow": "read /v1/state/automation, then POST command with If-Match and Idempotency-Key",
            },
        )
        self.assertEqual(
            set(automation["state"]["writable"]),
            {"command_names", "scope_requirements"},
        )
        self.assertIn("set_mode", automation["state"]["writable"]["command_names"])
        self.assertEqual(automation["state"]["writable"]["scope_requirements"]["set_mode"], "control_basic")
        self.assertEqual(automation["state"]["topology"]["charger_backend"], "goe_charger")
        self.assertEqual(automation["state"]["diagnostics"], {"/Auto/LastShellyReadAge": 0.2})
        self.assertEqual(automation["state"]["events_endpoint"], "/v1/events")
        self.assertEqual(automation["state"]["auto_decision"], automation["state"]["operational"]["auto_decision"])
        self.assertEqual(automation["state"]["auto_decision"]["relay_intent"], -1)
        self.assertEqual(automation["ok"], True)
        self.assertEqual(automation["api_version"], "v1")
        self.assertEqual(automation["kind"], "automation")
        self.assertEqual(
            set(health["state"]),
            {
                "health_reason",
                "health_code",
                "fault_active",
                "fault_reason",
                "runtime_overrides_active",
                "control_api_enabled",
                "control_api_running",
                "control_api_transport",
                "listen_host",
                "listen_port",
                "unix_socket_path",
                "control_api_localhost_only",
                "command_audit_entries",
                "command_audit_path",
                "idempotency_entries",
                "idempotency_path",
                "update_stale",
                "last_successful_update_at",
                "last_recovery_attempt_at",
            },
        )
        self.assertEqual(health["state"]["control_api_transport"], "http")
        self.assertFalse(health["state"]["fault_active"])
        self.assertEqual(health["state"]["fault_reason"], "")
        self.assertIs(health["state"]["runtime_overrides_active"], True)
        self.assertIs(health["state"]["control_api_enabled"], False)
        self.assertIs(health["state"]["control_api_running"], False)
        self.assertIs(health["state"]["control_api_localhost_only"], True)
        self.assertEqual(health["state"]["listen_host"], "")
        self.assertEqual(health["state"]["listen_port"], 0)
        self.assertEqual(health["state"]["unix_socket_path"], "")
        self.assertIsNone(health["state"]["last_successful_update_at"])
        self.assertIsNone(health["state"]["last_recovery_attempt_at"])
        self.assertEqual(
            capabilities,
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "auth_required": True,
                "read_auth_required": True,
                "control_auth_required": True,
                "localhost_only": True,
                "unix_socket_path": "",
                "auth_header": "Authorization: Bearer <token>",
                "auth_scopes": ["control_admin", "control_basic", "read", "update_admin"],
                "command_names": [
                    "legacy_unknown_write",
                    "reset_contactor_lockout",
                    "reset_phase_lockout",
                    "set_auto_runtime_setting",
                    "set_auto_start",
                    "set_current_setting",
                    "set_enable",
                    "set_mode",
                    "set_phase_selection",
                    "set_start_stop",
                    "trigger_software_update",
                ],
                "command_scope_requirements": {
                    "legacy_unknown_write": "control_admin",
                    "reset_contactor_lockout": "control_admin",
                    "reset_phase_lockout": "control_admin",
                    "set_auto_runtime_setting": "control_admin",
                    "set_auto_start": "control_basic",
                    "set_current_setting": "control_basic",
                    "set_enable": "control_basic",
                    "set_mode": "control_basic",
                    "set_phase_selection": "control_basic",
                    "set_start_stop": "control_basic",
                    "trigger_software_update": "update_admin",
                },
                "command_sources": ["dbus", "http", "internal", "mqtt"],
                "state_endpoints": expected_state_endpoints,
                "endpoints": expected_endpoints,
                "available_modes": [0, 1, 2],
                "supported_phase_selections": ["P1", "P1_P2", "P1_P2_P3"],
                "features": {
                    "command_audit_trail": True,
                    "dbus_diagnostics_state": True,
                    "event_stream": True,
                    "event_kind_filters": True,
                    "event_retry_hints": True,
                    "http_control_command": True,
                    "idempotency_tracking": True,
                    "optimistic_concurrency": True,
                    "per_command_request_schemas": True,
                    "rate_limiting": True,
                    "runtime_only_idempotency_persistence": True,
                    "multi_phase_selection": True,
                    "phase_selection_write": True,
                    "read_api": True,
                    "runtime_override_write": True,
                    "software_update_trigger": True,
                    "state_reads": True,
                },
                "topology": {
                    "backend_mode": "split",
                    "meter_backend": "template_meter",
                    "switch_backend": "switch_group",
                    "charger_backend": "goe_charger",
                },
                "versioning": {
                    "stable_endpoints": expected_stable_endpoints,
                    "experimental_endpoints": ["/v1/events"],
                    "breaking_change_policy": (
                        "Stable v1 endpoints require a version bump for breaking changes; "
                        "experimental endpoints may evolve within v1."
                    ),
                },
            },
        )
        self.assertEqual(
            set(capabilities),
            {
                "ok",
                "api_version",
                "transport",
                "auth_required",
                "read_auth_required",
                "control_auth_required",
                "localhost_only",
                "unix_socket_path",
                "auth_header",
                "auth_scopes",
                "command_names",
                "command_scope_requirements",
                "command_sources",
                "state_endpoints",
                "endpoints",
                "available_modes",
                "supported_phase_selections",
                "features",
                "topology",
                "versioning",
            },
        )
        self.assertEqual(capabilities["transport"], "http")
        self.assertTrue(capabilities["auth_required"])
        self.assertTrue(capabilities["read_auth_required"])
        self.assertTrue(capabilities["control_auth_required"])
        self.assertTrue(capabilities["localhost_only"])
        self.assertEqual(capabilities["unix_socket_path"], "")
        self.assertEqual(capabilities["auth_header"], "Authorization: Bearer <token>")
        self.assertEqual(capabilities["command_sources"], ["dbus", "http", "internal", "mqtt"])
        self.assertEqual(
            set(capabilities["features"]),
            {
                "command_audit_trail",
                "dbus_diagnostics_state",
                "event_stream",
                "event_kind_filters",
                "event_retry_hints",
                "http_control_command",
                "idempotency_tracking",
                "optimistic_concurrency",
                "per_command_request_schemas",
                "rate_limiting",
                "runtime_only_idempotency_persistence",
                "multi_phase_selection",
                "phase_selection_write",
                "read_api",
                "runtime_override_write",
                "software_update_trigger",
                "state_reads",
            },
        )
        self.assertTrue(all(capabilities["features"].values()))
        self.assertEqual(
            capabilities["versioning"],
            {
                "stable_endpoints": sorted(capabilities["versioning"]["stable_endpoints"]),
                "experimental_endpoints": ["/v1/events"],
                "breaking_change_policy": (
                    "Stable v1 endpoints require a version bump for breaking changes; "
                    "experimental endpoints may evolve within v1."
                ),
            },
        )
        self.assertEqual(set(snapshot), {"summary", "operational", "health", "update", "topology"})
        self.assertEqual(snapshot["health"], health)

    def test_control_api_health_payload_contract_reports_fault_reason_and_stale_state(self):
        service = _configured_control_service()
        service._last_health_reason = "contactor-lockout-open"
        service._last_health_code = 7
        service._last_successful_update_at = 123.4
        service._last_recovery_attempt_at = 456.7
        service._is_update_stale = MagicMock(return_value=True)

        health = service._state_api_health_payload()

        service._is_update_stale.assert_called_once()
        self.assertEqual(health["ok"], True)
        self.assertEqual(health["api_version"], "v1")
        self.assertEqual(health["kind"], "health")
        self.assertEqual(health["state"]["health_reason"], "contactor-lockout-open")
        self.assertEqual(health["state"]["health_code"], 7)
        self.assertTrue(health["state"]["fault_active"])
        self.assertEqual(health["state"]["fault_reason"], "contactor-lockout-open")
        self.assertIs(health["state"]["update_stale"], True)
        self.assertEqual(health["state"]["last_successful_update_at"], 123.4)
        self.assertEqual(health["state"]["last_recovery_attempt_at"], 456.7)

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

        self.assertIs(health["state"]["update_stale"], False)

    def test_control_api_state_token_changes_when_snapshot_changes(self):
        service = _ControlService()
        first_token = service._control_api_state_token()
        service.virtual_mode = 2
        second_token = service._control_api_state_token()
        self.assertNotEqual(first_token, second_token)

    def test_control_api_state_token_uses_canonical_json_encoding(self):
        service = _ControlService()
        timestamp = datetime(2026, 7, 4, 12, 30, 45)
        service._control_api_state_token_payload = lambda: {"z": timestamp, "a": {"b": 2, "a": 1}}
        expected_json = json.dumps(
            {"z": timestamp, "a": {"b": 2, "a": 1}},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

        self.assertEqual(
            service._control_api_state_token(),
            hashlib.sha256(expected_json.encode()).hexdigest(),
        )

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
