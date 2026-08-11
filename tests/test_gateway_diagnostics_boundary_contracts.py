# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for semantic gateway diagnostics."""

from __future__ import annotations

import unittest
from collections.abc import Callable

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.ports import gateway_diagnostic_discovery as discovery_contract
from venus_evcharger.ports import gateway_diagnostic_health as health_contract
from venus_evcharger.ports import gateway_diagnostic_values as values_contract
from venus_evcharger.ports import gateway_diagnostics_validation as diagnostics_validation
from venus_evcharger.ports.gateway_diagnostic_discovery import (
    GatewayDiscoverySummary,
    GatewaySourceSummary,
)
from venus_evcharger.ports.gateway_diagnostic_values import GatewayDiagnosticSample
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsSnapshot


def _assert_invalid_values(
    test_case: unittest.TestCase,
    parser: Callable[[object], object],
) -> None:
    for invalid in ("not-in-schema", 1):
        with test_case.subTest(parser=parser, invalid=invalid), test_case.assertRaises(ValueError):
            parser(invalid)


class GatewayDiagnosticsBoundaryContractsTests(unittest.TestCase):
    def test_text_validation_preserves_content_and_empty_policy(self) -> None:
        self.assertEqual(diagnostics_validation.text(" value ", "field"), " value ")
        self.assertEqual(
            diagnostics_validation.text("", "field", allow_empty=True),
            "",
        )
        with self.assertRaisesRegex(ValueError, "^field must be text$"):
            diagnostics_validation.text(1, "field")
        with self.assertRaisesRegex(ValueError, "^field must be non-empty text$"):
            diagnostics_validation.text("", "field")
        with self.assertRaisesRegex(ValueError, "^field must be non-empty text$"):
            diagnostics_validation.text(" \t", "field")

    def test_boolean_validation_rejects_integer_lookalikes(self) -> None:
        self.assertIs(diagnostics_validation.boolean(True, "enabled"), True)
        self.assertIs(diagnostics_validation.boolean(False, "enabled"), False)
        with self.assertRaisesRegex(TypeError, "^enabled must be a boolean$"):
            diagnostics_validation.boolean(1, "enabled")

    def test_non_negative_integer_validation_enforces_type_and_zero_boundary(self) -> None:
        self.assertEqual(diagnostics_validation.non_negative_int(0, "count"), 0)
        self.assertEqual(diagnostics_validation.non_negative_int(3, "count"), 3)
        with self.assertRaisesRegex(TypeError, "^count must be an integer$"):
            diagnostics_validation.non_negative_int(True, "count")
        with self.assertRaisesRegex(TypeError, "^count must be an integer$"):
            diagnostics_validation.non_negative_int(1.0, "count")
        with self.assertRaisesRegex(ValueError, "^count must be non-negative$"):
            diagnostics_validation.non_negative_int(-1, "count")

    def test_finite_float_validation_enforces_numeric_and_finite_values(self) -> None:
        self.assertEqual(diagnostics_validation.finite_float(2, "value"), 2.0)
        self.assertEqual(diagnostics_validation.finite_float(2.5, "value"), 2.5)
        with self.assertRaisesRegex(TypeError, "^value must be numeric$"):
            diagnostics_validation.finite_float(True, "value")
        with self.assertRaisesRegex(TypeError, "^value must be numeric$"):
            diagnostics_validation.finite_float("2", "value")
        with self.assertRaisesRegex(ValueError, "^value must be finite$"):
            diagnostics_validation.finite_float(float("inf"), "value")
        with self.assertRaisesRegex(ValueError, "^value must be finite$"):
            diagnostics_validation.finite_float(float("nan"), "value")

    def test_non_negative_float_validation_enforces_zero_boundary(self) -> None:
        self.assertEqual(diagnostics_validation.non_negative_float(0.0, "age"), 0.0)
        self.assertEqual(diagnostics_validation.non_negative_float(0.5, "age"), 0.5)
        with self.assertRaisesRegex(TypeError, "^age must be numeric$"):
            diagnostics_validation.non_negative_float("0.5", "age")
        with self.assertRaisesRegex(ValueError, "^age must be non-negative$"):
            diagnostics_validation.non_negative_float(-0.1, "age")

    def test_epoch_timestamp_normalization_fails_closed_at_ipc_boundary(self) -> None:
        normalize = diagnostics_validation.normalized_epoch_timestamp
        self.assertEqual(normalize(4, 5.0), 4.0)
        self.assertEqual(normalize(6.0, 5.0), 5.0)
        for invalid in (
            True,
            "4",
            -1.0,
            float("inf"),
            float("-inf"),
            float("nan"),
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(normalize(invalid, 5.0), 0.0)
        self.assertEqual(normalize(4.0, False), 0.0)

    def test_positive_float_validation_rejects_zero_and_negative_values(self) -> None:
        self.assertEqual(diagnostics_validation.positive_float(0.1, "interval"), 0.1)
        with self.assertRaisesRegex(ValueError, "^interval must be finite$"):
            diagnostics_validation.positive_float(float("inf"), "interval")
        with self.assertRaisesRegex(ValueError, "^interval must be positive$"):
            diagnostics_validation.positive_float(0.0, "interval")
        with self.assertRaisesRegex(ValueError, "^interval must be positive$"):
            diagnostics_validation.positive_float(-0.1, "interval")

    def test_bounded_float_validation_accepts_only_closed_interval(self) -> None:
        self.assertEqual(diagnostics_validation.bounded_float(0.0, "ratio", 0.0, 1.0), 0.0)
        self.assertEqual(diagnostics_validation.bounded_float(1.0, "ratio", 0.0, 1.0), 1.0)
        with self.assertRaisesRegex(TypeError, "^ratio must be numeric$"):
            diagnostics_validation.bounded_float(False, "ratio", 0.0, 1.0)
        with self.assertRaisesRegex(
            ValueError,
            "^ratio must be between 0.0 and 1.0$",
        ):
            diagnostics_validation.bounded_float(-0.1, "ratio", 0.0, 1.0)
        with self.assertRaisesRegex(
            ValueError,
            "^ratio must be between 0.0 and 1.0$",
        ):
            diagnostics_validation.bounded_float(1.1, "ratio", 0.0, 1.0)

    def test_discovery_source_inventory_rejects_all_count_mismatches(self) -> None:
        available = GatewaySourceSummary("grid", "grid", "available")
        dormant = GatewaySourceSummary("pv-ac", "pv_ac", "dormant", "sleep-confirmed")
        unavailable = GatewaySourceSummary(
            "battery",
            "battery",
            "unavailable",
            "service-missing",
        )
        with self.assertRaisesRegex(
            ValueError,
            "^gateway discovery dormant_source_count exceeds discovered_source_count$",
        ):
            GatewayDiscoverySummary(True, "idle", 0, 1, 0, 2)
        with self.assertRaisesRegex(
            ValueError,
            "^gateway discovery unavailable and dormant counts exceed discovered sources$",
        ):
            GatewayDiscoverySummary(True, "idle", 0, 2, 2, 1)
        with self.assertRaisesRegex(
            ValueError,
            "^gateway discovery source count does not match sources$",
        ):
            GatewayDiscoverySummary(True, "idle", 0, 2, 0, sources=(available,))
        with self.assertRaisesRegex(
            ValueError,
            "^gateway discovery availability counts do not match sources$",
        ):
            GatewayDiscoverySummary(
                True,
                "idle",
                0,
                3,
                0,
                dormant_source_count=1,
                sources=(available, dormant, unavailable),
            )
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics sources contains duplicate source_id values$",
        ):
            GatewayDiscoverySummary(
                True,
                "idle",
                0,
                2,
                0,
                sources=(available, available),
            )
        invalid_collections: tuple[object, ...] = (list[object](), (object(),))
        for invalid in invalid_collections:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                TypeError,
                "^gateway diagnostics sources contains an invalid source$",
            ):
                discovery_contract._source_tuple(invalid)

    def test_discovery_configuration_rejects_inconsistent_enabled_state(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^disabled gateway discovery requires state='disabled'$",
        ):
            GatewayDiscoverySummary(False, "idle", 0, 0, 0)
        with self.assertRaisesRegex(
            ValueError,
            "^enabled gateway discovery must not report state='disabled'$",
        ):
            GatewayDiscoverySummary(True, "disabled", 0, 0, 0)
        with self.assertRaisesRegex(
            ValueError,
            "^gateway discovery unusable_source_count exceeds discovered_source_count$",
        ):
            GatewayDiscoverySummary(True, "idle", 0, 0, 1)

    def test_discovery_source_counters_distinguish_all_availability_states(self) -> None:
        available = GatewaySourceSummary("grid", "grid", "available")
        dormant = GatewaySourceSummary("pv-ac", "pv_ac", "dormant", "sleep-confirmed")
        unavailable = GatewaySourceSummary("pv-dc", "pv_dc", "unavailable", "service-missing")
        unknown = GatewaySourceSummary("battery", "battery", "unknown", "not-scanned")
        sources = (available, dormant, unavailable, unknown)
        self.assertEqual(discovery_contract._unavailable_source_count(sources), 2)
        self.assertEqual(discovery_contract._dormant_source_count(sources), 1)

    def test_discovery_source_payload_sequence_has_exact_boundary_shape(self) -> None:
        source = GatewaySourceSummary("grid", "grid", "available")
        parsed = discovery_contract._sources([source.to_payload()])
        self.assertEqual(parsed, (source,))
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics sources must be an array$",
        ):
            discovery_contract._sources("not-an-array")

    def test_discovery_mapping_migrates_only_the_legacy_shape(self) -> None:
        current_names = {
            "enabled",
            "state",
            "pending_work",
            "discovered_source_count",
            "unusable_source_count",
            "dormant_source_count",
            "sources",
        }
        current: dict[str, object] = {
            "enabled": True,
            "state": "idle",
            "pending_work": 1,
            "discovered_source_count": 0,
            "unusable_source_count": 0,
            "dormant_source_count": 0,
            "sources": [],
        }
        self.assertIs(discovery_contract._discovery_mapping(current, current_names), current)
        legacy = {
            "enabled": True,
            "state": "idle",
            "pending_work": 1,
            "discovered_source_count": 0,
            "unusable_source_count": 0,
        }
        self.assertEqual(
            discovery_contract._discovery_mapping(legacy, current_names),
            {**legacy, "dormant_source_count": 0, "sources": []},
        )
        with self.assertRaisesRegex(
            ValueError,
            "^gateway discovery summary fields do not match the schema$",
        ):
            discovery_contract._discovery_mapping({}, current_names)

    def test_discovery_literal_parsers_cover_the_complete_vocabulary(self) -> None:
        for state in (
            "unknown",
            "disabled",
            "idle",
            "running",
            "degraded",
            "protective",
            "error",
            "unavailable",
        ):
            self.assertEqual(discovery_contract._discovery_state(state), state)
        for availability in ("available", "dormant", "unavailable", "unknown"):
            self.assertEqual(
                discovery_contract._source_availability(availability),
                availability,
            )
        with self.assertRaisesRegex(ValueError, "^gateway discovery state is invalid$"):
            discovery_contract._discovery_state("not-in-schema")
        with self.assertRaisesRegex(ValueError, "^gateway discovery state is invalid$"):
            discovery_contract._discovery_state(1)
        with self.assertRaisesRegex(ValueError, "^gateway source availability is invalid$"):
            discovery_contract._source_availability("not-in-schema")
        with self.assertRaisesRegex(ValueError, "^gateway source availability is invalid$"):
            discovery_contract._source_availability(1)

    def test_health_literal_parser_covers_the_complete_vocabulary(self) -> None:
        for state in ("unknown", "ok", "degraded", "protective", "unavailable"):
            self.assertEqual(health_contract._health_state(state), state)
        with self.assertRaisesRegex(ValueError, "^gateway health state is invalid$"):
            health_contract._health_state("not-in-schema")
        with self.assertRaisesRegex(ValueError, "^gateway health state is invalid$"):
            health_contract._health_state(1)

    def test_value_quality_parsers_cover_the_complete_vocabularies(self) -> None:
        for status in ("fresh", "stale", "inactive", "unavailable", "error", "unknown"):
            self.assertEqual(values_contract.gateway_diagnostic_status(status), status)
        for applicability in ("applicable", "not-applicable", "unknown"):
            self.assertEqual(
                values_contract.gateway_diagnostic_applicability(applicability),
                applicability,
            )
        _assert_invalid_values(self, values_contract.gateway_diagnostic_status)
        _assert_invalid_values(self, values_contract.gateway_diagnostic_applicability)

    def test_field_parser_covers_the_complete_schema_vocabulary(self) -> None:
        for name in (
            "operating_mode",
            "charging_enabled",
            "auto_start_enabled",
            "ac_power_w",
            "charger_state_code",
            "decision_reason",
            "decision_state",
            "last_health_reason",
            "runtime_overrides_active",
            "runtime_overrides_source",
        ):
            self.assertEqual(values_contract.gateway_diagnostic_field_name(name), name)
        _assert_invalid_values(self, values_contract.gateway_diagnostic_field_name)

    def test_primitive_payload_parsers_reject_ambiguous_container_shapes(self) -> None:
        payload = {"name": "operating_mode"}
        self.assertEqual(
            diagnostics_validation.exact_mapping(
                payload,
                "diagnostic record",
                {"name"},
            ),
            payload,
        )
        self.assertEqual(
            tuple(diagnostics_validation.object_sequence([1, "two"], "diagnostic values")),
            (1, "two"),
        )
        with self.assertRaisesRegex(TypeError, "string keys"):
            diagnostics_validation.exact_mapping({1: "bad"}, "diagnostic record", {"name"})
        with self.assertRaisesRegex(TypeError, "string keys"):
            diagnostics_validation.exact_mapping([], "diagnostic record", {"name"})
        with self.assertRaisesRegex(ValueError, "fields"):
            diagnostics_validation.exact_mapping({}, "diagnostic record", {"name"})
        self.assertEqual(
            diagnostics_validation.member_text("fresh", frozenset({"fresh"}), "status"),
            "fresh",
        )
        invalid_members: tuple[object, ...] = ("stale", 1)
        for invalid in invalid_members:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "invalid"):
                diagnostics_validation.member_text(
                    invalid,
                    frozenset({"fresh"}),
                    "status",
                )
        invalid_sequences: tuple[object, ...] = (
            "text",
            b"bytes",
            bytearray(b"bytes"),
            {"index": 0},
            1,
        )
        for invalid in invalid_sequences:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(TypeError, "array"):
                diagnostics_validation.object_sequence(invalid, "diagnostic values")

    def test_quality_metadata_rejects_every_inconsistent_state_transition(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            GatewayDiagnosticSample("operating_mode", 1, "fresh", 2.0, 1.0, 1.0)
        with self.assertRaisesRegex(ValueError, "applicability='unknown'"):
            GatewayDiagnosticSample("operating_mode", None, "unknown", 0.0, 0.0, 0.0)
        with self.assertRaisesRegex(ValueError, "requires applicability"):
            GatewayDiagnosticSample(
                "operating_mode",
                1,
                "fresh",
                1.0,
                1.0,
                1.0,
                applicability="unknown",
            )
        with self.assertRaisesRegex(ValueError, "requires reason_code"):
            GatewayDiagnosticSample(
                "decision_state",
                None,
                "inactive",
                0.0,
                0.0,
                1.0,
                applicability="not-applicable",
            )
        inactive = GatewayDiagnosticSample(
            "decision_state",
            "idle",
            "inactive",
            1.0,
            2.0,
            1.0,
            applicability="not-applicable",
            reason_code="manual-mode",
        )
        self.assertEqual(inactive.value, "idle")

    def test_snapshot_rejects_prior_schema_and_incomplete_semantic_values(self) -> None:
        payload = gateway_diagnostics_snapshot().to_payload()
        for unsupported in (0, 1, 2):
            with self.subTest(unsupported=unsupported), self.assertRaisesRegex(
                ValueError,
                "unsupported schema_version",
            ):
                GatewayDiagnosticsSnapshot.from_payload(
                    payload | {"schema_version": unsupported}
                )

        with self.assertRaisesRegex(ValueError, "every semantic field"):
            GatewayDiagnosticsSnapshot.from_payload(payload | {"ev_charger": []})


if __name__ == "__main__":
    unittest.main()
