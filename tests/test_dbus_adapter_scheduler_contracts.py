# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact read-scheduler, discovery, and JSON-writer contracts."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.dbus_adapter.scheduling import (
    AtomicJsonWriter,
    DbusDiscoveryManager,
    DbusReadScheduler,
    _interval_seconds,
)


class TestDbusAdapterSchedulerContracts(unittest.TestCase):
    def test_scheduler_initial_state_and_due_at_zero(self) -> None:
        scheduler = DbusReadScheduler(
            {
                "grid": {"interval": 4.0, "priority": "user"},
                "pv": {"interval": 7.0},
            }
        )
        self.assertEqual(scheduler.next_read_at, {"grid": 0.0, "pv": 0.0})
        self.assertEqual(scheduler.failure_counts, {"grid": 0, "pv": 0})
        self.assertEqual(scheduler._order, {"grid": 0, "pv": 1})
        priorities: list[str] = []
        due = scheduler.next_due(
            monotonic_at=0.0,
            circuit_state="ok",
            priority_allowed=lambda priority: priorities.append(priority) or True,
        )
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual((due[0], due[2]), ("grid", 4.0))
        self.assertEqual(priorities, ["user"])

    def test_next_due_sorts_by_time_then_declared_order(self) -> None:
        scheduler = DbusReadScheduler(
            {"first": {"interval": 1.0}, "second": {"interval": 2.0}}
        )
        scheduler.next_read_at.update({"first": 10.0, "second": 5.0})
        due = scheduler.next_due(
            monotonic_at=10.0,
            circuit_state="ok",
            priority_allowed=lambda _priority: True,
        )
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due[0], "second")
        scheduler.next_read_at.update({"first": 5.0, "second": 5.0})
        tied = scheduler.next_due(
            monotonic_at=10.0,
            circuit_state="ok",
            priority_allowed=lambda _priority: True,
        )
        self.assertIsNotNone(tied)
        assert tied is not None
        self.assertEqual(tied[0], "first")
        self.assertIsNone(
            scheduler.next_due(
                monotonic_at=4.999,
                circuit_state="ok",
                priority_allowed=lambda _priority: True,
            )
        )

    def test_missing_priority_defaults_to_read_and_block_stops_selection(self) -> None:
        scheduler = DbusReadScheduler(
            {"first": {"interval": 1.0}, "second": {"interval": 1.0, "priority": "user"}}
        )
        priorities: list[str] = []
        result = scheduler.next_due(
            monotonic_at=0.0,
            circuit_state="ok",
            priority_allowed=lambda priority: priorities.append(priority) or False,
        )
        self.assertIsNone(result)
        self.assertEqual(priorities, ["read"])

    def test_success_force_due_and_error_backoff_are_exact(self) -> None:
        scheduler = DbusReadScheduler({"grid": {"interval": 4.0}})
        scheduler.record_success("grid", monotonic_at=10.0, interval=-2.0)
        self.assertEqual(scheduler.failure_counts["grid"], 0)
        self.assertEqual(scheduler.next_read_at["grid"], 10.0)
        scheduler.next_read_at["grid"] = 99.0
        scheduler.force_due(["missing", "grid"])
        self.assertEqual(scheduler.next_read_at, {"grid": 0.0})
        scheduler.next_read_at["grid"] = 42.0
        scheduler.failure_counts["grid"] = 1
        scheduler.expedite_healthy(["missing", "grid"])
        self.assertEqual(scheduler.next_read_at, {"grid": 42.0})
        scheduler.failure_counts["grid"] = 0
        scheduler.expedite_healthy(["grid"])
        self.assertEqual(scheduler.next_read_at, {"grid": 0.0})
        scheduler.record_success("grid", monotonic_at=10.0, interval=4.0)
        self.assertEqual(scheduler.next_read_at["grid"], 14.0)
        scheduler.record_success(
            "grid",
            monotonic_at=20.0,
            interval=4.0,
            interval_factor=3.0,
        )
        self.assertEqual(scheduler.next_read_at["grid"], 32.0)
        scheduler.record_success(
            "grid",
            monotonic_at=20.0,
            interval=4.0,
            interval_factor=0.5,
        )
        self.assertEqual(scheduler.next_read_at["grid"], 24.0)

        for expected_failure, expected_due in (
            (1, 50.0),
            (2, 90.0),
            (3, 170.0),
            (4, 310.0),
            (5, 310.0),
            (6, 310.0),
            (6, 310.0),
        ):
            scheduler.record_error("grid", monotonic_at=10.0, interval=4.0)
            self.assertEqual(scheduler.failure_counts["grid"], expected_failure)
            self.assertEqual(scheduler.next_read_at["grid"], expected_due)
        scheduler.record_error("new", monotonic_at=0.0, interval=4.0)
        self.assertEqual(scheduler.failure_counts["new"], 1)
        self.assertEqual(scheduler.next_read_at["new"], 40.0)
        floor = DbusReadScheduler({"grid": {"interval": 2.0}})
        floor.record_error("grid", monotonic_at=10.0, interval=2.0)
        self.assertEqual(floor.next_read_at["grid"], 40.0)

    def test_effective_intervals_and_raw_interval_contract(self) -> None:
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": 7.0}, "ok"), 7.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": 7.0}, "degraded"), 21.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": 7.0}, "protective"), 35.0)
        self.assertEqual(DbusReadScheduler.effective_interval({}, "ok"), 2.0)
        for value, expected in ((True, 2.0), ("4.5", 4.5), ("bad", 2.0), (None, 2.0), (object(), 2.0)):
            self.assertEqual(_interval_seconds(value), expected)
        scheduler = DbusReadScheduler({"grid": {"interval": 7.0}})
        due = scheduler.next_due(
            monotonic_at=0.0,
            circuit_state="protective",
            priority_allowed=lambda _priority: True,
        )
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due[2], 35.0)

    def test_discovery_lifecycle_and_priority_contract(self) -> None:
        discovery = DbusDiscoveryManager(
            interval_seconds=0.0,
            missing_pv_interval_seconds=1.0,
        )
        self.assertEqual(discovery.interval_seconds, 5.0)
        self.assertEqual(discovery.missing_pv_interval_seconds, 5.0)
        self.assertEqual(discovery.next_scan_monotonic, 0.0)
        self.assertEqual(discovery.next_scan_at, 0.0)
        self.assertEqual(discovery.last_success_at, 0.0)
        self.assertEqual(discovery.last_error, "")
        priorities: list[str] = []
        self.assertTrue(
            discovery.due(
                monotonic_at=0.0,
                priority_allowed=lambda priority: priorities.append(priority) or True,
            )
        )
        self.assertEqual(priorities, ["discovery"])
        discovery.record_success(
            monotonic_at=10.0,
            captured_at=1000.0,
            needs_early_rescan=False,
        )
        self.assertEqual(discovery.last_success_at, 1000.0)
        self.assertEqual(discovery.last_error, "")
        self.assertEqual(discovery.next_scan_monotonic, 15.0)
        self.assertEqual(discovery.next_scan_at, 1005.0)
        self.assertFalse(
            discovery.due(
                monotonic_at=14.999,
                priority_allowed=lambda _priority: True,
            )
        )
        self.assertTrue(
            discovery.due(
                monotonic_at=15.0,
                priority_allowed=lambda _priority: True,
            )
        )
        discovery.record_error(
            RuntimeError("offline"),
            monotonic_at=20.0,
            captured_at=5000.0,
        )
        self.assertEqual(discovery.last_error, "offline")
        self.assertEqual(discovery.next_scan_monotonic, 25.0)
        self.assertEqual(discovery.next_scan_at, 5005.0)

        slow = DbusDiscoveryManager(
            interval_seconds=900.0,
            missing_pv_interval_seconds=60.0,
        )
        slow.record_success(
            monotonic_at=20.0,
            captured_at=2000.0,
            needs_early_rescan=True,
        )
        self.assertEqual(slow.next_scan_monotonic, 80.0)
        self.assertEqual(slow.next_scan_at, 2060.0)
        self.assertEqual(slow.active_interval_seconds, 60.0)
        slow.record_error(
            ValueError("bad"),
            monotonic_at=100.0,
            captured_at=10.0,
        )
        self.assertEqual(slow.next_scan_monotonic, 160.0)
        self.assertEqual(slow.next_scan_at, 70.0)
        slow.defer_for(monotonic_at=120.0, captured_at=20.0, seconds=90.0)
        self.assertEqual(slow.next_scan_monotonic, 160.0)
        self.assertEqual(slow.next_scan_at, 70.0)
        slow.defer_for(monotonic_at=160.0, captured_at=70.0, seconds=90.0)
        self.assertEqual(slow.next_scan_monotonic, 250.0)
        self.assertEqual(slow.next_scan_at, 160.0)
        slow.force_due()
        self.assertEqual(slow.next_scan_monotonic, 0.0)
        self.assertEqual(slow.next_scan_at, 0.0)

        immediate = DbusDiscoveryManager(
            interval_seconds=900.0,
            missing_pv_interval_seconds=60.0,
        )
        immediate.defer_for(
            monotonic_at=7.0,
            captured_at=70.0,
            seconds=-2.0,
        )
        self.assertEqual(immediate.next_scan_monotonic, 7.0)
        self.assertEqual(immediate.next_scan_at, 70.0)

    def test_atomic_json_writer_delegates_exact_payload(self) -> None:
        payload = {"kind": "set_value", "value": 2}
        with patch(
            "venus_evcharger.dbus_adapter.scheduling.write_json_file"
        ) as write_json_file:
            self.assertIsNone(AtomicJsonWriter().write("/tmp/command.json", payload))
        write_json_file.assert_called_once_with("/tmp/command.json", payload)


if __name__ == "__main__":
    unittest.main()
