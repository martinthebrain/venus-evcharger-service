# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary and orchestration contracts for runtime configuration validation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.controllers import state_validation as validation_module
from venus_evcharger.controllers.state import ServiceStateController


class TestStateValidationPrimitiveContracts(unittest.TestCase):
    def test_minimum_integer_and_positive_timeout_boundaries(self) -> None:
        svc = SimpleNamespace(value=4)
        ServiceStateController._clamp_min_int(svc, "value", 5, "Value", " units")
        self.assertEqual(svc.value, 5)
        svc.value = 5
        ServiceStateController._clamp_min_int(svc, "value", 5, "Value", " units")
        self.assertEqual(svc.value, 5)

        svc.value = 0.0
        ServiceStateController._clamp_positive_timeout(svc, "value", 2.0, "Value")
        self.assertEqual(svc.value, 2.0)
        svc.value = 0.1
        ServiceStateController._clamp_positive_timeout(svc, "value", 2.0, "Value")
        self.assertEqual(svc.value, 0.1)

    def test_non_negative_float_and_optional_integer_contracts(self) -> None:
        svc = SimpleNamespace()
        ServiceStateController._clamp_non_negative_float(svc, "missing")
        ServiceStateController._validate_optional_non_negative_int(svc, "missing", "Missing")

        svc.value = -0.1
        ServiceStateController._clamp_non_negative_float(svc, "value")
        self.assertEqual(svc.value, 0.0)
        svc.value = 0.0
        ServiceStateController._clamp_non_negative_float(svc, "value")
        self.assertEqual(svc.value, 0.0)

        svc.count = -1
        ServiceStateController._validate_optional_non_negative_int(svc, "count", "Count")
        self.assertEqual(svc.count, 0)
        svc.count = 0
        ServiceStateController._validate_optional_non_negative_int(svc, "count", "Count")
        self.assertEqual(svc.count, 0)

    def test_percentage_fraction_and_surplus_pair_boundaries(self) -> None:
        svc = SimpleNamespace(percent=-1.0, fraction=0.0, start=100.0, stop=101.0)
        ServiceStateController._clamp_percentage(svc, "percent", "Percent")
        self.assertEqual(svc.percent, 0.0)
        svc.percent = 101.0
        ServiceStateController._clamp_percentage(svc, "percent", "Percent")
        self.assertEqual(svc.percent, 100.0)
        for valid in (0.0, 100.0):
            svc.percent = valid
            ServiceStateController._clamp_percentage(svc, "percent", "Percent")
            self.assertEqual(svc.percent, valid)

        ServiceStateController._clamp_fraction(svc, "fraction", "Fraction", 0.25)
        self.assertEqual(svc.fraction, 0.25)
        svc.fraction = 1.01
        ServiceStateController._clamp_fraction(svc, "fraction", "Fraction", 0.25)
        self.assertEqual(svc.fraction, 0.25)
        for valid in (0.01, 1.0):
            svc.fraction = valid
            ServiceStateController._clamp_fraction(svc, "fraction", "Fraction", 0.25)
            self.assertEqual(svc.fraction, valid)

        ServiceStateController._clamp_surplus_pair(svc, "start", "stop", "Start", "Stop")
        self.assertEqual(svc.stop, 100.0)
        svc.stop = 100.0
        ServiceStateController._clamp_surplus_pair(svc, "start", "stop", "Start", "Stop")
        self.assertEqual(svc.stop, 100.0)


class TestStateValidationOrchestrationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SimpleNamespace()
        self.controller = ServiceStateController(self.service, int)

    def test_validate_runtime_config_calls_each_validation_stage_in_order(self) -> None:
        svc = SimpleNamespace(
            poll_interval_ms=100,
            sign_of_life_minutes=1,
            auto_pv_max_services=1,
            startup_device_info_retries=0,
            auto_policy="policy",
        )
        controller = ServiceStateController(svc, int)
        with (
            patch.object(ServiceStateController, "_clamp_min_int") as clamp_min,
            patch.object(ServiceStateController, "_clamp_interval_settings") as intervals,
            patch.object(ServiceStateController, "_validate_scheduled_runtime_config") as scheduled,
            patch.object(ServiceStateController, "_validate_startup_retry_config") as startup,
            patch.object(ServiceStateController, "_validate_timeout_settings") as timeouts,
            patch.object(validation_module, "validate_auto_policy") as policy,
        ):
            controller.validate_runtime_config()
        self.assertEqual(
            clamp_min.call_args_list,
            [
                call(svc, "poll_interval_ms", 100, "PollIntervalMs", " ms"),
                call(svc, "sign_of_life_minutes", 1, "SignOfLifeLog", " minute"),
                call(svc, "auto_pv_max_services", 1, "AutoPvMaxServices", ""),
            ],
        )
        intervals.assert_called_once_with()
        scheduled.assert_called_once_with(svc)
        startup.assert_called_once_with(svc)
        timeouts.assert_called_once_with(svc)
        policy.assert_called_once_with("policy", svc)

    def test_validate_runtime_config_uses_legacy_validation_without_policy(self) -> None:
        svc = SimpleNamespace(
            poll_interval_ms=100,
            sign_of_life_minutes=1,
            auto_pv_max_services=1,
            startup_device_info_retries=0,
        )
        controller = ServiceStateController(svc, int)
        with (
            patch.object(ServiceStateController, "_clamp_min_int"),
            patch.object(ServiceStateController, "_clamp_interval_settings"),
            patch.object(ServiceStateController, "_validate_scheduled_runtime_config"),
            patch.object(ServiceStateController, "_validate_startup_retry_config"),
            patch.object(ServiceStateController, "_validate_timeout_settings"),
            patch.object(ServiceStateController, "_validate_legacy_auto_config") as legacy,
            patch.object(validation_module, "validate_auto_policy") as policy,
        ):
            controller.validate_runtime_config()
        legacy.assert_called_once_with(svc)
        policy.assert_not_called()

    def test_scheduled_runtime_config_normalizes_every_optional_setting(self) -> None:
        svc = SimpleNamespace(
            auto_scheduled_enabled_days="days",
            auto_scheduled_latest_end_time="time",
            auto_scheduled_night_current_amps=-1.0,
        )
        with (
            patch.object(validation_module, "scheduled_enabled_days_text", return_value="normalized-days") as days,
            patch.object(validation_module, "normalize_hhmm_text", return_value="normalized-time") as hhmm,
        ):
            ServiceStateController._validate_scheduled_runtime_config(svc)
        days.assert_called_once_with("days", validation_module.DEFAULT_SCHEDULED_ENABLED_DAYS)
        hhmm.assert_called_once_with("time", "06:30")
        self.assertEqual(svc.auto_scheduled_enabled_days, "normalized-days")
        self.assertEqual(svc.auto_scheduled_latest_end_time, "normalized-time")
        self.assertEqual(svc.auto_scheduled_night_current_amps, 0.0)
        ServiceStateController._validate_scheduled_runtime_config(SimpleNamespace())

    def test_timeout_settings_cover_required_and_optional_timeouts(self) -> None:
        svc = SimpleNamespace(
            shelly_request_timeout_seconds=0.0,
            dbus_method_timeout_seconds=0.0,
            auto_audit_log_max_age_hours=0.0,
            auto_audit_log_repeat_seconds=0.0,
        )
        with patch.object(ServiceStateController, "_clamp_positive_timeout") as clamp:
            self.controller._validate_timeout_settings(svc)
        self.assertEqual(
            clamp.call_args_list,
            [
                call(svc, "shelly_request_timeout_seconds", 2.0, "ShellyRequestTimeoutSeconds"),
                call(svc, "dbus_method_timeout_seconds", 1.0, "DbusMethodTimeoutSeconds"),
                call(svc, "auto_audit_log_max_age_hours", 168.0, "AutoAuditLogMaxAgeHours"),
                call(svc, "auto_audit_log_repeat_seconds", 30.0, "AutoAuditLogRepeatSeconds"),
            ],
        )


class TestStateValidationLegacyContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ServiceStateController(SimpleNamespace(), int)

    def test_all_legacy_non_negative_fields_are_clamped(self) -> None:
        svc = SimpleNamespace()
        fields = (
            "auto_grid_recovery_start_seconds",
            "auto_stop_surplus_delay_seconds",
            "auto_scheduled_night_start_delay_seconds",
            "auto_stop_surplus_volatility_low_watts",
            "auto_stop_surplus_volatility_high_watts",
            "auto_battery_discharge_balance_warn_error_watts",
            "auto_battery_discharge_balance_bias_start_error_watts",
            "auto_battery_discharge_balance_bias_max_penalty_watts",
            "auto_battery_discharge_balance_bias_reserve_margin_soc",
            "auto_battery_discharge_balance_coordination_start_error_watts",
            "auto_battery_discharge_balance_coordination_max_penalty_watts",
            "auto_battery_discharge_balance_victron_bias_base_setpoint_watts",
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
            "auto_battery_discharge_balance_victron_bias_kp",
            "auto_battery_discharge_balance_victron_bias_ki",
            "auto_battery_discharge_balance_victron_bias_kd",
            "auto_battery_discharge_balance_victron_bias_integral_limit_watts",
            "auto_battery_discharge_balance_victron_bias_max_abs_watts",
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            "auto_battery_discharge_balance_victron_bias_min_update_seconds",
            "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence",
            "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score",
            "auto_battery_discharge_balance_victron_bias_auto_apply_blend",
            "auto_phase_upshift_headroom_watts",
            "auto_phase_downshift_margin_watts",
            "auto_learn_charge_power_min_watts",
            "auto_learn_charge_power_start_delay_seconds",
            "auto_learn_charge_power_window_seconds",
            "auto_learn_charge_power_max_age_seconds",
            "auto_phase_mismatch_retry_seconds",
            "auto_phase_mismatch_lockout_seconds",
            "auto_contactor_fault_latch_seconds",
        )
        for field in fields:
            setattr(svc, field, -1.0)
        self.controller._clamp_legacy_non_negative_auto_values(svc)
        self.assertEqual({field: getattr(svc, field) for field in fields}, {field: 0.0 for field in fields})

    def test_reference_power_and_volatility_contracts_cover_absent_valid_and_invalid_values(self) -> None:
        ServiceStateController._clamp_legacy_reference_power(SimpleNamespace())
        valid = SimpleNamespace(auto_reference_charge_power_watts=1.0)
        ServiceStateController._clamp_legacy_reference_power(valid)
        self.assertEqual(valid.auto_reference_charge_power_watts, 1.0)
        invalid = SimpleNamespace(auto_reference_charge_power_watts=0.0)
        ServiceStateController._clamp_legacy_reference_power(invalid)
        self.assertEqual(invalid.auto_reference_charge_power_watts, 1900.0)

        ServiceStateController._clamp_legacy_volatility_band(SimpleNamespace())
        band = SimpleNamespace(
            auto_stop_surplus_volatility_low_watts=200.0,
            auto_stop_surplus_volatility_high_watts=100.0,
        )
        ServiceStateController._clamp_legacy_volatility_band(band)
        self.assertEqual(band.auto_stop_surplus_volatility_high_watts, 200.0)
        band.auto_stop_surplus_volatility_high_watts = 200.0
        ServiceStateController._clamp_legacy_volatility_band(band)
        self.assertEqual(band.auto_stop_surplus_volatility_high_watts, 200.0)

    def test_mode_normalizers_accept_canonical_values_and_apply_documented_fallbacks(self) -> None:
        cases = (
            ("_normalize_discharge_balance_bias_mode", "auto_battery_discharge_balance_bias_mode", "export_only", "always"),
            (
                "_normalize_discharge_balance_coordination_support_mode",
                "auto_battery_discharge_balance_coordination_support_mode",
                "allow_experimental",
                "supported_only",
            ),
            (
                "_normalize_victron_balance_support_mode",
                "auto_battery_discharge_balance_victron_bias_support_mode",
                "supported_only",
                "allow_experimental",
            ),
            (
                "_normalize_victron_balance_activation_mode",
                "auto_battery_discharge_balance_victron_bias_activation_mode",
                "above_reserve_band",
                "always",
            ),
        )
        for method_name, attr_name, valid, fallback in cases:
            with self.subTest(method=method_name):
                method = getattr(ServiceStateController, method_name)
                method(SimpleNamespace())
                svc = SimpleNamespace(**{attr_name: f" {valid.upper()} "})
                method(svc)
                self.assertEqual(getattr(svc, attr_name), valid)
                setattr(svc, attr_name, "invalid")
                method(svc)
                self.assertEqual(getattr(svc, attr_name), fallback)

    def test_auto_apply_settings_dispatch_exact_field_contracts(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.5,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.5,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.5,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=1,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=1.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=1.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes=1,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds=1.0,
            auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=0.5,
        )
        with (
            patch.object(ServiceStateController, "_clamp_fraction") as fraction,
            patch.object(ServiceStateController, "_validate_optional_non_negative_int") as integer,
            patch.object(ServiceStateController, "_clamp_non_negative_float") as non_negative,
        ):
            self.controller._normalize_victron_balance_auto_apply_settings(svc)
        self.assertEqual(
            fraction.call_args_list,
            [
                call(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence", "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence", 0.85),
                call(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score", "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore", 0.75),
                call(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_blend", "AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend", 0.25),
                call(svc, "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score", "AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore", 0.45),
            ],
        )
        self.assertEqual(
            integer.call_args_list,
            [
                call(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples", "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples"),
                call(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes", "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges"),
            ],
        )
        self.assertEqual(
            non_negative.call_args_list,
            [
                call(svc, "auto_battery_discharge_balance_victron_bias_observation_window_seconds"),
                call(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds"),
                call(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
