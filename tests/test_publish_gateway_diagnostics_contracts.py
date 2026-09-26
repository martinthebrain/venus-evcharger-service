# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the semantic gateway-diagnostics consumer projection."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_reader
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)
from venus_evcharger.publish.gateway_diagnostics import GatewayDiscoveryDiagnostics


class _UnavailableReader:
    def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
        raise GatewayDiagnosticsUnavailable("missing")


class GatewayDiscoveryDiagnosticsContractTests(unittest.TestCase):
    def test_projection_exposes_only_semantic_discovery_and_source_health(self) -> None:
        projection = GatewayDiscoveryDiagnostics(
            gateway_diagnostics_reader(
                captured_at=90.0,
                discovery_state="running",
                discovery_pending_work=3,
                discovered_source_count=4,
                unusable_source_count=2,
            )
        )

        with patch("venus_evcharger.publish.gateway_diagnostics.time.monotonic", return_value=100.0):
            values = projection.values()

        self.assertEqual(
            values.counter_values(),
            {
                "auto_gateway_discovery_state": "running",
                "auto_gateway_discovery_pending_work": 3,
                "auto_gateway_discovered_source_count": 4,
                "auto_gateway_unusable_source_count": 2,
            },
        )
        self.assertEqual(values.age_seconds, 10.0)

    def test_unavailable_transport_fails_closed_without_raw_fallback(self) -> None:
        values = GatewayDiscoveryDiagnostics(_UnavailableReader()).values()

        self.assertEqual(values.state, "unavailable")
        self.assertEqual(values.pending_work, 0)
        self.assertEqual(values.discovered_source_count, 0)
        self.assertEqual(values.unusable_source_count, 0)
        self.assertEqual(values.age_seconds, -1.0)

    def test_future_monotonic_capture_time_fails_closed_without_aborting_cycle(self) -> None:
        projection = GatewayDiscoveryDiagnostics(
            gateway_diagnostics_reader(
                captured_at=90.0,
                captured_monotonic=110.0,
            )
        )
        with patch("venus_evcharger.publish.gateway_diagnostics.time.monotonic", return_value=100.0):
            values = projection.values()
        self.assertEqual(values.state, "unavailable")
        self.assertEqual(values.age_seconds, -1.0)

    def test_clock_is_sampled_after_concurrent_snapshot_publication(self) -> None:
        reader = gateway_diagnostics_reader(captured_at=90.0, captured_monotonic=110.0)
        with patch("venus_evcharger.publish.gateway_diagnostics.time.monotonic", return_value=100.0) as clock:
            class ConcurrentReader:
                def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
                    clock.assert_not_called()
                    clock.return_value = 111.0
                    return reader.read_snapshot()

            values = GatewayDiscoveryDiagnostics(ConcurrentReader()).values()
        self.assertEqual(values.age_seconds, 1.0)


if __name__ == "__main__":
    unittest.main()
