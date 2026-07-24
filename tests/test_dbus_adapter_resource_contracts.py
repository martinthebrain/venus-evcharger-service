# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource-monitor orchestration contracts."""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from itertools import repeat
from unittest.mock import patch

import venus_evcharger.dbus_adapter.resources as resources_module
from venus_evcharger.dbus_adapter.resources import (
    ResourceMonitor,
    ResourceMonitorSettings,
)


def _settings(
    *,
    sample_interval_seconds: float = 0.0,
    recovery_hold_seconds: float = 0.0,
    memory_stale_seconds: float = 10.0,
) -> ResourceMonitorSettings:
    return ResourceMonitorSettings(
        sample_interval_seconds=sample_interval_seconds,
        recovery_hold_seconds=recovery_hold_seconds,
        memory_stale_seconds=memory_stale_seconds,
    )


def _expected_process(cpu_pct: float | None) -> dict[str, object]:
    return {
        "pid": 123,
        "rss_kb": 10.0,
        "rss_hwm_kb": 20.0,
        "threads": 3,
        "fd_size": 64,
        "open_fds": 7,
        "cpu_pct_one_core": cpu_pct,
    }


class _ResourceReader:
    def __init__(
        self,
        *,
        system_cpu: Iterator[tuple[int, int] | None] | None = None,
        process_cpu: Iterator[float | None] | None = None,
        mem_available_kb: float = 100000.0,
        meminfo: Iterator[dict[str, float] | None] | None = None,
        cpu_count: int = 1,
        load_average: tuple[float, float, float] = (0.2, 0.3, 0.4),
    ) -> None:
        self._system_cpu = system_cpu or iter([(100, 50)])
        self._process_cpu = process_cpu or iter([1.0])
        default_meminfo = {
            "MemTotal": 200000.0,
            "MemAvailable": mem_available_kb,
        }
        self._meminfo = meminfo if meminfo is not None else repeat(default_meminfo)
        self._cpu_count = cpu_count
        self._load_average = load_average
        self.system_cpu_calls = 0
        self.meminfo_calls = 0
        self.process_status_calls = 0

    def system_cpu(self) -> tuple[int, int] | None:
        self.system_cpu_calls += 1
        return next(self._system_cpu)

    def process_cpu_seconds(self) -> float | None:
        return next(self._process_cpu)

    def meminfo(self) -> dict[str, float] | None:
        self.meminfo_calls += 1
        return next(self._meminfo)

    def process_status(self) -> dict[str, float] | None:
        self.process_status_calls += 1
        return {"VmRSS": 10.0, "VmHWM": 20.0, "Threads": 3.0, "FDSize": 64.0}

    def load_average(self) -> tuple[float, float, float] | None:
        return self._load_average

    def cpu_count(self) -> int:
        return self._cpu_count

    @staticmethod
    def open_fd_count() -> int | None:
        return 7


