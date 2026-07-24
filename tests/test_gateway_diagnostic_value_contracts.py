# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact behavioral contracts for semantic gateway diagnostic values."""

from __future__ import annotations

import unittest

from venus_evcharger.ports import gateway_diagnostic_values as values_contract
from venus_evcharger.ports.gateway_diagnostic_values import (
    GatewayDiagnosticApplicability,
    GatewayDiagnosticSample,
    GatewayDiagnosticStatus,
)


class GatewayDiagnosticValueContractsTests(unittest.TestCase):
    def test_literal_parsers_report_the_exact_contract_member(self) -> None:
        with self.assertRaisesRegex(ValueError, "^gateway diagnostic name is invalid$"):
            values_contract.gateway_diagnostic_field_name("invalid")
        with self.assertRaisesRegex(ValueError, "^gateway diagnostic status is invalid$"):
            values_contract.gateway_diagnostic_status("invalid")
        with self.assertRaisesRegex(ValueError, "^gateway diagnostic applicability is invalid$"):
            values_contract.gateway_diagnostic_applicability("invalid")

    def test_operating_mode_reader_accepts_only_the_three_schema_codes(self) -> None:
        self.assertEqual(values_contract._operating_mode(0), 0)
        self.assertEqual(values_contract._operating_mode(1), 1)
        self.assertEqual(values_contract._operating_mode(2), 2)
        with self.assertRaisesRegex(TypeError, "^operating_mode value must be an integer$"):
            values_contract._operating_mode(True)
        with self.assertRaisesRegex(ValueError, "^operating_mode value must be non-negative$"):
            values_contract._operating_mode(-1)
        with self.assertRaisesRegex(ValueError, "^operating_mode value must be 0, 1, or 2$"):
            values_contract._operating_mode(3)

    def test_scalar_readers_preserve_field_specific_type_contracts(self) -> None:
        self.assertEqual(values_contract._state_code(4), 4)
        self.assertIs(values_contract._charging_enabled(False), False)
        self.assertIs(values_contract._auto_start_enabled(True), True)
        self.assertIs(values_contract._runtime_overrides_active(False), False)
        self.assertEqual(values_contract._ac_power(-12.5), -12.5)
        self.assertEqual(values_contract._semantic_text(""), "")
        with self.assertRaisesRegex(TypeError, "^charger_state_code value must be an integer$"):
            values_contract._state_code(False)
        with self.assertRaisesRegex(TypeError, "^charging_enabled value must be a boolean$"):
            values_contract._charging_enabled(1)
        with self.assertRaisesRegex(TypeError, "^auto_start_enabled value must be a boolean$"):
            values_contract._auto_start_enabled(1)
        with self.assertRaisesRegex(TypeError, "^runtime_overrides_active value must be a boolean$"):
            values_contract._runtime_overrides_active(1)
        with self.assertRaisesRegex(ValueError, "^ac_power_w value must be finite$"):
            values_contract._ac_power(float("inf"))
        with self.assertRaisesRegex(ValueError, "^semantic diagnostic value must be text$"):
            values_contract._semantic_text(1)

    def test_observed_quality_requires_value_positive_ordered_timestamps(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^fresh diagnostic requires a value and positive timestamps$",
        ):
            GatewayDiagnosticSample("operating_mode", None, "fresh", 1.0, 1.0, 1.0)
        with self.assertRaisesRegex(
            ValueError,
            "^stale diagnostic requires a value and positive timestamps$",
        ):
            GatewayDiagnosticSample("operating_mode", 1, "stale", 0.0, 1.0, 1.0)
        with self.assertRaisesRegex(
            ValueError,
            "^fresh diagnostic requires a value and positive timestamps$",
        ):
            GatewayDiagnosticSample("operating_mode", 1, "fresh", 1.0, 0.0, 1.0)
        with self.assertRaisesRegex(
            ValueError,
            "^diagnostic changed_at must not exceed confirmed_at$",
        ):
            GatewayDiagnosticSample("operating_mode", 1, "fresh", 2.0, 1.0, 1.0)

    def test_observed_quality_requires_matching_applicability(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^fresh diagnostic requires applicability='applicable'$",
        ):
            GatewayDiagnosticSample(
                "operating_mode",
                1,
                "fresh",
                1.0,
                1.0,
                1.0,
                applicability="unknown",
            )
        with self.assertRaisesRegex(
            ValueError,
            "^inactive diagnostic requires applicability='not-applicable'$",
        ):
            GatewayDiagnosticSample(
                "decision_state",
                None,
                "inactive",
                0.0,
                0.0,
                1.0,
                reason_code="manual-mode",
            )
        with self.assertRaisesRegex(ValueError, "^inactive diagnostic requires reason_code$"):
            GatewayDiagnosticSample(
                "decision_state",
                None,
                "inactive",
                0.0,
                0.0,
                1.0,
                applicability="not-applicable",
            )

    def test_inactive_observed_value_still_requires_valid_timestamps(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^inactive diagnostic requires a value and positive timestamps$",
        ):
            GatewayDiagnosticSample(
                "decision_state",
                "idle",
                "inactive",
                0.0,
                0.0,
                1.0,
                applicability="not-applicable",
                reason_code="manual-mode",
            )
        sample = GatewayDiagnosticSample(
            "decision_state",
            None,
            "inactive",
            0.0,
            0.0,
            1.0,
            applicability="not-applicable",
            reason_code="manual-mode",
        )
        self.assertIsNone(sample.value)

    def test_unobserved_quality_rejects_values_and_requires_failure_reasons(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^error diagnostic must not carry a value$",
        ):
            GatewayDiagnosticSample(
                "operating_mode",
                1,
                "error",
                0.0,
                0.0,
                0.0,
                reason_code="read-failed",
            )
        with self.assertRaisesRegex(
            ValueError,
            "^unavailable diagnostic requires reason_code$",
        ):
            GatewayDiagnosticSample("operating_mode", None, "unavailable", 0.0, 0.0, 0.0)
        with self.assertRaisesRegex(ValueError, "^error diagnostic requires reason_code$"):
            GatewayDiagnosticSample("operating_mode", None, "error", 0.0, 0.0, 0.0)

    def test_unobserved_quality_requires_applicable_and_ordered_timestamps(self) -> None:
        unobserved_statuses: tuple[GatewayDiagnosticStatus, ...] = ("unavailable", "error")
        for status in unobserved_statuses:
            with self.subTest(status=status), self.assertRaisesRegex(
                ValueError,
                f"^{status} diagnostic requires applicability='applicable'$",
            ):
                GatewayDiagnosticSample(
                    "operating_mode",
                    None,
                    status,
                    1.0,
                    1.0,
                    0.0,
                    applicability="not-applicable",
                    reason_code="read-failed",
                )
        quality_cases: tuple[
            tuple[GatewayDiagnosticStatus, GatewayDiagnosticApplicability, str],
            ...,
        ] = (
            ("inactive", "not-applicable", "manual-mode"),
            ("error", "applicable", "read-failed"),
        )
        for status, applicability, reason_code in quality_cases:
            with self.subTest(status=status), self.assertRaisesRegex(
                ValueError,
                "^diagnostic changed_at must not exceed confirmed_at$",
            ):
                GatewayDiagnosticSample(
                    "operating_mode",
                    None,
                    status,
                    2.0,
                    1.0,
                    0.0,
                    applicability=applicability,
                    reason_code=reason_code,
                )

    def test_unknown_quality_requires_unknown_applicability(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^unknown diagnostic requires applicability='unknown'$",
        ):
            GatewayDiagnosticSample("operating_mode", None, "unknown", 0.0, 0.0, 0.0)
        sample = GatewayDiagnosticSample(
            "operating_mode",
            None,
            "unknown",
            0.0,
            0.0,
            0.0,
            applicability="unknown",
        )
        self.assertEqual(sample.status, "unknown")

    def test_sample_mapping_preserves_current_shape_and_migrates_legacy_times(self) -> None:
        current = GatewayDiagnosticSample(
            "operating_mode",
            1,
            "fresh",
            2.0,
            3.0,
            0.75,
        ).to_payload()
        self.assertIs(values_contract._sample_mapping(current), current)
        legacy: dict[str, object] = {
            "name": "operating_mode",
            "value": None,
            "status": "unknown",
            "observed_at": 4.0,
            "confidence": 0.25,
            "reason_code": "",
        }
        self.assertEqual(
            values_contract._sample_mapping(legacy),
            {
                "name": "operating_mode",
                "value": None,
                "status": "unknown",
                "changed_at": 4.0,
                "confirmed_at": 4.0,
                "confidence": 0.25,
                "applicability": "unknown",
                "reason_code": "",
            },
        )
        legacy["status"] = "fresh"
        self.assertEqual(
            values_contract._sample_mapping(legacy)["applicability"],
            "applicable",
        )
        legacy["status"] = "inactive"
        self.assertEqual(
            values_contract._sample_mapping(legacy)["applicability"],
            "not-applicable",
        )
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostic sample fields do not match the schema$",
        ):
            values_contract._sample_mapping({})


if __name__ == "__main__":
    unittest.main()
