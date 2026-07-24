# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure resource parsing and metric contracts."""

from __future__ import annotations

import math
import unittest

from venus_evcharger.dbus_adapter.resource_metrics import (
    CpuUsageTracker,
    ResourceMetrics,
    average,
    numeric_token,
    parse_numeric_mapping,
    parse_process_cpu_seconds,
    parse_system_cpu,
    percentage,
)


def _required_float(value: float | None) -> float:
    if value is None:
        raise AssertionError("expected an available float")
    return value


class TestResourceMetricsContracts(unittest.TestCase):
    def test_system_and_process_cpu_parsers_preserve_procfs_semantics(self) -> None:
        self.assertEqual(parse_system_cpu("cpu 1 2 3 4 5 6\n"), (21, 9))
        self.assertEqual(parse_system_cpu("cpu 1 2\n"), (3, 0))
        self.assertEqual(parse_system_cpu("cpu 1 2 3\n"), (6, 0))
        self.assertEqual(parse_system_cpu("cpu 1 2 3 4\n"), (10, 4))
        self.assertEqual(parse_system_cpu(""), (0, 0))
        self.assertEqual(parse_system_cpu("cpu 1 2 3 4 5\ncpu0 90 90 90 90\n"), (15, 9))

        fields_from_state = ["S", *(["0"] * 12)]
        fields_from_state[11], fields_from_state[12] = "10", "20"
        self.assertEqual(
            parse_process_cpu_seconds(
                f"123 (worker name with ) bracket) {' '.join(fields_from_state)}",
                clock_ticks_per_second=100.0,
            ),
            0.3,
        )
        with self.assertRaisesRegex(ValueError, "^process stat command field is missing$"):
            parse_process_cpu_seconds("short", clock_ticks_per_second=100.0)
        with self.assertRaises(IndexError):
            parse_process_cpu_seconds("123 (short) S 0", clock_ticks_per_second=100.0)

    def test_numeric_mapping_modes_and_fail_closed_behavior(self) -> None:
        lines = ["Value: 1.5 kB\n", "Count: 2\n"]
        self.assertEqual(parse_numeric_mapping(lines), {"Value": 1.5, "Count": 2.0})
        self.assertEqual(parse_numeric_mapping(lines, digits_only=True), {"Count": 2.0})
        self.assertEqual(parse_numeric_mapping(["Key: 1\n", "broken\n"]), {})
        with self.assertRaises(ValueError):
            parse_numeric_mapping(["Key: nested: 1\n"])
        self.assertEqual(parse_numeric_mapping(["Groups:\t \n"], digits_only=True), {})
        self.assertIsNone(numeric_token(" \n", digits_only=False))
        self.assertIsNone(numeric_token("1.5 kB", digits_only=True))
        self.assertEqual(numeric_token("2 kB", digits_only=True), "2")

    def test_cpu_usage_tracks_first_sample_deltas_and_clamps(self) -> None:
        tracker = CpuUsageTracker()
        self.assertEqual(
            tracker.percentages(
                now=10.0,
                system_cpu=(100, 40),
                process_cpu=1.0,
                cpu_count=1,
            ),
            (0.0, 0.0),
        )
        self.assertEqual(tracker.last_system_sample, (10.0, 100, 40))
        self.assertEqual(tracker.last_process_sample, (10.0, 1.0))
        system, process = tracker.percentages(
            now=11.0,
            system_cpu=(200, 80),
            process_cpu=1.2,
            cpu_count=1,
        )
        self.assertAlmostEqual(_required_float(system), 60.0)
        self.assertAlmostEqual(_required_float(process), 20.0)
        self.assertEqual(tracker.last_system_sample, (11.0, 200, 80))
        self.assertEqual(tracker.last_process_sample, (11.0, 1.2))
        self.assertEqual(
            tracker.percentages(
                now=10.0,
                system_cpu=(150, 90),
                process_cpu=1.0,
                cpu_count=1,
            ),
            (0.0, 0.0),
        )

        one_tick = CpuUsageTracker()
        one_tick.percentages(
            now=10.0,
            system_cpu=(100, 40),
            process_cpu=1.0,
            cpu_count=1,
        )
        one_tick_system, one_tick_process = one_tick.percentages(
            now=12.0,
            system_cpu=(101, 40),
            process_cpu=1.2,
            cpu_count=1,
        )
        self.assertEqual(one_tick_system, 100.0)
        self.assertAlmostEqual(_required_float(one_tick_process), 10.0)

    def test_cpu_failures_rebaseline_each_source_and_bounds_are_physical(self) -> None:
        tracker = CpuUsageTracker()
        tracker.percentages(
            now=1.0,
            system_cpu=(100, 50),
            process_cpu=1.0,
            cpu_count=2,
        )
        self.assertEqual(
            tracker.percentages(
                now=2.0,
                system_cpu=None,
                process_cpu=5.0,
                cpu_count=2,
            ),
            (None, 200.0),
        )
        self.assertEqual(
            tracker.percentages(
                now=3.0,
                system_cpu=(300, 150),
                process_cpu=None,
                cpu_count=2,
            ),
            (0.0, None),
        )
        system, process = tracker.percentages(
            now=4.0,
            system_cpu=(400, 400),
            process_cpu=9.0,
            cpu_count=2,
        )
        self.assertEqual(system, 0.0)
        self.assertEqual(process, 0.0)
        self.assertEqual(
            tracker.percentages(
                now=5.0,
                system_cpu=(500, 350),
                process_cpu=math.inf,
                cpu_count=2,
            ),
            (0.0, None),
        )
        self.assertIsNone(tracker.last_process_sample)
        self.assertEqual(
            tracker.percentages(
                now=6.0,
                system_cpu=(600, 350),
                process_cpu=10.0,
                cpu_count=2,
            ),
            (100.0, 0.0),
        )

        boundary = CpuUsageTracker()
        boundary.percentages(
            now=1.0,
            system_cpu=(100, 50),
            process_cpu=0.0,
            cpu_count=1,
        )
        self.assertEqual(
            boundary.percentages(
                now=1.0,
                system_cpu=(200, 50),
                process_cpu=0.5,
                cpu_count=1,
            ),
            (0.0, 0.0),
        )
        self.assertEqual(
            boundary.percentages(
                now=2.0,
                system_cpu=(200, 50),
                process_cpu=0.5,
                cpu_count=1,
            ),
            (0.0, 0.0),
        )

        one_core = CpuUsageTracker()
        one_core.percentages(
            now=1.0,
            system_cpu=None,
            process_cpu=0.0,
            cpu_count=1,
        )
        self.assertEqual(
            one_core.percentages(
                now=2.0,
                system_cpu=None,
                process_cpu=2.0,
                cpu_count=1,
            ),
            (None, 100.0),
        )

    def test_resource_metrics_build_exact_normalized_payload(self) -> None:
        metrics = ResourceMetrics(
            load=(2.0, 3.0, 4.0),
            cpu_count=4,
            system_cpu_pct=50.0,
            process_cpu_pct=12.5,
            meminfo={"MemTotal": 1000.0, "MemAvailable": 250.0},
            memory_sample_status="cached",
            memory_sample_age_s=1.5,
            process_status={"VmRSS": 10.0, "VmHWM": 20.0, "Threads": 3.0, "FDSize": 64.0},
            open_fds=7,
        )
        self.assertEqual(metrics.load_per_cpu, 0.5)
        self.assertEqual(metrics.mem_available_kb, 250.0)
        self.assertEqual(
            metrics.payload(pid=123, state="constrained"),
            {
                "state": "constrained",
                "loadavg_1m": 2.0,
                "loadavg_5m": 3.0,
                "loadavg_15m": 4.0,
                "load_per_cpu_1m": 0.5,
                "system_cpu_pct": 50.0,
                "mem_total_kb": 1000.0,
                "mem_available_kb": 250.0,
                "mem_available_pct": 25.0,
                "memory_sample_status": "cached",
                "memory_sample_age_s": 1.5,
                "process": {
                    "pid": 123,
                    "rss_kb": 10.0,
                    "rss_hwm_kb": 20.0,
                    "threads": 3,
                    "fd_size": 64,
                    "open_fds": 7,
                    "cpu_pct_one_core": 12.5,
                },
            },
        )

        empty = ResourceMetrics(
            load=(1.0, 0.0, 0.0),
            cpu_count=0,
            system_cpu_pct=0.0,
            process_cpu_pct=0.0,
            meminfo=None,
            memory_sample_status="unavailable",
            memory_sample_age_s=None,
            process_status=None,
            open_fds=None,
        )
        self.assertEqual(empty.load_per_cpu, 1.0)
        self.assertIsNone(empty.mem_available_kb)
        payload = empty.payload(pid=1, state="ok")
        self.assertIsNone(payload["mem_available_pct"])
        self.assertEqual(
            payload["process"],
            {
                "pid": 1,
                "rss_kb": None,
                "rss_hwm_kb": None,
                "threads": None,
                "fd_size": None,
                "open_fds": None,
                "cpu_pct_one_core": 0.0,
            },
        )

        unavailable = ResourceMetrics(
            load=None,
            cpu_count=2,
            system_cpu_pct=math.nan,
            process_cpu_pct=500.0,
            meminfo={"MemTotal": math.inf, "MemAvailable": math.nan},
            memory_sample_status="unavailable",
            memory_sample_age_s=None,
            process_status={"Threads": math.nan},
            open_fds=None,
        )
        unavailable_payload = unavailable.payload(pid=2, state="ok")
        self.assertIsNone(unavailable.load_per_cpu)
        self.assertEqual(unavailable_payload["system_cpu_pct"], 0.0)
        self.assertIsNone(unavailable_payload["mem_total_kb"])
        self.assertIsNone(unavailable_payload["mem_available_kb"])
        self.assertEqual(
            unavailable_payload["process"],
            {
                "pid": 2,
                "rss_kb": None,
                "rss_hwm_kb": None,
                "threads": None,
                "fd_size": None,
                "open_fds": None,
                "cpu_pct_one_core": 200.0,
            },
        )

        capped_system = ResourceMetrics(
            load=None,
            cpu_count=1,
            system_cpu_pct=150.0,
            process_cpu_pct=None,
            meminfo=None,
            memory_sample_status="unavailable",
            memory_sample_age_s=None,
            process_status=None,
            open_fds=None,
        )
        self.assertEqual(capped_system.payload(pid=3, state="ok")["system_cpu_pct"], 100.0)

        missing_available = ResourceMetrics(
            load=None,
            cpu_count=1,
            system_cpu_pct=None,
            process_cpu_pct=None,
            meminfo={"MemTotal": 100.0},
            memory_sample_status="unavailable",
            memory_sample_age_s=None,
            process_status=None,
            open_fds=None,
        )
        missing_total = ResourceMetrics(
            load=None,
            cpu_count=1,
            system_cpu_pct=None,
            process_cpu_pct=None,
            meminfo={"MemAvailable": 50.0},
            memory_sample_status="unavailable",
            memory_sample_age_s=None,
            process_status=None,
            open_fds=None,
        )
        self.assertIsNone(missing_available.payload(pid=4, state="ok")["mem_available_pct"])
        self.assertIsNone(missing_total.payload(pid=5, state="ok")["mem_available_pct"])

    def test_small_metric_helpers_are_exact(self) -> None:
        self.assertEqual(average([]), 0.0)
        self.assertEqual(average([1.0, 3.0]), 2.0)
        self.assertEqual(percentage(25.0, 100.0), 25.0)
        self.assertEqual(percentage(25.0, 0.0), 0.0)
        self.assertEqual(percentage(0.5, 1.0), 50.0)
        self.assertEqual(percentage(-1.0, 100.0), 0.0)
        self.assertEqual(percentage(200.0, 100.0), 100.0)
        self.assertEqual(percentage(math.inf, 100.0), 0.0)
        self.assertEqual(percentage(1.0, math.nan), 0.0)


if __name__ == "__main__":
    unittest.main()
