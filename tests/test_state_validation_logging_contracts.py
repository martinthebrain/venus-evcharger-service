# SPDX-License-Identifier: GPL-3.0-or-later
"""Observable warning and dispatch contracts for runtime validation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.controllers import state_validation as validation_module
from venus_evcharger.controllers.state import ServiceStateController


class TestStateValidationLoggingContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ServiceStateController(SimpleNamespace(), int)

    def test_primitive_clamps_log_exact_invalid_values_and_not_valid_boundaries(self) -> None:
        cases = (
            (
                lambda svc: ServiceStateController._clamp_min_int(svc, "value", 5, "Value", " ms"),
                4,
                5,
                "WARNING:root:Value 4 too small, clamping to 5 ms",
            ),
            (
                lambda svc: ServiceStateController._clamp_non_negative_float(svc, "value"),
                -1.0,
                0.0,
                "WARNING:root:value -1.0 invalid, clamping to 0",
            ),
            (
                lambda svc: ServiceStateController._clamp_positive_timeout(svc, "value", 2.0, "Timeout"),
                0.0,
                0.1,
                "WARNING:root:Timeout 0.0 invalid, clamping to 2.0",
            ),
            (
                lambda svc: ServiceStateController._clamp_percentage(svc, "value", "Percent"),
                -1.0,
                0.0,
                "WARNING:root:Percent -1.0 outside 0..100, clamping",
            ),
            (
                lambda svc: ServiceStateController._clamp_fraction(svc, "value", "Fraction", 0.25),
                0.0,
                1.0,
                "WARNING:root:Fraction 0.0 outside (0,1], clamping to 0.25",
            ),
            (
                lambda svc: ServiceStateController._validate_optional_non_negative_int(svc, "value", "Count"),
                -1,
                0,
                "WARNING:root:Count -1 invalid, clamping to 0",
            ),
        )
        for invoke, invalid, valid_boundary, warning in cases:
            with self.subTest(warning=warning):
                with self.assertLogs(level="WARNING") as logs:
                    invoke(SimpleNamespace(value=invalid))
                self.assertEqual(logs.output, [warning])
                with self.assertNoLogs(level="WARNING"):
                    invoke(SimpleNamespace(value=valid_boundary))
        with self.assertNoLogs(level="WARNING"):
            ServiceStateController._clamp_percentage(SimpleNamespace(value=100.0), "value", "Percent")

    def test_surplus_pair_logs_only_when_stop_exceeds_start(self) -> None:
        invalid = SimpleNamespace(start=100.0, stop=101.0)
        with self.assertLogs(level="WARNING") as logs:
            ServiceStateController._clamp_surplus_pair(invalid, "start", "stop", "Start", "Stop")
        self.assertEqual(logs.output, ["WARNING:root:Stop 101.0 above Start 100.0, clamping"])
        with self.assertNoLogs(level="WARNING"):
            ServiceStateController._clamp_surplus_pair(
                SimpleNamespace(start=100.0, stop=100.0),
                "start",
                "stop",
                "Start",
                "Stop",
            )

    def test_startup_retry_logs_invalid_value_and_accepts_zero(self) -> None:
        invalid = SimpleNamespace(startup_device_info_retries=-1)
        with self.assertLogs(level="WARNING") as logs:
            ServiceStateController._validate_startup_retry_config(invalid)
        self.assertEqual(invalid.startup_device_info_retries, 0)
        self.assertEqual(
            logs.output,
            ["WARNING:root:StartupDeviceInfoRetries -1 invalid, clamping to 0"],
        )
        with self.assertNoLogs(level="WARNING"):
            ServiceStateController._validate_startup_retry_config(
                SimpleNamespace(startup_device_info_retries=0)
            )

    def test_soc_relationships_log_only_when_hysteresis_is_inverted(self) -> None:
        equal = SimpleNamespace(
            auto_min_soc=40.0,
            auto_resume_soc=40.0,
            auto_high_soc_threshold=80.0,
            auto_high_soc_release_threshold=80.0,
        )
        with self.assertNoLogs(level="WARNING"):
            ServiceStateController(equal, int)._clamp_soc_thresholds()

        inverted = SimpleNamespace(
            auto_min_soc=40.0,
            auto_resume_soc=30.0,
            auto_high_soc_threshold=80.0,
            auto_high_soc_release_threshold=90.0,
        )
        with self.assertLogs(level="WARNING") as logs:
            ServiceStateController(inverted, int)._clamp_soc_thresholds()
        self.assertEqual(
            logs.output,
            [
                "WARNING:root:AutoHighSocReleaseThreshold 90.0 above AutoHighSocThreshold 80.0, clamping",
                "WARNING:root:AutoResumeSoc 30.0 below AutoMinSoc 40.0, clamping to AutoMinSoc",
            ],
        )
        self.assertEqual(inverted.auto_high_soc_release_threshold, 80.0)
        self.assertEqual(inverted.auto_resume_soc, 40.0)

    def test_reference_power_and_volatility_warnings_are_stable_contracts(self) -> None:
        reference = SimpleNamespace(auto_reference_charge_power_watts=0.0)
        with self.assertLogs(level="WARNING") as logs:
            ServiceStateController._clamp_legacy_reference_power(reference)
        self.assertEqual(
            logs.output,
            ["WARNING:root:AutoReferenceChargePowerWatts 0.0 invalid, clamping to 1900.0"],
        )

        band = SimpleNamespace(
            auto_stop_surplus_volatility_low_watts=200.0,
            auto_stop_surplus_volatility_high_watts=100.0,
        )
        with self.assertLogs(level="WARNING") as logs:
            ServiceStateController._clamp_legacy_volatility_band(band)
        self.assertEqual(
            logs.output,
            [
                "WARNING:root:AutoStopSurplusVolatilityHighWatts 100.0 "
                "below AutoStopSurplusVolatilityLowWatts 200.0, clamping"
            ],
        )
        with self.assertNoLogs(level="WARNING"):
            ServiceStateController._clamp_legacy_volatility_band(
                SimpleNamespace(
                    auto_stop_surplus_volatility_low_watts=200.0,
                    auto_stop_surplus_volatility_high_watts=200.0,
                )
            )

    def test_each_mode_normalizer_accepts_all_values_and_logs_its_fallback(self) -> None:
        cases = (
            (
                "_normalize_discharge_balance_bias_mode",
                "auto_battery_discharge_balance_bias_mode",
                ("always", "export_only", "above_reserve_band", "export_and_above_reserve_band"),
                "always",
                "AutoBatteryDischargeBalanceBiasMode",
            ),
            (
                "_normalize_discharge_balance_coordination_support_mode",
                "auto_battery_discharge_balance_coordination_support_mode",
                ("supported_only", "allow_experimental"),
                "supported_only",
                "AutoBatteryDischargeBalanceCoordinationSupportMode",
            ),
            (
                "_normalize_victron_balance_support_mode",
                "auto_battery_discharge_balance_victron_bias_support_mode",
                ("supported_only", "allow_experimental"),
                "allow_experimental",
                "AutoBatteryDischargeBalanceVictronBiasSupportMode",
            ),
            (
                "_normalize_victron_balance_activation_mode",
                "auto_battery_discharge_balance_victron_bias_activation_mode",
                ("always", "export_only", "above_reserve_band", "export_and_above_reserve_band"),
                "always",
                "AutoBatteryDischargeBalanceVictronBiasActivationMode",
            ),
        )
        for method_name, attr_name, allowed, fallback, label in cases:
            method = getattr(ServiceStateController, method_name)
            for value in allowed:
                with self.subTest(method=method_name, value=value), self.assertNoLogs(level="WARNING"):
                    svc = SimpleNamespace(**{attr_name: value})
                    method(svc)
                    self.assertEqual(getattr(svc, attr_name), value)
            svc = SimpleNamespace(**{attr_name: "invalid"})
            with self.assertLogs(level="WARNING") as logs:
                method(svc)
            self.assertEqual(getattr(svc, attr_name), fallback)
            self.assertEqual(
                logs.output,
                [f"WARNING:root:{label} invalid invalid, clamping to {fallback}"],
            )


class TestStateValidationDispatchContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ServiceStateController(SimpleNamespace(), int)

    def test_legacy_validation_dispatches_every_stage_once_in_order(self) -> None:
        svc = SimpleNamespace()
        names = (
            "_clamp_legacy_non_negative_auto_values",
            "_clamp_legacy_reference_power",
            "_clamp_soc_thresholds",
            "_clamp_surplus_thresholds",
            "_clamp_legacy_fractional_values",
            "_clamp_legacy_volatility_band",
            "_normalize_discharge_balance_bias_mode",
            "_normalize_discharge_balance_coordination_support_mode",
            "_normalize_victron_balance_activation_mode",
            "_normalize_victron_balance_support_mode",
            "_normalize_victron_balance_auto_apply_settings",
        )
        patchers = [patch.object(ServiceStateController, name) for name in names]
        mocks = [patcher.start() for patcher in patchers]
        for patcher in patchers:
            self.addCleanup(patcher.stop)
        with patch.object(ServiceStateController, "_validate_optional_non_negative_int") as optional_int:
            self.controller._validate_legacy_auto_config(svc)
        for name, helper in zip(names, mocks):
            if name == "_clamp_soc_thresholds":
                helper.assert_called_once_with()
            else:
                helper.assert_called_once_with(svc)
        self.assertEqual(
            optional_int.call_args_list,
            [
                call(svc, "auto_phase_mismatch_lockout_count", "AutoPhaseMismatchLockoutCount"),
                call(svc, "auto_contactor_fault_latch_count", "AutoContactorFaultLatchCount"),
            ],
        )

    def test_soc_surplus_and_fraction_dispatches_use_exact_fields(self) -> None:
        svc = SimpleNamespace(
            auto_min_soc=30.0,
            auto_resume_soc=40.0,
            auto_high_soc_threshold=80.0,
            auto_high_soc_release_threshold=70.0,
            auto_start_surplus_watts=1000.0,
            auto_stop_surplus_watts=900.0,
            auto_high_soc_start_surplus_watts=800.0,
            auto_high_soc_stop_surplus_watts=700.0,
            auto_stop_ewma_alpha=0.1,
            auto_stop_ewma_alpha_stable=0.2,
            auto_stop_ewma_alpha_volatile=0.3,
            auto_learn_charge_power_alpha=0.4,
        )
        controller = ServiceStateController(svc, int)
        with patch.object(ServiceStateController, "_clamp_percentage") as percentage:
            controller._clamp_soc_thresholds()
        self.assertEqual(
            percentage.call_args_list,
            [
                call(svc, "auto_min_soc", "AutoMinSoc"),
                call(svc, "auto_resume_soc", "AutoResumeSoc"),
                call(svc, "auto_high_soc_threshold", "AutoHighSocThreshold"),
                call(svc, "auto_high_soc_release_threshold", "AutoHighSocReleaseThreshold"),
            ],
        )
        with patch.object(validation_module._StateValidation, "_clamp_surplus_pair") as surplus:
            self.controller._clamp_surplus_thresholds(svc)
        self.assertEqual(
            surplus.call_args_list,
            [
                call(svc, "auto_start_surplus_watts", "auto_stop_surplus_watts", "AutoStartSurplusWatts", "AutoStopSurplusWatts"),
                call(svc, "auto_high_soc_start_surplus_watts", "auto_high_soc_stop_surplus_watts", "AutoHighSocStartSurplusWatts", "AutoHighSocStopSurplusWatts"),
            ],
        )
        for partial in (
            SimpleNamespace(
                auto_start_surplus_watts=1000.0,
                auto_stop_surplus_watts=900.0,
                auto_high_soc_start_surplus_watts=800.0,
            ),
            SimpleNamespace(
                auto_start_surplus_watts=1000.0,
                auto_stop_surplus_watts=900.0,
                auto_high_soc_stop_surplus_watts=700.0,
            ),
        ):
            with patch.object(validation_module._StateValidation, "_clamp_surplus_pair") as partial_surplus:
                self.controller._clamp_surplus_thresholds(partial)
            partial_surplus.assert_called_once_with(
                partial,
                "auto_start_surplus_watts",
                "auto_stop_surplus_watts",
                "AutoStartSurplusWatts",
                "AutoStopSurplusWatts",
            )
        with patch.object(ServiceStateController, "_clamp_fraction") as fraction:
            self.controller._clamp_legacy_fractional_values(svc)
        self.assertEqual(
            fraction.call_args_list,
            [
                call(svc, "auto_stop_ewma_alpha", "AutoStopEwmaAlpha", 0.35),
                call(svc, "auto_stop_ewma_alpha_stable", "AutoStopEwmaAlphaStable", 0.55),
                call(svc, "auto_stop_ewma_alpha_volatile", "AutoStopEwmaAlphaVolatile", 0.15),
                call(svc, "auto_learn_charge_power_alpha", "AutoLearnChargePowerAlpha", 0.2),
            ],
        )


if __name__ == "__main__":
    unittest.main()
