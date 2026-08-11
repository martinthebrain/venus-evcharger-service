# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict contracts for the transport-neutral gateway diagnostics boundary."""

from __future__ import annotations

import ast
import json
import math
import tempfile
import unittest
from pathlib import Path

from tests.gateway_diagnostics_fixtures import diagnostic_samples, gateway_diagnostics_snapshot
from venus_evcharger.ipc.gateway_diagnostics import (
    DEFAULT_GATEWAY_DIAGNOSTICS_PATH,
    GatewayDiagnosticsFileReader,
    decode_gateway_diagnostics,
    encode_gateway_diagnostics,
    gateway_diagnostics_payload,
    gateway_diagnostics_path,
    semantic_gateway_diagnostics_document,
)
from venus_evcharger.ports.gateway_diagnostic_discovery import (
    GatewayDiscoverySummary,
    GatewaySourceSummary,
)
from venus_evcharger.ports.gateway_diagnostic_health import (
    GatewayHealthSummary,
    GatewayPublicationSummary,
)
from venus_evcharger.ports.gateway_diagnostic_values import (
    DiagnosticScalar,
    GatewayDiagnosticFieldName,
    GatewayDiagnosticSample,
)
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsReader,
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)
from venus_evcharger.ports import gateway_diagnostics_validation as diagnostics_validation
from venus_evcharger.ports import gateway_diagnostics as diagnostics_contract

_FORBIDDEN_MODULE_PREFIXES = (
    "dbus",
    "vedbus",
    "venus_evcharger.dbus_adapter",
    "venus_evcharger.dbus_gateway",
    "venus_evcharger.dbus_introspection",
)
_FORBIDDEN_SYMBOLS = {
    "DbusCacheStore",
    "dbus_path_key",
    "gateway_paths",
    "load_introspection_snapshot",
    "read_dbus_paths",
}
_FORBIDDEN_LITERALS = {
    "/Ac/Power",
    "/Mode",
    "/StartStop",
    "com.victronenergy",
    "queue_depth",
    "worker_state",
}


def _node_imported_modules(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return {node.module}
    return set()


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        modules.update(_node_imported_modules(node))
    return modules


def _node_used_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.ImportFrom):
        return {alias.name for alias in node.names}
    return set()


def _used_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        names.update(_node_used_names(node))
    return names


def _has_forbidden_import(modules: set[str]) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in modules
        for prefix in _FORBIDDEN_MODULE_PREFIXES
    )


class _Reader:
    def __init__(self, snapshot: GatewayDiagnosticsSnapshot) -> None:
        self.snapshot = snapshot

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
        return self.snapshot


