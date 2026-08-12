#!/usr/bin/env python3
"""Behavioral contracts for DBus adapter queue health metrics."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.health.queue import (
    command_activity_at,
    critical_queue_operation_count,
    oldest_command_age,
    oldest_slo_command_age,
    physical_command_count_from_pending,
    queue_class_health,
    queue_health,
)
from venus_evcharger.ipc.command_types import CommandFileList
from venus_evcharger.ipc.energy import EnergyRefreshRequest
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command


def pending_commands() -> CommandFileList:
    gui_publication = publish_evcs_fields_command({"mode": 1}, priority="critical")
    gui_publication["created_at"] = 98.0
    fast_refresh = EnergyRefreshRequest(
        "health-grid",
        "grid",
        0.0,
        urgency="priority",
    ).to_command(source="health-test")
    fast_refresh["created_at"] = 96.0
    return [
        ("slow-old.json", {"queue_class": "read-slow", "created_at": 90.0}),
        ("slow-updated.json", {"queue_class": "read-slow", "created_at": 1.0, "updated_at": 97.0}),
        ("remote.json", {"queue_class": "remote-write", "created_at": 95.0}),
        ("fast-fallback.json", fast_refresh),
        ("gui-fallback.json", gui_publication),
    ]


class DbusAdapterHealthQueueContractTests(unittest.TestCase):
    def test_critical_count_includes_only_valid_user_facing_work(self) -> None:
        self.assertEqual(
            critical_queue_operation_count(
                {
                    "startup/register": {"pending": "2"},
                    "gui-critical-publish": {"pending": 1},
                    "local-publish": {"pending": 3},
                    "remote-write": {"pending": 4},
                    "read-fast": {"pending": 5},
                    "read-slow": {"pending": 99},
                    "diagnostic": {"pending": 99},
                }
            ),
            15,
        )
        self.assertEqual(
            critical_queue_operation_count(
                {
                    "startup/register": {"pending": -2},
                    "gui-critical-publish": {"pending": "invalid"},
                    "local-publish": "invalid",
                }
            ),
            0,
        )

    def test_activity_prefers_present_updated_at_and_defaults_to_now(self) -> None:
        self.assertEqual(command_activity_at({"created_at": 1.0, "updated_at": 99.0}, 100.0), 99.0)
        self.assertEqual(command_activity_at({"created_at": 98.0, "updated_at": None}, 100.0), 98.0)
        self.assertEqual(command_activity_at({"created_at": "bad"}, 100.0), 100.0)
        self.assertEqual(command_activity_at({}, 100.0), 100.0)
        self.assertEqual(command_activity_at({"updated_at": 0.5}, 1.0), 0.5)
        self.assertEqual(command_activity_at({"updated_at": 101.0}, 100.0), 101.0)

    def test_oldest_age_clamps_future_commands_and_handles_empty_queue(self) -> None:
        self.assertEqual(oldest_command_age(pending_commands(), 100.0), 10.0)
        self.assertEqual(oldest_command_age([("future.json", {"created_at": 101.0})], 100.0), 0.0)
        self.assertEqual(oldest_command_age([("tiny.json", {"created_at": 99.5})], 100.0), 0.5)
        self.assertEqual(oldest_command_age([("invalid.json", {"created_at": "bad"})], 100.0), 0.0)
        self.assertEqual(oldest_command_age([], 100.0), 0.0)

    def test_slo_age_excludes_advisory_work_but_preserves_actionable_age(self) -> None:
        pending = pending_commands() + [
            ("introspection.json", {"queue_class": "introspection", "created_at": 5.0}),
            ("discovery.json", {"queue_class": "discovery", "created_at": 10.0}),
            ("diagnostic.json", {"queue_class": "diagnostic", "created_at": 15.0}),
        ]
        self.assertEqual(oldest_command_age(pending, 100.0), 95.0)
        self.assertEqual(oldest_slo_command_age(pending, 100.0), 10.0)
        self.assertEqual(oldest_slo_command_age(pending[-3:], 100.0), 0.0)

    def test_physical_count_distinguishes_files_from_coalesced_commands(self) -> None:
        pending = pending_commands()
        self.assertEqual(physical_command_count_from_pending(pending, None), 5)
        self.assertEqual(physical_command_count_from_pending(pending, 7), 7)
        self.assertEqual(physical_command_count_from_pending(pending, 0), 0)

    def test_queue_class_health_groups_sorts_and_uses_oldest_activity(self) -> None:
        self.assertEqual(
            queue_class_health(pending_commands(), 100.0),
            {
                "gui-critical-publish": {"pending": 1, "oldest_age_s": 2.0},
                "read-fast": {"pending": 1, "oldest_age_s": 4.0},
                "read-slow": {"pending": 2, "oldest_age_s": 10.0},
                "remote-write": {"pending": 1, "oldest_age_s": 5.0},
            },
        )
        self.assertEqual(queue_class_health([], 100.0), {})
        self.assertEqual(
            queue_class_health([("future.json", {"queue_class": "diagnostic", "created_at": 101.0})], 100.0),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.0}},
        )
        self.assertEqual(
            queue_class_health([("bad.json", {"queue_class": "diagnostic", "created_at": "bad"})], 100.0),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.0}},
        )

    def test_queue_health_reports_exact_counts_ages_and_scheduler_rate(self) -> None:
        pending = pending_commands()
        core_pending: CommandFileList = [
            ("core.json", {"created_at": 80.0}),
            ("core-updated.json", {"created_at": 1.0, "updated_at": 99.0}),
        ]
        self.assertEqual(
            queue_health(
                pending,
                core_pending,
                100.0,
                physical_count=7,
                write_scheduler_health={"processed_commands_60s": "30", "last_processed_at": "88.5"},
            ),
            {
                "pending_command_count": 5,
                "physical_command_count": 7,
                "oldest_command_age_s": 10.0,
                "oldest_slo_command_age_s": 10.0,
                "core_command_count": 2,
                "oldest_core_command_age_s": 20.0,
                "processed_commands_60s": 30,
                "queue_drain_rate_per_s": 0.5,
                "last_processed_at": 88.5,
            },
        )

    def test_queue_health_defaults_invalid_or_missing_scheduler_metrics(self) -> None:
        pending = pending_commands()
        expected = {
            "pending_command_count": 5,
            "physical_command_count": 5,
            "oldest_command_age_s": 10.0,
            "oldest_slo_command_age_s": 10.0,
            "core_command_count": 0,
            "oldest_core_command_age_s": 0.0,
            "processed_commands_60s": 0,
            "queue_drain_rate_per_s": 0.0,
            "last_processed_at": 0.0,
        }
        self.assertEqual(queue_health(pending, [], 100.0), expected)
        self.assertEqual(
            queue_health(
                pending,
                [],
                100.0,
                write_scheduler_health={"processed_commands_60s": "bad", "last_processed_at": object()},
            ),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
