# SPDX-License-Identifier: GPL-3.0-or-later
"""Event-loop tick health contracts."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.tick_health import TickHealth


def _required_float(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise AssertionError("expected a numeric value")
    return float(value)


class TestTickHealthContracts(unittest.TestCase):
    def test_exact_snapshot_and_pruning(self) -> None:
        self.assertEqual(TickHealth(window_seconds=0.0).window_seconds, 1.0)
        health = TickHealth(window_seconds=10.0)
        health.record(duration_ms=-5.0, expected_interval_s=1.0, now=100.0)
        health.record(duration_ms=2001.0, expected_interval_s=1.0, now=102.1)
        expected_gap_ms = (102.1 - 100.0) * 1000.0
        self.assertEqual(
            health.snapshot(now=102.1),
            {
                "tick_count_60s": 2,
                "avg_tick_duration_ms_60s": 1000.5,
                "max_tick_duration_ms_60s": 2001.0,
                "late_ticks_60s": 1,
                "avg_tick_gap_ms_60s": expected_gap_ms,
                "max_tick_gap_ms_60s": expected_gap_ms,
                "late_tick_gap_count_60s": 1,
            },
        )
        self.assertEqual(health.snapshot(now=113.0)["tick_count_60s"], 0)

    def test_lateness_threshold_is_exclusive(self) -> None:
        health = TickHealth(window_seconds=60.0)
        health.record(duration_ms=2000.0, expected_interval_s=1.0, now=10.0)
        health.record(duration_ms=1.0, expected_interval_s=1.0, now=12.0)
        snapshot = health.snapshot(now=12.0)
        self.assertEqual(snapshot["late_ticks_60s"], 0)
        self.assertEqual(snapshot["late_tick_gap_count_60s"], 0)

    def test_default_window_and_small_gap_boundaries(self) -> None:
        default = TickHealth()
        default.record(duration_ms=1.0, expected_interval_s=1.0, now=100.0)
        self.assertEqual(default.snapshot(now=160.0)["tick_count_60s"], 1)
        self.assertEqual(default.snapshot(now=160.001)["tick_count_60s"], 0)

        health = TickHealth(window_seconds=10.0)
        health.record(duration_ms=1.0, expected_interval_s=-1.0, now=0.5)
        health.record(duration_ms=1.0, expected_interval_s=-1.0, now=0.5005)
        snapshot = health.snapshot(now=0.5005)
        self.assertEqual(snapshot["late_ticks_60s"], 2)
        self.assertAlmostEqual(_required_float(snapshot["max_tick_gap_ms_60s"]), 0.5)
        self.assertEqual(snapshot["late_tick_gap_count_60s"], 1)

        late_gap = TickHealth(window_seconds=10.0)
        late_gap.record(duration_ms=0.0, expected_interval_s=1.0, now=10.0)
        late_gap.record(duration_ms=0.0, expected_interval_s=1.0, now=12.001)
        self.assertEqual(late_gap.snapshot(now=12.001)["late_tick_gap_count_60s"], 1)

    def test_empty_snapshot_uses_zero_defaults(self) -> None:
        self.assertEqual(
            TickHealth().snapshot(now=10.0),
            {
                "tick_count_60s": 0,
                "avg_tick_duration_ms_60s": 0.0,
                "max_tick_duration_ms_60s": 0.0,
                "late_ticks_60s": 0,
                "avg_tick_gap_ms_60s": 0.0,
                "max_tick_gap_ms_60s": 0.0,
                "late_tick_gap_count_60s": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
