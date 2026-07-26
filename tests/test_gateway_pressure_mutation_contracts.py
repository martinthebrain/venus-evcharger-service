# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from venus_evcharger.ipc import gateway_pressure
from venus_evcharger.ports.gateway_pressure import GatewayPressureSnapshot


class _FloatValue:
    def __init__(self, value: float) -> None:
        self.value = value

    def __float__(self) -> float:
        return self.value


class GatewayPressureMutationContractTests(unittest.TestCase):
    def test_constructor_normalizes_configuration_without_synthetic_cache_entry(self) -> None:
        clock = Mock(return_value=42.5)
        policy = gateway_pressure.CachedGatewayPressurePolicy(
            "  /tmp/gateway-health.json  ",
            now=clock,
            max_age_seconds=-3.0,
            cache_seconds=-2.0,
        )

        self.assertEqual(policy.health_path, "/tmp/gateway-health.json")
        self.assertIs(policy._now, clock)
        self.assertEqual(policy.max_age_seconds, 0.0)
        self.assertEqual(policy.cache_seconds, 0.0)
        self.assertIsNone(policy._cached_at)
        self.assertIsNone(policy._cached_snapshot)

        defaults = gateway_pressure.CachedGatewayPressurePolicy("/tmp/default-health.json")
        self.assertEqual(defaults.max_age_seconds, 10.0)
        self.assertEqual(defaults.cache_seconds, 1.0)
        self.assertEqual(gateway_pressure.CachedGatewayPressurePolicy("").health_path, "")

    def test_snapshot_uses_injected_clock_and_half_open_cache_window(self) -> None:
        current = [10.0]
        first = GatewayPressureSnapshot("ok", 10.0, 0.0, False, "backpressure")
        second = GatewayPressureSnapshot("slow", 12.0, 0.0, False, "backpressure")
        policy = gateway_pressure.CachedGatewayPressurePolicy(
            " /tmp/health.json ",
            now=lambda: current[0],
            max_age_seconds=7.0,
            cache_seconds=2.0,
        )

        with patch.object(
            gateway_pressure,
            "read_gateway_pressure_snapshot",
            side_effect=[first, second],
        ) as read_snapshot:
            self.assertEqual(policy.snapshot(), first)
            read_snapshot.assert_called_once_with(
                "/tmp/health.json",
                now=10.0,
                max_age_seconds=7.0,
            )

            current[0] = 11.999
            self.assertIs(policy.snapshot(), first)
            self.assertEqual(read_snapshot.call_count, 1)

            current[0] = 12.0
            self.assertIs(policy.snapshot(), second)
            self.assertEqual(read_snapshot.call_count, 2)

    def test_snapshot_rereads_after_clock_moves_backwards(self) -> None:
        current = [20.0]
        policy = gateway_pressure.CachedGatewayPressurePolicy(
            "/tmp/health.json",
            now=lambda: current[0],
            cache_seconds=5.0,
        )
        snapshots = [
            GatewayPressureSnapshot("ok", 20.0, 0.0, False, "backpressure"),
            GatewayPressureSnapshot("slow", 19.0, 0.0, False, "backpressure"),
        ]

        with patch.object(
            gateway_pressure,
            "read_gateway_pressure_snapshot",
            side_effect=snapshots,
        ) as read_snapshot:
            self.assertEqual(policy.snapshot().state, "ok")
            current[0] = 19.999
            self.assertEqual(policy.snapshot().state, "slow")
            self.assertEqual(read_snapshot.call_count, 2)

    def test_reader_strips_path_and_clamps_negative_max_age(self) -> None:
        payload = {"captured_at": 1.0, "backpressure": {"state": "ok"}}
        with patch.object(gateway_pressure, "_read_json", return_value=payload) as read_json:
            snapshot = gateway_pressure.read_gateway_pressure_snapshot(
                "  /tmp/health.json  ",
                now=100.0,
                max_age_seconds=-1.0,
            )

        read_json.assert_called_once_with("/tmp/health.json")
        self.assertEqual(
            snapshot,
            GatewayPressureSnapshot("ok", 1.0, 99.0, False, "backpressure"),
        )

    def test_json_reader_requests_utf8_and_conservatively_handles_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "health.json"
            path.write_text(json.dumps({"label": "Größe"}), encoding="utf-8")
            self.assertEqual(gateway_pressure._read_json(str(path)), {"label": "Größe"})

        with patch.object(Path, "read_text", return_value='{"state": "ok"}') as read_text:
            self.assertEqual(gateway_pressure._read_json("/tmp/health.json"), {"state": "ok"})
        read_text.assert_called_once_with(encoding="utf-8")

        for error in (OSError("offline"), json.JSONDecodeError("bad", "x", 0)):
            with self.subTest(error=type(error).__name__):
                with patch.object(Path, "read_text", side_effect=error):
                    self.assertIsNone(gateway_pressure._read_json("/tmp/health.json"))

    def test_service_health_path_accepts_only_the_declared_string_attribute(self) -> None:
        exact = type("Service", (), {"gateway_health_path": " /run/health.json "})()
        missing = object()
        invalid = type("Service", (), {"gateway_health_path": Path("/run/health.json")})()

        self.assertEqual(gateway_pressure._service_health_path(exact), " /run/health.json ")
        self.assertEqual(gateway_pressure._service_health_path(missing), "")
        self.assertEqual(gateway_pressure._service_health_path(invalid), "")

    def test_numeric_boundary_helpers_preserve_values_and_reject_invalid_input(self) -> None:
        for raw, expected in ((3.5, 3.5), (0.0, 0.0), (-1.25, 0.0)):
            with self.subTest(raw=raw):
                self.assertEqual(gateway_pressure._non_negative_seconds(raw), expected)

        for raw, expected in (
            ("2.5", 2.5),
            (b"3.5", 3.5),
            (_FloatValue(4.5), 4.5),
            ("invalid", 0.0),
            (object(), 0.0),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(gateway_pressure._float_or_zero(raw), expected)

    def test_staleness_contract_uses_strict_age_boundary_and_positive_budget(self) -> None:
        self.assertFalse(gateway_pressure._payload_stale(10.0, 10.0))
        self.assertTrue(gateway_pressure._payload_stale(10.001, 10.0))
        self.assertTrue(gateway_pressure._payload_stale(0.501, 0.5))
        self.assertFalse(gateway_pressure._payload_stale(10.001, 0.0))
        self.assertFalse(gateway_pressure._payload_stale(10.001, -1.0))

    def test_resource_state_contract_covers_each_mapping_and_unknown_shapes(self) -> None:
        cases = (
            ({"state": " CONSTRAINED "}, ("slow", "resources")),
            ({"state": "Busy"}, ("congested", "resources")),
            ({"state": "ok"}, ("ok", "resources")),
            ({"state": "idle"}, ("unknown", "unknown")),
            ({"state": 1}, ("unknown", "unknown")),
            ({}, ("unknown", "unknown")),
        )
        for resources, expected in cases:
            with self.subTest(resources=resources):
                self.assertEqual(gateway_pressure._resource_state(resources), expected)


if __name__ == "__main__":
    unittest.main()
