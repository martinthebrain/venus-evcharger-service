# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation contracts for DBus gateway queue and backpressure policy."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_gateway_policy import (
    command_allowed_by_backpressure,
    command_queue_class,
)
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
    REGISTER_COMPANION_KIND,
    REGISTER_EVCS_KIND,
)


class DbusGatewayPolicyMutationContracts(unittest.TestCase):
    def test_kind_is_required_and_type_is_not_a_command_alias(self) -> None:
        cases: list[tuple[dict[str, object], str]] = [
            ({"kind": "introspect", "type": "gx_relay_refresh"}, "introspection"),
            ({"kind": "", "type": "gx_relay_refresh"}, "diagnostic"),
            ({"kind": None, "type": "introspect"}, "diagnostic"),
            ({"kind": 0, "type": "ess_grid_setpoint"}, "diagnostic"),
            ({"type": "gx_relay_set_enabled"}, "diagnostic"),
            ({"kind": "unknown", "type": "introspect"}, "diagnostic"),
            ({"kind": " introspect "}, "diagnostic"),
            ({}, "diagnostic"),
        ]

        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(command_queue_class(command), expected)

    def test_publication_and_refresh_kinds_have_exact_subclasses(self) -> None:
        cases: list[tuple[dict[str, object], str]] = [
            (
                {
                    "kind": PUBLISH_EVCS_FIELDS_KIND,
                    "publication_priority": "critical",
                },
                "gui-critical-publish",
            ),
            (
                {
                    "kind": PUBLISH_COMPANION_FIELDS_KIND,
                    "publication_priority": "critical",
                },
                "gui-critical-publish",
            ),
            (
                {
                    "kind": PUBLISH_EVCS_FIELDS_KIND,
                    "publication_priority": "Critical",
                },
                "local-publish",
            ),
            ({"kind": PUBLISH_EVCS_FIELDS_KIND}, "local-publish"),
            (
                {
                    "kind": "refresh_energy_inputs",
                    "scope": "topology",
                },
                "discovery",
            ),
            (
                {
                    "kind": "refresh_energy_inputs",
                    "scope": "Topology",
                },
                "read-fast",
            ),
            ({"kind": "refresh_energy_inputs", "scope": None}, "read-fast"),
        ]

        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(command_queue_class(command), expected)

    def test_registration_is_always_admitted(self) -> None:
        for kind in (REGISTER_EVCS_KIND, REGISTER_COMPANION_KIND):
            command: dict[str, object] = {
                "kind": kind,
                "priority": "diagnostic",
            }
            for state in ("ok", "congested", "slow", "protective", "unknown", ""):
                with self.subTest(kind=kind, state=state):
                    self.assertTrue(command_allowed_by_backpressure(command, state))

    def test_state_and_priority_are_trimmed_and_case_normalized(self) -> None:
        user_write: dict[str, object] = {
            "kind": "gx_relay_set_enabled",
            "priority": " USER ",
        }
        safety_write: dict[str, object] = {
            "kind": "gx_relay_set_enabled",
            "priority": " SaFeTy ",
        }
        diagnostic_work: dict[str, object] = {
            "kind": "unknown",
            "priority": " DIAGNOSTIC ",
        }

        self.assertTrue(command_allowed_by_backpressure(user_write, " SLOW "))
        self.assertTrue(command_allowed_by_backpressure(user_write, " DEGRADED "))
        self.assertTrue(command_allowed_by_backpressure(safety_write, " PROTECTIVE "))
        self.assertTrue(command_allowed_by_backpressure(diagnostic_work, " OK "))
        self.assertFalse(command_allowed_by_backpressure(diagnostic_work, " mystery "))

    def test_missing_or_false_priority_is_diagnostic(self) -> None:
        for priority in (None, "", 0, False):
            command: dict[str, object] = {
                "kind": "gx_relay_set_enabled",
                "priority": priority,
            }
            with self.subTest(priority=priority):
                self.assertFalse(command_allowed_by_backpressure(command, "slow"))
                self.assertFalse(command_allowed_by_backpressure(command, "congested"))

        self.assertFalse(
            command_allowed_by_backpressure(
                {"kind": "gx_relay_set_enabled"},
                "slow",
            )
        )
        self.assertFalse(
            command_allowed_by_backpressure(
                {"kind": "gx_relay_set_enabled"},
                "congested",
            )
        )

    def test_congested_state_blocks_optional_and_diagnostic_work_only(self) -> None:
        cases: list[tuple[str, str, bool]] = [
            ("optional", "remote-write", False),
            ("diagnostic", "remote-write", False),
            ("user", "diagnostic", False),
            ("normal", "diagnostic", False),
            ("user", "remote-write", True),
            ("safety", "read-fast", True),
            ("normal", "read-fast", True),
        ]

        for priority, queue_class, expected in cases:
            command: dict[str, object] = {
                "kind": "unknown",
                "priority": priority,
                "queue_class": queue_class,
            }
            with self.subTest(priority=priority, queue_class=queue_class):
                self.assertEqual(
                    command_allowed_by_backpressure(command, "congested"),
                    expected,
                )

    def test_slow_state_admits_critical_publication_or_user_intent(self) -> None:
        cases: list[tuple[str, str, bool]] = [
            ("diagnostic", "gui-critical-publish", True),
            ("normal", "gui-critical-publish", True),
            ("safety", "configuration", True),
            ("user", "remote-write", True),
            ("publish", "local-publish", False),
            ("normal", "read-fast", False),
            ("diagnostic", "diagnostic", False),
        ]

        for priority, queue_class, expected in cases:
            command: dict[str, object] = {
                "kind": "unknown",
                "priority": priority,
                "queue_class": queue_class,
            }
            with self.subTest(priority=priority, queue_class=queue_class):
                self.assertEqual(
                    command_allowed_by_backpressure(command, "slow"),
                    expected,
                )

    def test_protective_state_requires_intent_and_permitted_queue_class(self) -> None:
        cases: list[tuple[str, str, bool]] = [
            ("safety", "gui-critical-publish", True),
            ("user", "gui-critical-publish", True),
            ("safety", "remote-write", True),
            ("user", "remote-write", True),
            ("normal", "gui-critical-publish", False),
            ("diagnostic", "remote-write", False),
            ("safety", "configuration", False),
            ("user", "introspection", False),
        ]

        for priority, queue_class, expected in cases:
            command: dict[str, object] = {
                "kind": "unknown",
                "priority": priority,
                "queue_class": queue_class,
            }
            with self.subTest(priority=priority, queue_class=queue_class):
                self.assertEqual(
                    command_allowed_by_backpressure(command, "protective"),
                    expected,
                )

    def test_unknown_and_empty_states_fail_closed_as_slow(self) -> None:
        critical: dict[str, object] = {
            "kind": PUBLISH_EVCS_FIELDS_KIND,
            "publication_priority": "critical",
            "priority": "safety",
        }
        routine: dict[str, object] = {
            "kind": "refresh_energy_inputs",
            "priority": "normal",
        }

        for state in ("", "unknown", " healthy "):
            with self.subTest(state=state):
                self.assertTrue(command_allowed_by_backpressure(critical, state))
                self.assertFalse(command_allowed_by_backpressure(routine, state))


if __name__ == "__main__":
    unittest.main()
