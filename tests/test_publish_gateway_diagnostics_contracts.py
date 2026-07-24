# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the semantic gateway-diagnostics consumer projection."""

from __future__ import annotations

import unittest

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

        values = projection.values(100.0)

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
        values = GatewayDiscoveryDiagnostics(_UnavailableReader()).values(100.0)

        self.assertEqual(values.state, "unavailable")
        self.assertEqual(values.pending_work, 0)
        self.assertEqual(values.discovered_source_count, 0)
        self.assertEqual(values.unusable_source_count, 0)
        self.assertEqual(values.age_seconds, -1.0)

    def test_future_capture_time_is_rejected(self) -> None:
        projection = GatewayDiscoveryDiagnostics(
            gateway_diagnostics_reader(captured_at=110.0)
        )
        with self.assertRaisesRegex(ValueError, "captured_at"):
            projection.values(100.0)


if __name__ == "__main__":
    unittest.main()
