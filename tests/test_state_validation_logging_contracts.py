# SPDX-License-Identifier: GPL-3.0-or-later
"""Observable warning and dispatch contracts for runtime validation."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from types import SimpleNamespace

from venus_evcharger.controllers.state_validation import RuntimeConfigValidator


ValidationInvocation = Callable[[object], None]
ValidationCase = tuple[ValidationInvocation, object, object, str]


class TestStateValidationLoggingContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = RuntimeConfigValidator(SimpleNamespace())

    def test_primitive_clamps_log_exact_invalid_values_and_not_valid_boundaries(self) -> None:
        cases: tuple[ValidationCase, ...] = (
            (
                lambda svc: RuntimeConfigValidator._clamp_min_int(svc, "value", 5, "Value", " ms"),
                4,
                5,
                "WARNING:root:Value 4 too small, clamping to 5 ms",
            ),
            (
                lambda svc: RuntimeConfigValidator._clamp_non_negative_float(svc, "value"),
                -1.0,
                0.0,
                "WARNING:root:value -1.0 invalid, clamping to 0",
            ),
            (
                lambda svc: RuntimeConfigValidator._clamp_positive_timeout(svc, "value", 2.0, "Timeout"),
                0.0,
                0.1,
                "WARNING:root:Timeout 0.0 invalid, clamping to 2.0",
            ),
            (
                lambda svc: RuntimeConfigValidator._clamp_fraction(svc, "value", "Fraction", 0.25),
                0.0,
                1.0,
                "WARNING:root:Fraction 0.0 outside (0,1], clamping to 0.25",
            ),
            (
                lambda svc: RuntimeConfigValidator._validate_optional_non_negative_int(svc, "value", "Count"),
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
            method = getattr(RuntimeConfigValidator, method_name)
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


if __name__ == "__main__":
    unittest.main()
