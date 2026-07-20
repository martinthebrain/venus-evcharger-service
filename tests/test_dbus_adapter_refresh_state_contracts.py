# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for completing coalesced gateway refresh requests."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.refresh_state import (
    cached_refresh_outcome,
    command_request_at,
    services_refresh_satisfied,
)


class DbusAdapterRefreshStateContracts(unittest.TestCase):
    def test_command_request_timestamp_prefers_a_valid_update(self) -> None:
        self.assertEqual(command_request_at({"created_at": 10.0}), 10.0)
        self.assertEqual(command_request_at({"created_at": 0.5}), 0.5)
        self.assertEqual(command_request_at({"created_at": 10.0, "updated_at": "12.5"}), 12.5)
        self.assertEqual(command_request_at({"created_at": 10.0, "updated_at": None}), 10.0)
        for invalid in (
            None,
            object(),
            "bad",
            0.0,
            -1.0,
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            self.assertEqual(command_request_at({"created_at": invalid}), 0.0)

    def test_cache_completion_requires_a_terminal_event_after_the_request(self) -> None:
        command = {"created_at": 10.0}
        self.assertIsNone(cached_refresh_outcome(None, command))
        self.assertIsNone(cached_refresh_outcome({"status": "fresh", "confirmed_at": 20.0}, {}))
        self.assertEqual(
            cached_refresh_outcome({"status": "fresh", "confirmed_at": 10.0}, command),
            "applied",
        )
        self.assertEqual(
            cached_refresh_outcome(
                {"status": "fresh", "confirmed_at": 0.5},
                {"created_at": 0.5},
            ),
            "applied",
        )
        self.assertIsNone(
            cached_refresh_outcome({"status": "stale", "confirmed_at": 20.0}, command)
        )
        self.assertEqual(
            cached_refresh_outcome({"status": "error", "error_at": 20.0}, command),
            "dropped",
        )
        self.assertEqual(
            cached_refresh_outcome({"status": "unavailable", "error_at": 20.0}, command),
            "dropped",
        )
        self.assertIsNone(
            cached_refresh_outcome({"status": "fresh", "error_at": 20.0}, command)
        )
        self.assertIsNone(
            cached_refresh_outcome(
                {"status": "error", "confirmed_at": "bad", "error_at": object()},
                command,
            )
        )

    def test_service_completion_requires_a_newer_discovery_snapshot(self) -> None:
        services = {"svc": {"seen_at": 20.0}}
        self.assertTrue(services_refresh_satisfied(services, {"created_at": 10.0}))
        self.assertTrue(
            services_refresh_satisfied({"svc": {"seen_at": 0.5}}, {"created_at": 0.5})
        )
        self.assertTrue(
            services_refresh_satisfied(
                {"old": {"seen_at": 5.0}, "new": {"seen_at": 10.0}},
                {"created_at": 10.0},
            )
        )
        self.assertFalse(
            services_refresh_satisfied(services, {"created_at": 10.0, "updated_at": 25.0})
        )
        self.assertFalse(services_refresh_satisfied({}, {"created_at": 10.0}))
        self.assertFalse(services_refresh_satisfied({"svc": {"seen_at": "bad"}}, {"created_at": 10.0}))
        self.assertFalse(services_refresh_satisfied(services, {}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
