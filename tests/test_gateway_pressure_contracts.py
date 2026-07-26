# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from venus_evcharger.ipc.gateway_pressure import (
    CachedGatewayPressurePolicy,
    normalized_gateway_pressure_state,
    read_gateway_pressure_snapshot,
    service_gateway_pressure_policy,
)
from venus_evcharger.ports.gateway_pressure import GatewayPressurePolicy, GatewayPressureSnapshot


def _write_payload(path: str, payload: object) -> None:
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


class _SlottedService:
    __slots__ = ("gateway_health_path",)

    def __init__(self, path: str) -> None:
        self.gateway_health_path = path


class GatewayPressureContractTests(unittest.TestCase):
    def test_state_normalization_exposes_only_transport_neutral_states(self) -> None:
        cases = {
            " OK ": "ok",
            "congested": "congested",
            "slow": "slow",
            "protective": "protective",
            "degraded": "slow",
            "other": "unknown",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalized_gateway_pressure_state(raw), expected)
        self.assertEqual(normalized_gateway_pressure_state(None), "unknown")

    def test_snapshot_reader_owns_json_shape_priority_and_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/gateway-health.json"
            _write_payload(
                path,
                {
                    "captured_at": 90.0,
                    "dbus_health": {
                        "backpressure": {"state": "congested"},
                        "state": "protective",
                        "resources": {"state": "constrained"},
                    },
                },
            )
            fresh = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual(
                fresh,
                GatewayPressureSnapshot("congested", 90.0, 10.0, False, "backpressure"),
            )

            stale = read_gateway_pressure_snapshot(path, now=100.001, max_age_seconds=10.0)
            self.assertEqual(stale.state, "slow")
            self.assertTrue(stale.stale)
            self.assertEqual(stale.source, "stale-health")

    def test_reader_falls_back_to_gateway_and_resource_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/gateway-health.json"
            _write_payload(path, {"captured_at": 100.0, "state": "degraded"})
            direct = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual((direct.state, direct.source), ("slow", "gateway-health"))

            _write_payload(path, {"captured_at": 100.0, "resources": {"state": "busy"}})
            resources = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual((resources.state, resources.source), ("congested", "resources"))

            _write_payload(path, {"captured_at": 100.0, "resources": {"state": 1}})
            invalid_resource = read_gateway_pressure_snapshot(
                path,
                now=100.0,
                max_age_seconds=10.0,
            )
            self.assertEqual(
                (invalid_resource.state, invalid_resource.source),
                ("slow", "missing-state"),
            )

    def test_reader_is_conservative_for_missing_or_invalid_documents(self) -> None:
        missing_path = read_gateway_pressure_snapshot("", now=100.0, max_age_seconds=10.0)
        self.assertEqual(
            missing_path,
            GatewayPressureSnapshot("slow", 0.0, 0.0, True, "missing-path"),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/gateway-health.json"
            missing = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual(
                missing,
                GatewayPressureSnapshot("slow", 0.0, 0.0, True, "missing-health"),
            )
            Path(path).write_text("not-json", encoding="utf-8")
            invalid = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual((invalid.state, invalid.source), ("slow", "missing-health"))
            _write_payload(path, ["not", "an", "object"])
            non_object = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual((non_object.state, non_object.source), ("slow", "missing-health"))

            _write_payload(path, {"captured_at": "not-a-number", "backpressure": {"state": "ok"}})
            invalid_time = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual(
                invalid_time,
                GatewayPressureSnapshot(
                    "slow",
                    0.0,
                    0.0,
                    True,
                    "missing-timestamp",
                ),
            )

            _write_payload(
                path,
                {"captured_at": 0.5, "backpressure": {"state": "ok"}},
            )
            early = read_gateway_pressure_snapshot(
                path,
                now=1.0,
                max_age_seconds=10.0,
            )
            self.assertEqual(
                early,
                GatewayPressureSnapshot("ok", 0.5, 0.5, False, "backpressure"),
            )

            _write_payload(path, {"captured_at": 100.001, "backpressure": {"state": "ok"}})
            future = read_gateway_pressure_snapshot(path, now=100.0, max_age_seconds=10.0)
            self.assertEqual(
                future,
                GatewayPressureSnapshot("slow", 100.001, 0.0, True, "future-health"),
            )

            _write_payload(
                path,
                {"captured_at": 80.0, "backpressure": {"state": "ok"}},
            )
            stale = read_gateway_pressure_snapshot(
                path,
                now=100.0,
                max_age_seconds=10.0,
            )
            self.assertEqual(
                stale,
                GatewayPressureSnapshot("slow", 80.0, 20.0, True, "stale-health"),
            )

    def test_policy_caches_snapshots_and_applies_explicit_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/gateway-health.json"
            current = [100.0]
            _write_payload(path, {"captured_at": 100.0, "backpressure": {"state": "protective"}})
            policy = CachedGatewayPressurePolicy(path, now=lambda: current[0], cache_seconds=2.0)
            self.assertIsInstance(policy, GatewayPressurePolicy)
            self.assertTrue(policy.should_throttle_optional_work())
            self.assertEqual(policy.publish_interval_seconds(2.0, group="live-measurements"), 10.0)
            self.assertEqual(policy.publish_interval_seconds(2.0, group="diagnostics"), 24.0)
            self.assertEqual(policy.audit_repeat_seconds(2.0), 16.0)
            self.assertEqual(policy.audit_cleanup_interval_seconds(2.0), 16.0)
            self.assertEqual(policy.optional_work_interval_seconds(2.0), 24.0)
            self.assertEqual(policy.liveness_timeout_seconds(2.0), 10.0)
            self.assertTrue(policy._cache_fresh(100.0))
            self.assertFalse(policy._cache_fresh(99.999))

            _write_payload(path, {"captured_at": 101.0, "backpressure": {"state": "ok"}})
            current[0] = 101.9
            self.assertEqual(policy.state(), "protective")
            current[0] = 102.0
            self.assertEqual(policy.state(), "ok")
            self.assertFalse(policy.should_throttle_optional_work())

    def test_service_resolver_prefers_injection_then_caches_file_policy(self) -> None:
        injected = CachedGatewayPressurePolicy("")
        composed = type("Composed", (), {"gateway_pressure_policy": injected})()
        self.assertIs(service_gateway_pressure_policy(composed), injected)

        service = type("Service", (), {"gateway_health_path": " /tmp/health.json "})()
        first = service_gateway_pressure_policy(service)
        second = service_gateway_pressure_policy(service)
        self.assertIs(first, second)
        self.assertIs(service._gateway_pressure_policy, first)
        self.assertIsInstance(first, CachedGatewayPressurePolicy)
        assert isinstance(first, CachedGatewayPressurePolicy)
        self.assertEqual(first.health_path, "/tmp/health.json")

        slotted = _SlottedService("/tmp/slotted-health.json")
        uncached = service_gateway_pressure_policy(slotted)
        self.assertIsInstance(uncached, CachedGatewayPressurePolicy)
        assert isinstance(uncached, CachedGatewayPressurePolicy)
        self.assertEqual(uncached.health_path, "/tmp/slotted-health.json")

if __name__ == "__main__":
    unittest.main()
