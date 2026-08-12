#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for DBus operation-latency aggregation."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.health.latency import operation_p95_ms


class DbusAdapterHealthLatencyContracts(unittest.TestCase):
    def test_slowest_valid_summary_wins(self) -> None:
        self.assertEqual(
            operation_p95_ms(
                {
                    "p95_latency_ms": "125",
                    "operations": {
                        "read-fast": {"p95_latency_ms": "250"},
                        "invalid": "not-a-summary",
                    },
                }
            ),
            250.0,
        )

    def test_overall_summary_survives_missing_operation_breakdown(self) -> None:
        self.assertEqual(operation_p95_ms({"p95_latency_ms": "125"}), 125.0)
        self.assertEqual(operation_p95_ms({"operations": "invalid"}), 0.0)


if __name__ == "__main__":
    unittest.main()
