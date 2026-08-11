# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact aggregate and migration contracts for gateway diagnostics snapshots."""

from __future__ import annotations

import unittest
from dataclasses import replace
from tests.gateway_diagnostics_fixtures import (
    diagnostic_samples,
    gateway_diagnostics_snapshot,
)
from venus_evcharger.ports import gateway_diagnostics as diagnostics_contract


class GatewayDiagnosticsSnapshotContractsTests(unittest.TestCase):
    def test_sample_inventory_rejects_duplicates_and_missing_fields(self) -> None:
        samples = diagnostic_samples()
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics ev_charger contains duplicate fields$",
        ):
            diagnostics_contract._validate_samples((samples[0], *samples))
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics ev_charger must contain every semantic field exactly once$",
        ):
            diagnostics_contract._validate_samples(samples[:-1])

    def test_sample_container_parsers_enforce_tuple_and_array_boundaries(self) -> None:
        samples = diagnostic_samples()
        self.assertIs(diagnostics_contract._sample_tuple(samples), samples)
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics ev_charger contains an invalid sample$",
        ):
            diagnostics_contract._sample_tuple((object(),))
        payloads = [sample.to_payload() for sample in samples]
        self.assertEqual(diagnostics_contract._samples(payloads), samples)
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics ev_charger must be an array$",
        ):
            diagnostics_contract._samples("not-an-array")

    def test_schema_version_parser_accepts_only_the_current_domain(self) -> None:
        self.assertEqual(diagnostics_contract._schema_version(3), 3)
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics schema_version must be an integer$",
        ):
            diagnostics_contract._schema_version(True)
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics has an unsupported schema_version$",
        ):
            diagnostics_contract._schema_version(1)

    def test_summary_type_guards_preserve_exact_aggregate_types(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        self.assertIs(diagnostics_contract._health_summary(snapshot.health), snapshot.health)
        self.assertIs(
            diagnostics_contract._discovery_summary(snapshot.discovery),
            snapshot.discovery,
        )
        self.assertIs(
            diagnostics_contract._publication_summary(snapshot.publication),
            snapshot.publication,
        )
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics health must be GatewayHealthSummary$",
        ):
            diagnostics_contract._health_summary(object())
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics discovery must be GatewayDiscoverySummary$",
        ):
            diagnostics_contract._discovery_summary(object())
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics publication must be GatewayPublicationSummary$",
        ):
            diagnostics_contract._publication_summary(object())

    def test_snapshot_mapping_preserves_current_document_identity(self) -> None:
        current = gateway_diagnostics_snapshot().to_payload()
        self.assertIs(diagnostics_contract._snapshot_mapping(current), current)
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics snapshot must be an object with string keys$",
        ):
            diagnostics_contract._snapshot_mapping([])
        incomplete = dict(current)
        incomplete.pop("publication")
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics snapshot fields do not match the schema$",
        ):
            diagnostics_contract._snapshot_mapping(incomplete)

    def test_prior_volatile_schemas_are_rejected_without_clock_inference(self) -> None:
        payload = gateway_diagnostics_snapshot().to_payload()
        for prior_version in (1, 2):
            with self.subTest(prior_version=prior_version), self.assertRaisesRegex(
                ValueError,
                "^gateway diagnostics has an unsupported schema_version$",
            ):
                diagnostics_contract.GatewayDiagnosticsSnapshot.from_payload(
                    payload | {"schema_version": prior_version}
                )

    def test_embedded_epoch_timestamps_survive_a_wall_clock_regression(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        sample = replace(
            snapshot.ev_charger[0],
            changed_at=500.0,
            confirmed_at=500.0,
        )
        adjusted = replace(
            snapshot,
            captured_at=50.0,
            health=replace(snapshot.health, last_success_at=500.0),
            publication=replace(snapshot.publication, heartbeat_at=500.0),
            ev_charger=(sample, *snapshot.ev_charger[1:]),
        )

        restored = diagnostics_contract.GatewayDiagnosticsSnapshot.from_payload(
            adjusted.to_payload()
        )

        self.assertEqual(restored.captured_at, 50.0)
        self.assertEqual(restored.health.last_success_at, 500.0)
        self.assertEqual(restored.publication.heartbeat_at, 500.0)
        self.assertEqual(restored.ev_charger[0].changed_at, 500.0)
        self.assertTrue(restored.is_fresh(100.0, 0.0))

if __name__ == "__main__":
    unittest.main()
