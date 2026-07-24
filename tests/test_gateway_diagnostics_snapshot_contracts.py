# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact aggregate and migration contracts for gateway diagnostics snapshots."""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import TypeGuard

from tests.gateway_diagnostics_fixtures import (
    diagnostic_samples,
    gateway_diagnostics_legacy_payload,
    gateway_diagnostics_snapshot,
)
from venus_evcharger.ports import gateway_diagnostics as diagnostics_contract
from venus_evcharger.ports.gateway_diagnostics_validation import is_string_object_mapping

_LEGACY_NAMES = {
    "schema_version",
    "sequence",
    "captured_at",
    "health",
    "discovery",
    "ev_charger",
}


def _object_list(value: object) -> list[object]:
    if not _is_object_list(value):
        raise AssertionError("expected object list")
    return value


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _object_dict(value: object) -> dict[str, object]:
    if not _is_object_dict(value):
        raise AssertionError("expected string-keyed object dict")
    return value


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return is_string_object_mapping(value) and isinstance(value, dict)


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

    def test_schema_version_parsers_preserve_current_and_legacy_domains(self) -> None:
        self.assertEqual(diagnostics_contract._schema_version(2), 2)
        self.assertEqual(diagnostics_contract._legacy_schema_version(1), 1)
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
        with self.assertRaisesRegex(
            TypeError,
            "^gateway diagnostics schema_version must be an integer$",
        ):
            diagnostics_contract._legacy_schema_version(False)

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

    def test_legacy_migration_derives_publication_and_discovery_defaults(self) -> None:
        legacy = gateway_diagnostics_legacy_payload()
        migrated = diagnostics_contract._migrate_legacy_snapshot(legacy, _LEGACY_NAMES)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(
            migrated["publication"],
            {
                "registered": True,
                "heartbeat_at": 99.0,
                "stale": False,
            },
        )
        expected_discovery: dict[str, object] = {
            "enabled": True,
            "state": "idle",
            "pending_work": 0,
            "discovered_source_count": 4,
            "unusable_source_count": 1,
            "dormant_source_count": 0,
            "sources": [],
        }
        self.assertEqual(migrated["discovery"], expected_discovery)

    def test_legacy_migration_handles_an_empty_sample_stream_without_heartbeat(self) -> None:
        legacy = gateway_diagnostics_legacy_payload()
        legacy["ev_charger"] = []
        migrated = diagnostics_contract._migrate_legacy_snapshot(legacy, _LEGACY_NAMES)
        self.assertEqual(
            migrated["publication"],
            {
                "registered": False,
                "heartbeat_at": 0.0,
                "stale": True,
            },
        )

    def test_legacy_migration_marks_an_old_positive_heartbeat_stale(self) -> None:
        legacy = gateway_diagnostics_legacy_payload(observed_at=0.5)
        migrated = diagnostics_contract._migrate_legacy_snapshot(legacy, _LEGACY_NAMES)
        self.assertEqual(
            migrated["publication"],
            {
                "registered": True,
                "heartbeat_at": 0.5,
                "stale": True,
            },
        )

    def test_legacy_migration_preserves_inactive_applicability(self) -> None:
        legacy = gateway_diagnostics_legacy_payload()
        samples = _object_list(legacy["ev_charger"])
        inactive = _object_dict(samples[0])
        inactive["status"] = "inactive"
        inactive["reason_code"] = "manual-mode"
        migrated = diagnostics_contract.GatewayDiagnosticsSnapshot.from_payload(legacy)
        self.assertEqual(migrated.sample("operating_mode").status, "inactive")
        self.assertEqual(
            migrated.sample("operating_mode").applicability,
            "not-applicable",
        )

    def test_legacy_migration_uses_health_and_sample_freshness(self) -> None:
        health_stale = gateway_diagnostics_legacy_payload()
        health = _object_dict(health_stale["health"])
        health["stale"] = True
        migrated_health = diagnostics_contract._migrate_legacy_snapshot(
            health_stale,
            _LEGACY_NAMES,
        )
        health_publication = _object_dict(migrated_health["publication"])
        self.assertEqual(health_publication["stale"], True)

        sample_stale = gateway_diagnostics_legacy_payload()
        samples = _object_list(sample_stale["ev_charger"])
        sample = _object_dict(samples[0])
        sample["status"] = "stale"
        migrated_sample = diagnostics_contract._migrate_legacy_snapshot(
            sample_stale,
            _LEGACY_NAMES,
        )
        sample_publication = _object_dict(migrated_sample["publication"])
        self.assertEqual(sample_publication["stale"], True)

    def test_snapshot_rejects_embedded_timestamps_beyond_future_tolerance(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        with self.assertRaisesRegex(
            ValueError,
            "^gateway health last_success_at exceeds gateway diagnostics captured_at tolerance$",
        ):
            replace(
                snapshot,
                health=replace(snapshot.health, last_success_at=101.001),
            )
        with self.assertRaisesRegex(
            ValueError,
            "^gateway publication heartbeat_at exceeds gateway diagnostics captured_at tolerance$",
        ):
            replace(
                snapshot,
                publication=replace(snapshot.publication, heartbeat_at=101.001),
            )
        changed = replace(snapshot.ev_charger[0], changed_at=101.001, confirmed_at=101.001)
        with self.assertRaisesRegex(
            ValueError,
            "^operating_mode diagnostic changed_at exceeds gateway diagnostics captured_at tolerance$",
        ):
            replace(snapshot, ev_charger=(changed, *snapshot.ev_charger[1:]))
        confirmed = replace(snapshot.ev_charger[0], confirmed_at=101.001)
        with self.assertRaisesRegex(
            ValueError,
            "^operating_mode diagnostic confirmed_at exceeds gateway diagnostics captured_at tolerance$",
        ):
            replace(snapshot, ev_charger=(confirmed, *snapshot.ev_charger[1:]))

    def test_snapshot_accepts_the_exact_future_tolerance_boundary(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        sample = replace(snapshot.ev_charger[0], changed_at=101.0, confirmed_at=101.0)
        bounded = replace(
            snapshot,
            health=replace(snapshot.health, last_success_at=101.0),
            publication=replace(snapshot.publication, heartbeat_at=101.0),
            ev_charger=(sample, *snapshot.ev_charger[1:]),
        )
        self.assertEqual(bounded.health.last_success_at, 101.0)

    def test_legacy_migration_rejects_non_v1_documents_exactly(self) -> None:
        legacy = gateway_diagnostics_legacy_payload()
        legacy["schema_version"] = 0
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics has an unsupported schema_version$",
        ):
            diagnostics_contract._migrate_legacy_snapshot(legacy, _LEGACY_NAMES)

    def test_legacy_migration_rejects_malformed_shapes_exactly(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^gateway diagnostics snapshot fields do not match the schema$",
        ):
            diagnostics_contract._migrate_legacy_snapshot({}, _LEGACY_NAMES)


if __name__ == "__main__":
    unittest.main()
