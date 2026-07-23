# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary and orchestration contracts for runtime configuration validation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.controllers import state_validation as validation_module
from venus_evcharger.controllers.state_validation import RuntimeConfigValidator
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS


class TestStateValidationPrimitiveContracts(unittest.TestCase):
    def test_minimum_integer_and_positive_timeout_boundaries(self) -> None:
        svc = SimpleNamespace(value=4)
        RuntimeConfigValidator._clamp_min_int(svc, "value", 5, "Value", " units")
        self.assertEqual(svc.value, 5)
        svc.value = 5
        RuntimeConfigValidator._clamp_min_int(svc, "value", 5, "Value", " units")
        self.assertEqual(svc.value, 5)

        svc.value = 0.0
        RuntimeConfigValidator._clamp_positive_timeout(svc, "value", 2.0, "Value")
        self.assertEqual(svc.value, 2.0)
        svc.value = 0.1
        RuntimeConfigValidator._clamp_positive_timeout(svc, "value", 2.0, "Value")
        self.assertEqual(svc.value, 0.1)

    def test_non_negative_float_and_optional_integer_contracts(self) -> None:
        svc = SimpleNamespace()
        RuntimeConfigValidator._clamp_non_negative_float(svc, "missing")
        RuntimeConfigValidator._validate_optional_non_negative_int(svc, "missing", "Missing")

        svc.value = -0.1
        RuntimeConfigValidator._clamp_non_negative_float(svc, "value")
        self.assertEqual(svc.value, 0.0)
        svc.value = 0.0
        RuntimeConfigValidator._clamp_non_negative_float(svc, "value")
        self.assertEqual(svc.value, 0.0)

        svc.count = -1
        RuntimeConfigValidator._validate_optional_non_negative_int(svc, "count", "Count")
        self.assertEqual(svc.count, 0)
        svc.count = 0
        RuntimeConfigValidator._validate_optional_non_negative_int(svc, "count", "Count")
        self.assertEqual(svc.count, 0)

    def test_fraction_boundaries(self) -> None:
        svc = SimpleNamespace(fraction=0.0)
        RuntimeConfigValidator._clamp_fraction(svc, "fraction", "Fraction", 0.25)
        self.assertEqual(svc.fraction, 0.25)
        svc.fraction = 1.01
        RuntimeConfigValidator._clamp_fraction(svc, "fraction", "Fraction", 0.25)
        self.assertEqual(svc.fraction, 0.25)
        for valid in (0.01, 1.0):
            svc.fraction = valid
            RuntimeConfigValidator._clamp_fraction(svc, "fraction", "Fraction", 0.25)
            self.assertEqual(svc.fraction, valid)

    def test_primitive_validators_reject_wrong_runtime_types_with_exact_errors(self) -> None:
        with self.assertRaisesRegex(TypeError, "^value must be an integer$"):
            RuntimeConfigValidator._clamp_min_int(
                SimpleNamespace(value=1.0), "value", 1, "Value", ""
            )
        with self.assertRaisesRegex(TypeError, "^value must be numeric$"):
            RuntimeConfigValidator._clamp_non_negative_float(
                SimpleNamespace(value="invalid"), "value"
            )
        with self.assertRaisesRegex(TypeError, "^value must be numeric$"):
            RuntimeConfigValidator._clamp_positive_timeout(
                SimpleNamespace(value="invalid"), "value", 1.0, "Value"
            )
        with self.assertRaisesRegex(TypeError, "^value must be numeric$"):
            RuntimeConfigValidator._clamp_fraction(
                SimpleNamespace(value=None), "value", "Value", 0.5
            )
        with self.assertRaisesRegex(TypeError, "^value must be an integer$"):
            RuntimeConfigValidator._validate_optional_non_negative_int(
                SimpleNamespace(value=1.0), "value", "Value"
            )

class TestStateValidationOrchestrationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SimpleNamespace()
        self.controller = RuntimeConfigValidator(self.service)

    def test_validate_runtime_config_calls_each_validation_stage_in_order(self) -> None:
        svc = SimpleNamespace(
            poll_interval_ms=100,
            sign_of_life_minutes=1,
            auto_pv_max_services=1,
            auto_policy=AutoPolicy(),
        )
        controller = RuntimeConfigValidator(svc)
        with (
            patch.object(RuntimeConfigValidator, "_clamp_min_int") as clamp_min,
            patch.object(RuntimeConfigValidator, "_clamp_interval_settings") as intervals,
            patch.object(RuntimeConfigValidator, "_validate_scheduled_runtime_config") as scheduled,
            patch.object(RuntimeConfigValidator, "_validate_timeout_settings") as timeouts,
            patch.object(RuntimeConfigValidator, "_validate_balance_runtime_config") as balance,
            patch.object(validation_module, "validate_auto_policy") as policy,
        ):
            controller.validate()
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
        timeouts.assert_called_once_with(svc)
        balance.assert_called_once_with(svc)
        policy.assert_called_once_with(svc.auto_policy)

    def test_validate_runtime_config_requires_a_bootstrap_owned_policy(self) -> None:
        svc = SimpleNamespace(
            poll_interval_ms=100,
            sign_of_life_minutes=1,
            auto_pv_max_services=1,
        )
        controller = RuntimeConfigValidator(svc)
        with (
            patch.object(RuntimeConfigValidator, "_clamp_min_int"),
            patch.object(RuntimeConfigValidator, "_clamp_interval_settings"),
            patch.object(RuntimeConfigValidator, "_validate_scheduled_runtime_config"),
            patch.object(RuntimeConfigValidator, "_validate_timeout_settings"),
            patch.object(RuntimeConfigValidator, "_validate_balance_runtime_config"),
        ):
            with self.assertRaisesRegex(AttributeError, "auto_policy"):
                controller.validate()

    def test_validate_runtime_config_rejects_a_non_policy_owner_value(self) -> None:
        svc = SimpleNamespace(
            poll_interval_ms=100,
            sign_of_life_minutes=1,
            auto_pv_max_services=1,
            auto_policy=object(),
        )
        controller = RuntimeConfigValidator(svc)
        with (
            patch.object(RuntimeConfigValidator, "_clamp_min_int"),
            patch.object(RuntimeConfigValidator, "_clamp_interval_settings"),
            patch.object(RuntimeConfigValidator, "_validate_scheduled_runtime_config"),
            patch.object(RuntimeConfigValidator, "_validate_timeout_settings"),
            patch.object(RuntimeConfigValidator, "_validate_balance_runtime_config"),
        ):
            with self.assertRaisesRegex(
                TypeError, "^state service must expose AutoPolicy as auto_policy$"
            ):
                controller.validate()

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
            RuntimeConfigValidator._validate_scheduled_runtime_config(svc)
        days.assert_called_once_with("days", DEFAULT_SCHEDULED_ENABLED_DAYS)
        hhmm.assert_called_once_with("time", "06:30")
        self.assertEqual(svc.auto_scheduled_enabled_days, "normalized-days")
        self.assertEqual(svc.auto_scheduled_latest_end_time, "normalized-time")
        self.assertEqual(svc.auto_scheduled_night_current_amps, 0.0)
        RuntimeConfigValidator._validate_scheduled_runtime_config(SimpleNamespace())

    def test_timeout_settings_cover_required_and_optional_timeouts(self) -> None:
        svc = SimpleNamespace(
            shelly_request_timeout_seconds=0.0,
            dbus_method_timeout_seconds=0.0,
            auto_audit_log_max_age_hours=0.0,
            auto_audit_log_repeat_seconds=0.0,
        )
        with patch.object(RuntimeConfigValidator, "_clamp_positive_timeout") as clamp:
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


class TestStateValidationGeneralContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = RuntimeConfigValidator(SimpleNamespace())

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
                method = getattr(RuntimeConfigValidator, method_name)
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
            patch.object(RuntimeConfigValidator, "_clamp_fraction") as fraction,
            patch.object(RuntimeConfigValidator, "_validate_optional_non_negative_int") as integer,
            patch.object(RuntimeConfigValidator, "_clamp_non_negative_float") as non_negative,
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