class TestResourceMonitorContracts(unittest.TestCase):
    def test_snapshot_copy_preserves_non_mapping_process_metadata(self) -> None:
        payload = {"state": "unknown", "process": "unavailable"}

        copied = resources_module._snapshot_copy(payload)

        self.assertEqual(copied, payload)
        self.assertIsNot(copied, payload)

    def test_monitor_uses_current_process_and_procfs_reader_by_default(self) -> None:
        with (
            patch("venus_evcharger.dbus_adapter.resources.os.getpid", return_value=321),
            patch("venus_evcharger.dbus_adapter.resources.resource_procfs.ProcfsResourceReader") as reader,
        ):
            monitor = ResourceMonitor(
                settings=_settings(
                    sample_interval_seconds=-1.0,
                    recovery_hold_seconds=-1.0,
                    memory_stale_seconds=-1.0,
                ),
            )

        self.assertEqual(monitor.pid, 321)
        self.assertEqual(monitor.sample_interval_seconds, 0.0)
        self.assertEqual(monitor.memory_stale_seconds, 0.0)
        reader.assert_called_once_with(321)

    def test_default_sampling_and_recovery_intervals_are_exact(self) -> None:
        reader = _ResourceReader(
            system_cpu=iter([(100, 50), (200, 100), (300, 150)]),
            process_cpu=iter([1.0, 2.0, 3.0]),
            meminfo=iter(
                [
                    {"MemTotal": 200000.0, "MemAvailable": 32000.0},
                    {"MemTotal": 200000.0, "MemAvailable": 100000.0},
                    {"MemTotal": 200000.0, "MemAvailable": 100000.0},
                ]
            ),
        )
        monitor = ResourceMonitor(
            pid=123,
            settings=_settings(recovery_hold_seconds=10.0),
            reader=reader,
            monotonic=iter([0.0, 1.0, 11.0]).__next__,
        )

        self.assertEqual(monitor.memory_stale_seconds, 10.0)
        self.assertEqual(monitor.snapshot()["state"], "constrained")
        self.assertEqual(monitor.snapshot()["state"], "constrained")
        self.assertEqual(monitor.snapshot()["state"], "ok")

        default_monitor = ResourceMonitor(
            pid=123,
            reader=_ResourceReader(),
            monotonic=lambda: 0.0,
        )
        self.assertEqual(default_monitor.sample_interval_seconds, 2.0)

    def test_snapshot_payload_and_successive_cpu_metrics_are_exact(self) -> None:
        reader = _ResourceReader(
            system_cpu=iter([(1000, 500), (1100, 540)]),
            process_cpu=iter([1.0, 1.2]),
            cpu_count=4,
        )
        monitor = ResourceMonitor(
            pid=123,
            settings=_settings(),
            reader=reader,
            monotonic=iter([10.0, 11.0]).__next__,
        )

        first = monitor.snapshot()
        second = monitor.snapshot()

        self.assertEqual(
            first,
            {
                "state": "ok",
                "loadavg_1m": 0.2,
                "loadavg_5m": 0.3,
                "loadavg_15m": 0.4,
                "load_per_cpu_1m": 0.05,
                "system_cpu_pct": 0.0,
                "mem_total_kb": 200000.0,
                "mem_available_kb": 100000.0,
                "mem_available_pct": 50.0,
                "memory_sample_status": "fresh",
                "memory_sample_age_s": 0.0,
                "process": {
                    "pid": 123,
                    "rss_kb": 10.0,
                    "rss_hwm_kb": 20.0,
                    "threads": 3,
                    "fd_size": 64,
                    "open_fds": 7,
                    "cpu_pct_one_core": 0.0,
                },
            },
        )
        self.assertEqual(second["system_cpu_pct"], 60.0)
        self.assertEqual(second["process"], _expected_process((1.2 - 1.0) * 100.0))

    def test_snapshot_reuses_a_copy_until_sample_interval_expires(self) -> None:
        reader = _ResourceReader(
            system_cpu=iter([(100, 50), (200, 100)]),
            process_cpu=iter([1.0, 2.0]),
        )
        monitor = ResourceMonitor(
            pid=123,
            settings=_settings(sample_interval_seconds=2.0),
            reader=reader,
            monotonic=iter([10.0, 11.0, 12.0]).__next__,
        )

        first = monitor.snapshot()
        first_process = first["process"]
        self.assertIsInstance(first_process, dict)
        assert isinstance(first_process, dict)
        first_process["rss_kb"] = -1.0
        cached = monitor.snapshot()
        refreshed = monitor.snapshot()

        cached_process = cached["process"]
        self.assertIsInstance(cached_process, dict)
        assert isinstance(cached_process, dict)
        self.assertEqual(cached_process["rss_kb"], 10.0)
        self.assertIsNot(cached, first)
        self.assertNotEqual(id(cached_process), id(first_process))
        self.assertEqual(refreshed["state"], "ok")
        self.assertEqual(reader.system_cpu_calls, 2)
        self.assertEqual(reader.meminfo_calls, 2)
        self.assertEqual(reader.process_status_calls, 2)

    def test_monitor_applies_pressure_latch_to_each_fresh_sample(self) -> None:
        reader = _ResourceReader(
            system_cpu=iter([(100, 50), (200, 100)]),
            process_cpu=iter([1.0, 2.0]),
            mem_available_kb=32000.0,
        )
        monitor = ResourceMonitor(
            pid=123,
            settings=_settings(recovery_hold_seconds=10.0),
            reader=reader,
            monotonic=iter([1.0, 2.0]).__next__,
        )

        self.assertEqual(monitor.snapshot()["state"], "constrained")
        self.assertEqual(monitor.snapshot()["state"], "constrained")

    def test_monitor_forwards_load_and_cpu_pressure_dimensions(self) -> None:
        load_monitor = ResourceMonitor(
            pid=123,
            settings=_settings(),
            reader=_ResourceReader(load_average=(2.0, 0.0, 0.0)),
            monotonic=lambda: 1.0,
        )
        self.assertEqual(load_monitor.snapshot()["state"], "constrained")

        cpu_monitor = ResourceMonitor(
            pid=123,
            settings=_settings(),
            reader=_ResourceReader(
                system_cpu=iter([(100, 50), (200, 50)]),
                process_cpu=iter([1.0, 1.0]),
                load_average=(0.0, 0.0, 0.0),
            ),
            monotonic=iter([1.0, 2.0]).__next__,
        )
        self.assertEqual(cpu_monitor.snapshot()["state"], "ok")
        self.assertEqual(cpu_monitor.snapshot()["state"], "constrained")

    def test_memory_failure_uses_bounded_cache_then_reports_unavailable(self) -> None:
        reader = _ResourceReader(
            system_cpu=iter(
                [
                    (100, 50),
                    (200, 100),
                    (300, 150),
                    (400, 200),
                    (500, 250),
                    (600, 300),
                ]
            ),
            process_cpu=iter([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
            meminfo=iter(
                [
                    {"MemTotal": 200000.0, "MemAvailable": 100000.0},
                    None,
                    None,
                    None,
                    None,
                    {"MemTotal": 200000.0, "MemAvailable": 90000.0},
                ]
            ),
        )
        monitor = ResourceMonitor(
            pid=123,
            settings=_settings(memory_stale_seconds=5.0),
            reader=reader,
            monotonic=iter([10.0, 10.0, 15.0, 16.0, 17.0, 18.0]).__next__,
        )

        fresh = monitor.snapshot()
        cached_at_zero = monitor.snapshot()
        cached_at_limit = monitor.snapshot()
        unavailable = monitor.snapshot()
        still_unavailable = monitor.snapshot()
        recovered = monitor.snapshot()

        self.assertEqual(fresh["memory_sample_status"], "fresh")
        self.assertEqual(cached_at_zero["memory_sample_status"], "cached")
        self.assertEqual(cached_at_zero["memory_sample_age_s"], 0.0)
        self.assertEqual(cached_at_limit["memory_sample_status"], "cached")
        self.assertEqual(cached_at_limit["memory_sample_age_s"], 5.0)
        self.assertEqual(cached_at_limit["mem_available_kb"], 100000.0)
        self.assertEqual(unavailable["state"], "ok")
        self.assertEqual(unavailable["memory_sample_status"], "unavailable")
        self.assertIsNone(unavailable["memory_sample_age_s"])
        self.assertIsNone(unavailable["mem_available_kb"])
        self.assertEqual(still_unavailable["memory_sample_status"], "unavailable")
        self.assertEqual(recovered["memory_sample_status"], "fresh")
        self.assertEqual(recovered["mem_available_kb"], 90000.0)

    def test_first_or_invalid_memory_sample_never_creates_false_pressure(self) -> None:
        for meminfo in (
            None,
            {},
            {"MemTotal": 100.0},
            {"MemAvailable": 50.0},
            {"MemTotal": 0.0, "MemAvailable": 0.0},
            {"MemTotal": 100.0, "MemAvailable": -1.0},
            {"MemTotal": 100.0, "MemAvailable": 101.0},
        ):
            with self.subTest(meminfo=meminfo):
                monitor = ResourceMonitor(
                    pid=123,
                    settings=_settings(),
                    reader=_ResourceReader(meminfo=iter([meminfo])),
                    monotonic=lambda: 1.0,
                )
                snapshot = monitor.snapshot()
                self.assertEqual(snapshot["state"], "ok")
                self.assertEqual(snapshot["memory_sample_status"], "unavailable")
                self.assertIsNone(snapshot["mem_available_kb"])

    def test_zero_and_full_available_memory_are_physical_samples(self) -> None:
        for available in (0.0, 1.0):
            with self.subTest(available=available):
                monitor = ResourceMonitor(
                    pid=123,
                    settings=_settings(),
                    reader=_ResourceReader(
                        meminfo=iter([{"MemTotal": 1.0, "MemAvailable": available}])
                    ),
                    monotonic=lambda: 1.0,
                )
                snapshot = monitor.snapshot()
                self.assertEqual(snapshot["memory_sample_status"], "fresh")
                self.assertEqual(snapshot["mem_available_kb"], available)

    def test_cpu_failure_requires_new_baseline_before_percentages_resume(self) -> None:
        reader = _ResourceReader(
            system_cpu=iter([(100, 50), None, (300, 150), (400, 190)]),
            process_cpu=iter([1.0, None, 3.0, 3.2]),
            cpu_count=1,
        )
        monitor = ResourceMonitor(
            pid=123,
            settings=_settings(),
            reader=reader,
            monotonic=iter([1.0, 2.0, 3.0, 4.0]).__next__,
        )

        first = monitor.snapshot()
        failed = monitor.snapshot()
        baseline = monitor.snapshot()
        measured = monitor.snapshot()

        self.assertEqual(first["system_cpu_pct"], 0.0)
        self.assertIsNone(failed["system_cpu_pct"])
        self.assertEqual(failed["process"], _expected_process(None))
        self.assertEqual(baseline["system_cpu_pct"], 0.0)
        self.assertEqual(baseline["process"], _expected_process(0.0))
        self.assertEqual(measured["system_cpu_pct"], 60.0)
        self.assertEqual(measured["process"], _expected_process((3.2 - 3.0) * 100.0))


if __name__ == "__main__":
    unittest.main()