class GatewayDiagnosticsContractsTests(unittest.TestCase):
    def test_component_contracts_are_owned_by_responsibility_modules(self) -> None:
        for old_aggregate_symbol in (
            "GatewayDiagnosticSample",
            "GatewayDiscoverySummary",
            "GatewayHealthSummary",
            "GatewayPublicationSummary",
            "GatewaySourceSummary",
        ):
            with self.subTest(symbol=old_aggregate_symbol):
                self.assertFalse(hasattr(diagnostics_contract, old_aggregate_symbol))

    def test_diagnostics_path_follows_gateway_runtime_directory(self) -> None:
        self.assertEqual(gateway_diagnostics_path(), DEFAULT_GATEWAY_DIAGNOSTICS_PATH)
        self.assertEqual(gateway_diagnostics_path(""), DEFAULT_GATEWAY_DIAGNOSTICS_PATH)
        self.assertEqual(gateway_diagnostics_path("   "), DEFAULT_GATEWAY_DIAGNOSTICS_PATH)
        self.assertEqual(
            gateway_diagnostics_path(" /tmp/gateway-runtime "),
            "/tmp/gateway-runtime/gateway-diagnostics.json",
        )

    def test_consumers_do_not_depend_on_raw_gateway_or_dbus_shapes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        consumers = (
            root / "venus_evcharger/ops/forensic_observer.py",
            root / "venus_evcharger/backend/probe.py",
            root / "venus_evcharger/publish/gateway_diagnostics.py",
            root / "venus_evcharger/publish/dbus_diagnostics.py",
            root / "scripts/ops/gateway_cache_read.py",
        )

        for path in consumers:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
                self.assertFalse(_has_forbidden_import(_imported_modules(tree)))
                self.assertTrue(_FORBIDDEN_SYMBOLS.isdisjoint(_used_names(tree)))
                self.assertFalse(any(literal in source for literal in _FORBIDDEN_LITERALS))

    def test_snapshot_round_trip_and_reader_protocol(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        payload = gateway_diagnostics_payload(snapshot)
        self.assertEqual(decode_gateway_diagnostics(payload), snapshot)
        self.assertEqual(semantic_gateway_diagnostics_document(payload), payload)
        self.assertEqual(json.loads(encode_gateway_diagnostics(snapshot)), payload)
        self.assertIsInstance(_Reader(snapshot), GatewayDiagnosticsReader)
        with self.assertRaisesRegex(TypeError, "snapshot must"):
            gateway_diagnostics_payload(object())

    def test_file_reader_accepts_only_valid_semantic_documents(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostics.json"
            path.write_text(encode_gateway_diagnostics(snapshot), encoding="utf-8")
            self.assertEqual(GatewayDiagnosticsFileReader(str(path)).read_snapshot(), snapshot)

            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(GatewayDiagnosticsUnavailable, "unavailable"):
                GatewayDiagnosticsFileReader(str(path)).read_snapshot()

            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(GatewayDiagnosticsUnavailable):
                GatewayDiagnosticsFileReader(str(path)).read_snapshot()

            with self.assertRaises(GatewayDiagnosticsUnavailable):
                GatewayDiagnosticsFileReader(str(path.with_name("missing"))).read_snapshot()

        invalid_paths = ("", "   ")
        for invalid in invalid_paths:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                GatewayDiagnosticsFileReader(invalid)

    def test_diagnostic_samples_enforce_field_specific_types(self) -> None:
        valid_values: dict[GatewayDiagnosticFieldName, DiagnosticScalar] = {
            "operating_mode": 0,
            "charging_enabled": False,
            "auto_start_enabled": True,
            "ac_power_w": -50.5,
            "charger_state_code": 0,
            "decision_reason": "",
            "decision_state": "idle",
            "last_health_reason": "ok",
            "runtime_overrides_active": False,
            "runtime_overrides_source": "",
        }
        for name, value in valid_values.items():
            sample = GatewayDiagnosticSample(name, value, "fresh", 1.0, 1.5, 0.5)
            self.assertEqual(GatewayDiagnosticSample.from_payload(sample.to_payload()), sample)

        invalid_values: tuple[tuple[object, object], ...] = (
            ("operating_mode", 3),
            ("operating_mode", True),
            ("charger_state_code", -1),
            ("charging_enabled", 1),
            ("ac_power_w", True),
            ("ac_power_w", math.inf),
            ("decision_state", 1),
            ("not-a-field", 1),
        )
        valid_payload = GatewayDiagnosticSample(
            "operating_mode",
            0,
            "fresh",
            1.0,
            1.5,
            1.0,
        ).to_payload()
        for invalid_name, invalid_value in invalid_values:
            with self.subTest(
                name=invalid_name,
                value=invalid_value,
            ), self.assertRaises((TypeError, ValueError)):
                GatewayDiagnosticSample.from_payload(
                    valid_payload
                    | {
                        "name": invalid_name,
                        "value": invalid_value,
                    }
                )

    def test_diagnostic_quality_state_invariants_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a value"):
            GatewayDiagnosticSample("operating_mode", None, "fresh", 1.0, 1.0, 1.0)
        with self.assertRaisesRegex(ValueError, "positive timestamps"):
            GatewayDiagnosticSample("operating_mode", 1, "stale", 0.0, 1.0, 1.0)
        with self.assertRaisesRegex(ValueError, "must not carry"):
            GatewayDiagnosticSample(
                "operating_mode",
                1,
                "error",
                1.0,
                1.0,
                0.0,
                reason_code="read-failed",
            )
        with self.assertRaisesRegex(ValueError, "requires reason_code"):
            GatewayDiagnosticSample("operating_mode", None, "unavailable", 0.0, 0.0, 0.0)
        with self.assertRaisesRegex(ValueError, "requires reason_code"):
            GatewayDiagnosticSample("operating_mode", None, "error", 0.0, 0.0, 0.0)

        unknown = GatewayDiagnosticSample(
            "operating_mode",
            None,
            "unknown",
            0.0,
            0.0,
            0.0,
            applicability="unknown",
        )
        self.assertEqual(GatewayDiagnosticSample.from_payload(unknown.to_payload()), unknown)
        for replacement in (
            {"status": "invalid"},
            {"changed_at": -1.0},
            {"confirmed_at": -1.0},
            {"confidence": 1.1},
            {"confidence": math.nan},
            {"applicability": "invalid"},
            {"reason_code": 1},
        ):
            payload = unknown.to_payload() | replacement
            with self.subTest(replacement=replacement), self.assertRaises((TypeError, ValueError)):
                GatewayDiagnosticSample.from_payload(payload)

    def test_health_summary_rejects_inconsistent_or_malformed_metrics(self) -> None:
        health = gateway_diagnostics_snapshot().health
        self.assertEqual(GatewayHealthSummary.from_payload(health.to_payload()), health)
        base = health.to_payload()
        invalid = (
            {"state": "bad"},
            {"stale": 1},
            {"timeouts_60s": -1},
            {"timeouts_60s": True},
            {"average_latency_ms": -1.0},
            {"maximum_latency_ms": 3.0},
            {"pending_gateway_commands": -1},
            {"pending_core_commands": -1},
            {"maximum_event_loop_gap_ms_60s": -1.0},
            {"last_success_at": -1.0},
            {"last_error_code": 1},
        )
        for replacement in invalid:
            with self.subTest(replacement=replacement), self.assertRaises((TypeError, ValueError)):
                GatewayHealthSummary.from_payload(base | replacement)

    def test_discovery_summary_enforces_bounded_semantics(self) -> None:
        discovery = gateway_diagnostics_snapshot().discovery
        self.assertEqual(GatewayDiscoverySummary.from_payload(discovery.to_payload()), discovery)
        base = discovery.to_payload()
        invalid = (
            {"enabled": 1},
            {"state": "bad"},
            {"pending_work": -1},
            {"discovered_source_count": -1},
            {"unusable_source_count": -1},
            {"discovered_source_count": 1, "unusable_source_count": 2},
            {"enabled": False, "state": "idle"},
            {"enabled": True, "state": "disabled"},
        )
        for replacement in invalid:
            with self.subTest(replacement=replacement), self.assertRaises((TypeError, ValueError)):
                GatewayDiscoverySummary.from_payload(base | replacement)
        disabled = GatewayDiscoverySummary(False, "disabled", 0, 0, 0)
        self.assertEqual(GatewayDiscoverySummary.from_payload(disabled.to_payload()), disabled)

    def test_source_and_publication_summaries_keep_expected_dormancy_explicit(self) -> None:
        sources = (
            GatewaySourceSummary("pv-ac", "pv_ac", "dormant", "optional-pv-not-advertising"),
            GatewaySourceSummary("grid", "grid", "available"),
        )
        discovery = GatewayDiscoverySummary(
            True,
            "idle",
            0,
            2,
            0,
            dormant_source_count=1,
            sources=sources,
        )
        self.assertEqual(GatewayDiscoverySummary.from_payload(discovery.to_payload()), discovery)
        publication = GatewayPublicationSummary(True, 10.0, False)
        self.assertEqual(
            GatewayPublicationSummary.from_payload(publication.to_payload()),
            publication,
        )
        with self.assertRaisesRegex(ValueError, "availability counts"):
            GatewayDiscoverySummary(
                True,
                "idle",
                0,
                2,
                1,
                dormant_source_count=1,
                sources=sources,
            )
        with self.assertRaisesRegex(ValueError, "positive heartbeat"):
            GatewayPublicationSummary(True, 0.0, False)
        with self.assertRaisesRegex(ValueError, "heartbeat_at=0"):
            GatewayPublicationSummary(False, 1.0, False)
        with self.assertRaisesRegex(ValueError, "requires reason_code"):
            GatewaySourceSummary("pv-ac", "pv_ac", "dormant")
        with self.assertRaisesRegex(ValueError, "kind is invalid"):
            GatewaySourceSummary.from_payload(
                {
                    "source_id": "pv-ac",
                    "kind": "wind",
                    "availability": "available",
                    "reason_code": "",
                }
            )

    def test_snapshot_requires_complete_unique_typed_semantic_fields(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        self.assertEqual(snapshot.sample("operating_mode").value, 2)
        self.assertEqual(snapshot.critical_unavailable_fields(), ())
        with self.assertRaisesRegex(
            ValueError,
            "current monotonic time precedes captured_monotonic",
        ):
            snapshot.age_seconds(99.0)
        self.assertEqual(snapshot.age_seconds(100.0), 0.0)
        self.assertEqual(snapshot.age_seconds(125.0), 25.0)
        self.assertTrue(snapshot.is_fresh(100.0, 0.0))
        with self.assertRaisesRegex(ValueError, "precedes captured_monotonic"):
            snapshot.is_fresh(99.0, 100.0)
        self.assertTrue(snapshot.is_fresh(125.0, 25.0))
        self.assertFalse(snapshot.is_fresh(125.1, 25.0))

        unavailable = gateway_diagnostics_snapshot(
            status_overrides={"operating_mode": "error", "ac_power_w": "unknown"}
        )
        self.assertEqual(unavailable.critical_unavailable_fields(), ("operating_mode", "ac_power_w"))

        samples = diagnostic_samples()
        sample_payloads = [sample.to_payload() for sample in samples]
        invalid_snapshots: tuple[dict[str, object], ...] = (
            {"sequence": -1},
            {"captured_at": 0.0},
            {"health": object()},
            {"discovery": object()},
            {"ev_charger": sample_payloads[:-1]},
            {"ev_charger": sample_payloads[:-1] + [sample_payloads[0]]},
            {"ev_charger": sample_payloads[:-1] + [object()]},
        )
        payload = snapshot.to_payload()
        for replacement in invalid_snapshots:
            with self.subTest(replacement=replacement), self.assertRaises((TypeError, ValueError)):
                GatewayDiagnosticsSnapshot.from_payload(payload | replacement)
        with self.assertRaisesRegex(ValueError, "unsupported schema_version"):
            GatewayDiagnosticsSnapshot(
                sequence=snapshot.sequence,
                captured_at=snapshot.captured_at,
                captured_monotonic=snapshot.captured_monotonic,
                health=snapshot.health,
                discovery=snapshot.discovery,
                publication=snapshot.publication,
                ev_charger=snapshot.ev_charger,
                schema_version=4,
            )
        with self.assertRaisesRegex(TypeError, "invalid sample"):
            diagnostics_contract._sample_tuple([*samples])
        with self.assertRaisesRegex(TypeError, "invalid sample"):
            diagnostics_contract._sample_tuple((object(),))
        with self.assertRaisesRegex(ValueError, "duplicate fields"):
            diagnostics_contract._validate_samples(samples[:-1] + (samples[0],))
        with self.assertRaisesRegex(ValueError, "every semantic field"):
            diagnostics_contract._validate_samples(samples[:-1])
        for value, validator, label in (
            (object(), diagnostics_contract._health_summary, "health"),
            (object(), diagnostics_contract._discovery_summary, "discovery"),
            (object(), diagnostics_contract._publication_summary, "publication"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(TypeError, label):
                validator(value)

    def test_epoch_clock_changes_do_not_affect_snapshot_freshness(self) -> None:
        before_epoch_adjustment = gateway_diagnostics_snapshot(
            captured_at=1_000.0,
            captured_monotonic=100.0,
        )
        after_epoch_adjustment = gateway_diagnostics_snapshot(
            captured_at=10.0,
            captured_monotonic=100.0,
        )

        self.assertEqual(before_epoch_adjustment.age_seconds(104.0), 4.0)
        self.assertEqual(after_epoch_adjustment.age_seconds(104.0), 4.0)
        self.assertTrue(before_epoch_adjustment.is_fresh(104.0, 4.0))
        self.assertTrue(after_epoch_adjustment.is_fresh(104.0, 4.0))

    def test_payload_schema_is_exact_at_every_boundary(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        payload = snapshot.to_payload()
        malformed = (
            payload | {"extra": True},
            {key: value for key, value in payload.items() if key != "health"},
            payload | {"schema_version": True},
            payload | {"schema_version": 2},
            payload | {"sequence": 1.5},
            payload | {"ev_charger": "bad"},
            payload | {"ev_charger": object()},
            [("schema_version", 1)],
            {1: "bad"},
        )
        for item in malformed:
            with self.subTest(item=item), self.assertRaises((TypeError, ValueError)):
                GatewayDiagnosticsSnapshot.from_payload(item)

        sample_payload = snapshot.ev_charger[0].to_payload()
        health_payload = snapshot.health.to_payload()
        discovery_payload = snapshot.discovery.to_payload()
        publication_payload = snapshot.publication.to_payload()
        for item, decoder in (
            (sample_payload | {"extra": True}, GatewayDiagnosticSample.from_payload),
            (health_payload | {"extra": True}, GatewayHealthSummary.from_payload),
            (discovery_payload | {"extra": True}, GatewayDiscoverySummary.from_payload),
            (
                publication_payload | {"extra": True},
                GatewayPublicationSummary.from_payload,
            ),
        ):
            with self.assertRaises(ValueError):
                decoder(item)
        with self.assertRaisesRegex(ValueError, "non-empty text"):
            diagnostics_validation.text("", "required")

    def test_time_bounds_reject_non_numeric_non_finite_and_negative_values(self) -> None:
        snapshot = gateway_diagnostics_snapshot()
        invalid_times = (math.inf, -1.0)
        for now in invalid_times:
            with self.subTest(now=now), self.assertRaises((TypeError, ValueError)):
                snapshot.age_seconds(now)
        for maximum in invalid_times:
            with self.subTest(maximum=maximum), self.assertRaises((TypeError, ValueError)):
                snapshot.is_fresh(100.0, maximum)
        for value in (True, "now"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                diagnostics_validation.finite_float(value, "diagnostic time")

if __name__ == "__main__":
    unittest.main()
