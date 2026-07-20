# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact resource-monitor and event-loop health contracts."""

from __future__ import annotations

import os
import unittest
from unittest.mock import mock_open, patch

from venus_evcharger.dbus_adapter.resources import (
    BUSY_CPU_PERCENT,
    BUSY_LOAD_PER_CPU,
    BUSY_MEM_AVAILABLE_KB,
    CONSTRAINED_CPU_PERCENT,
    CONSTRAINED_LOAD_PER_CPU,
    CONSTRAINED_MEM_AVAILABLE_KB,
    ResourceMonitor,
    TickHealth,
    _average,
    _percentage,
    _read_proc_numeric_mapping,
    resource_state,
)


class TestDbusAdapterResourceContracts(unittest.TestCase):
    def test_tick_health_exact_snapshot_and_pruning(self) -> None:
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

    def test_tick_lateness_threshold_is_exclusive(self) -> None:
        health = TickHealth(window_seconds=60.0)
        health.record(duration_ms=2000.0, expected_interval_s=1.0, now=10.0)
        health.record(duration_ms=1.0, expected_interval_s=1.0, now=12.0)
        snapshot = health.snapshot(now=12.0)
        self.assertEqual(snapshot["late_ticks_60s"], 0)
        self.assertEqual(snapshot["late_tick_gap_count_60s"], 0)

    def test_tick_health_default_window_and_small_gap_boundaries(self) -> None:
        default = TickHealth()
        default.record(duration_ms=1.0, expected_interval_s=1.0, now=100.0)
        self.assertEqual(default.snapshot(now=160.0)["tick_count_60s"], 1)
        self.assertEqual(default.snapshot(now=160.001)["tick_count_60s"], 0)

        health = TickHealth(window_seconds=10.0)
        health.record(duration_ms=1.0, expected_interval_s=-1.0, now=0.5)
        health.record(duration_ms=1.0, expected_interval_s=-1.0, now=0.5005)
        snapshot = health.snapshot(now=0.5005)
        self.assertEqual(snapshot["late_ticks_60s"], 2)
        self.assertAlmostEqual(snapshot["max_tick_gap_ms_60s"], 0.5)
        self.assertEqual(snapshot["late_tick_gap_count_60s"], 1)

        late_gap = TickHealth(window_seconds=10.0)
        late_gap.record(duration_ms=0.0, expected_interval_s=1.0, now=10.0)
        late_gap.record(duration_ms=0.0, expected_interval_s=1.0, now=12.001)
        self.assertEqual(late_gap.snapshot(now=12.001)["late_tick_gap_count_60s"], 1)

    def test_empty_tick_and_resource_payload_defaults(self) -> None:
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
        monitor = ResourceMonitor(pid=123)
        with patch.object(monitor, "_open_fd_count", return_value=0), patch(
            "venus_evcharger.dbus_adapter.resources.os.cpu_count", return_value=None
        ):
            payload = monitor._snapshot_payload(
                load=(0.0, 0.0, 0.0),
                meminfo={},
                process_cpu_pct=0.0,
                status={},
                system_cpu_pct=0.0,
            )
        self.assertEqual(payload["mem_total_kb"], 0.0)
        self.assertEqual(payload["mem_available_kb"], 0.0)
        self.assertEqual(payload["load_per_cpu_1m"], 0.0)
        self.assertEqual(
            payload["process"],
            {
                "pid": 123,
                "rss_kb": 0.0,
                "rss_hwm_kb": 0.0,
                "threads": 0,
                "fd_size": 0,
                "open_fds": 0,
                "cpu_pct_one_core": 0.0,
            },
        )
        with patch("venus_evcharger.dbus_adapter.resources.os.cpu_count", return_value=None):
            one_cpu = monitor._snapshot_payload(
                load=(1.0, 0.0, 0.0),
                meminfo={"MemTotal": 200000.0, "MemAvailable": 100000.0},
                process_cpu_pct=0.0,
                status={},
                system_cpu_pct=0.0,
            )
        self.assertEqual(one_cpu["load_per_cpu_1m"], 1.0)
        self.assertEqual(one_cpu["state"], "busy")

    def test_resource_snapshot_payload_is_exact(self) -> None:
        monitor = ResourceMonitor(pid=123)
        with patch.object(monitor, "_open_fd_count", return_value=7), patch(
            "venus_evcharger.dbus_adapter.resources.os.cpu_count", return_value=4
        ):
            payload = monitor._snapshot_payload(
                load=(2.0, 3.0, 4.0),
                meminfo={"MemTotal": 1000.0, "MemAvailable": 250.0},
                process_cpu_pct=12.5,
                status={"VmRSS": 10.0, "VmHWM": 20.0, "Threads": 3.0, "FDSize": 64.0},
                system_cpu_pct=50.0,
            )
        self.assertEqual(
            payload,
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
        with patch("venus_evcharger.dbus_adapter.resources.os.cpu_count", return_value=4):
            load_scaled = monitor._snapshot_payload(
                load=(2.0, 0.0, 0.0),
                meminfo={"MemTotal": 200000.0, "MemAvailable": 100000.0},
                process_cpu_pct=0.0,
                status={},
                system_cpu_pct=0.0,
            )
        self.assertEqual(load_scaled["load_per_cpu_1m"], 0.5)
        self.assertEqual(load_scaled["state"], "ok")

    def test_public_snapshot_forwards_every_source(self) -> None:
        monitor = ResourceMonitor(pid=123)
        with patch.object(monitor, "_read_system_cpu", return_value=(100, 40)), patch.object(
            monitor, "_read_process_cpu_seconds", return_value=1.5
        ), patch.object(monitor, "_cpu_percentages", return_value=(60.0, 20.0)) as cpu_percentages, patch.object(
            monitor, "_read_meminfo", return_value={"MemTotal": 100.0, "MemAvailable": 80.0}
        ), patch.object(monitor, "_read_process_status", return_value={"Threads": 2.0}), patch.object(
            monitor, "_loadavg", return_value=(0.1, 0.2, 0.3)
        ), patch.object(monitor, "_snapshot_payload", return_value={"state": "ok"}) as snapshot_payload, patch(
            "venus_evcharger.dbus_adapter.resources.time.monotonic", return_value=10.0
        ):
            self.assertEqual(monitor.snapshot(), {"state": "ok"})
        cpu_percentages.assert_called_once_with(10.0, 100, 40, 1.5)
        snapshot_payload.assert_called_once_with(
            load=(0.1, 0.2, 0.3),
            meminfo={"MemTotal": 100.0, "MemAvailable": 80.0},
            process_cpu_pct=20.0,
            status={"Threads": 2.0},
            system_cpu_pct=60.0,
        )

    def test_cpu_percentages_cover_first_delta_and_clamps(self) -> None:
        monitor = ResourceMonitor(pid=123)
        self.assertEqual(monitor._cpu_percentages(10.0, 100, 40, 1.0), (0.0, 0.0))
        self.assertEqual(monitor._last_sample, (10.0, 100, 40, 1.0))
        system, process = monitor._cpu_percentages(11.0, 200, 80, 1.2)
        self.assertAlmostEqual(system, 60.0)
        self.assertAlmostEqual(process, 20.0)
        self.assertEqual(monitor._last_sample, (11.0, 200, 80, 1.2))
        self.assertEqual(monitor._cpu_percentages(10.0, 150, 90, 1.0), (0.0, 0.0))

        one_tick = ResourceMonitor(pid=123)
        one_tick._cpu_percentages(10.0, 100, 40, 1.0)
        one_tick_system, one_tick_process = one_tick._cpu_percentages(12.0, 101, 40, 1.2)
        self.assertEqual(one_tick_system, 100.0)
        self.assertAlmostEqual(one_tick_process, 10.0)

    def test_procfs_success_parsers(self) -> None:
        monitor = ResourceMonitor(pid=123)
        system_open = mock_open(read_data="cpu 1 2 3 4 5 6\n")
        with patch("builtins.open", system_open):
            self.assertEqual(monitor._read_system_cpu(), (21, 9))
        system_open.assert_called_once_with("/proc/stat", encoding="utf-8")
        stat_parts = ["0"] * 15
        stat_parts[13], stat_parts[14] = "10", "20"
        process_open = mock_open(read_data=" ".join(stat_parts))
        with patch("builtins.open", process_open), patch(
            "venus_evcharger.dbus_adapter.resources.os.sysconf", return_value=100
        ) as sysconf:
            self.assertEqual(monitor._read_process_cpu_seconds(), 0.3)
        process_open.assert_called_once_with("/proc/123/stat", encoding="utf-8")
        sysconf.assert_called_once_with(os.sysconf_names["SC_CLK_TCK"])
        mem_open = mock_open(read_data="MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
        with patch("builtins.open", mem_open):
            self.assertEqual(monitor._read_meminfo(), {"MemTotal": 1000.0, "MemAvailable": 250.0})
        mem_open.assert_called_once_with("/proc/meminfo", encoding="utf-8")
        status_open = mock_open(read_data="VmRSS: 10 kB\nThreads: 3\nName: python\n")
        with patch("builtins.open", status_open):
            self.assertEqual(monitor._read_process_status(), {"VmRSS": 10.0, "Threads": 3.0})
        status_open.assert_called_once_with("/proc/123/status", encoding="utf-8")
        with patch("venus_evcharger.dbus_adapter.resources.os.listdir", return_value=["0", "1", "2"]):
            self.assertEqual(monitor._open_fd_count(), 3)

    def test_process_status_ignores_empty_venus_os_groups_field(self) -> None:
        status = (
            "Name:\tpython3\n"
            "Groups:\t \n"
            "FDSize:\t256\n"
            "VmHWM:\t21764 kB\n"
            "VmRSS:\t14480 kB\n"
            "Threads:\t1\n"
        )
        with patch("builtins.open", mock_open(read_data=status)):
            self.assertEqual(
                ResourceMonitor(pid=1481)._read_process_status(),
                {"FDSize": 256.0, "VmHWM": 21764.0, "VmRSS": 14480.0, "Threads": 1.0},
            )

    def test_procfs_short_and_malformed_inputs_fail_closed(self) -> None:
        monitor = ResourceMonitor(pid=123)
        with patch("builtins.open", side_effect=OSError("missing")):
            self.assertEqual(monitor._read_system_cpu(), (0, 0))
        with patch("builtins.open", mock_open(read_data="cpu 1 2\n")):
            self.assertEqual(monitor._read_system_cpu(), (3, 0))
        with patch("builtins.open", mock_open(read_data="cpu 1 2 3\n")):
            self.assertEqual(monitor._read_system_cpu(), (6, 0))
        with patch("builtins.open", mock_open(read_data="cpu 1 2 3 4\n")):
            self.assertEqual(monitor._read_system_cpu(), (10, 4))
        with patch("builtins.open", mock_open(read_data="short")):
            self.assertEqual(monitor._read_process_cpu_seconds(), 0.0)
        with patch("builtins.open", mock_open(read_data="broken\n")):
            self.assertEqual(monitor._read_meminfo(), {})
            self.assertEqual(monitor._read_process_status(), {})
        with patch("builtins.open", mock_open(read_data="Key: 5:6\n")):
            self.assertEqual(_read_proc_numeric_mapping("/proc/test"), {})

    def test_proc_numeric_mapping_modes_and_loadavg(self) -> None:
        with patch("builtins.open", mock_open(read_data="Value: 1.5 kB\nCount: 2\n")):
            self.assertEqual(_read_proc_numeric_mapping("/proc/test"), {"Value": 1.5, "Count": 2.0})
        with patch("builtins.open", mock_open(read_data="Value: 1.5 kB\nCount: 2\n")):
            self.assertEqual(_read_proc_numeric_mapping("/proc/test", digits_only=True), {"Count": 2.0})
        with patch("venus_evcharger.dbus_adapter.resources.os.getloadavg", return_value=(1, 2, 3)):
            self.assertEqual(ResourceMonitor._loadavg(), (1.0, 2.0, 3.0))
        with patch("venus_evcharger.dbus_adapter.resources.os.getloadavg", side_effect=OSError):
            self.assertEqual(ResourceMonitor._loadavg(), (0.0, 0.0, 0.0))
        with patch("venus_evcharger.dbus_adapter.resources.os.listdir", side_effect=OSError):
            self.assertEqual(ResourceMonitor(pid=123)._open_fd_count(), 0)

    def test_helpers_and_resource_thresholds_are_exact(self) -> None:
        self.assertEqual(_average([]), 0.0)
        self.assertEqual(_average([1.0, 3.0]), 2.0)
        self.assertEqual(_percentage(25.0, 100.0), 25.0)
        self.assertEqual(_percentage(25.0, 0.0), 0.0)
        self.assertEqual(_percentage(0.5, 1.0), 50.0)
        self.assertEqual(resource_state(CONSTRAINED_LOAD_PER_CPU, 0.0, 100000.0), "constrained")
        self.assertEqual(resource_state(0.0, CONSTRAINED_CPU_PERCENT, 100000.0), "constrained")
        self.assertEqual(resource_state(0.0, 0.0, CONSTRAINED_MEM_AVAILABLE_KB - 1.0), "constrained")
        self.assertEqual(resource_state(0.0, 0.0, CONSTRAINED_MEM_AVAILABLE_KB), "busy")
        self.assertEqual(resource_state(BUSY_LOAD_PER_CPU, 0.0, 100000.0), "busy")
        self.assertEqual(resource_state(0.0, BUSY_CPU_PERCENT, 100000.0), "busy")
        self.assertEqual(resource_state(0.0, 0.0, BUSY_MEM_AVAILABLE_KB - 1.0), "busy")
        self.assertEqual(resource_state(0.0, 0.0, BUSY_MEM_AVAILABLE_KB), "ok")


if __name__ == "__main__":
    unittest.main()
