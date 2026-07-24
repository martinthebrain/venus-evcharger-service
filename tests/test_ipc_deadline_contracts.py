#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for transient command deadlines."""

from __future__ import annotations

import unittest

from venus_evcharger.ipc.deadline import (
    COMMAND_DEADLINE_FUTURE_SKEW_SECONDS,
    TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
    command_deadline_expired,
    deadline_pair,
    normalized_transient_deadline,
    remaining_transient_ttl,
    valid_deadline_anchor,
)


class CommandDeadlineContracts(unittest.TestCase):
    def test_pair_and_normalization_are_exact(self) -> None:
        self.assertEqual(
            deadline_pair({"deadline_s": "2.5", "created_at": "4"}),
            (2.5, 4.0),
        )
        for invalid in (None, 0.0, -1.0, float("nan"), float("inf"), "invalid"):
            with self.subTest(value=invalid):
                self.assertEqual(
                    normalized_transient_deadline(invalid),
                    TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
                )
        self.assertEqual(normalized_transient_deadline(4.5), 4.5)
        self.assertEqual(
            normalized_transient_deadline(TRANSIENT_PUBLICATION_DEADLINE_SECONDS + 1.0),
            TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
        )

    def test_deadline_free_commands_remain_untouched(self) -> None:
        for priority in ("safety", "user"):
            with self.subTest(priority=priority):
                self.assertFalse(
                    command_deadline_expired(
                        {"priority": priority, "created_at": float("nan")},
                        100.0,
                    )
                )
        self.assertFalse(command_deadline_expired({"deadline_s": 0.0}, 100.0))
        self.assertFalse(command_deadline_expired({"deadline_s": -1.0}, 100.0))

    def test_positive_deadline_requires_a_valid_anchor(self) -> None:
        invalid_anchors: tuple[dict[str, object], ...] = (
            {},
            {"created_at": 0.0},
            {"created_at": -1.0},
            {"created_at": float("nan")},
            {"created_at": float("inf")},
        )
        for anchor in invalid_anchors:
            with self.subTest(anchor=anchor):
                self.assertTrue(
                    command_deadline_expired(
                        {"deadline_s": 1.0, **anchor},
                        100.0,
                    )
                )
        self.assertTrue(
            command_deadline_expired(
                {"deadline_s": float("nan"), "created_at": 100.0},
                100.0,
            )
        )
        self.assertTrue(
            command_deadline_expired(
                {"deadline_s": float("inf"), "created_at": 100.0},
                100.0,
            )
        )

    def test_future_skew_and_elapsed_boundaries_are_exact(self) -> None:
        skew = COMMAND_DEADLINE_FUTURE_SKEW_SECONDS
        self.assertTrue(valid_deadline_anchor(100.0 + skew, 100.0))
        self.assertFalse(valid_deadline_anchor(100.0 + skew + 0.001, 100.0))
        self.assertFalse(
            command_deadline_expired(
                {"deadline_s": 1.0, "created_at": 100.0 + skew},
                100.0,
            )
        )
        self.assertTrue(
            command_deadline_expired(
                {"deadline_s": 1.0, "created_at": 100.0 + skew + 0.001},
                100.0,
            )
        )
        self.assertFalse(
            command_deadline_expired(
                {"deadline_s": 1.0, "created_at": 99.0},
                100.0,
            )
        )
        self.assertTrue(
            command_deadline_expired(
                {"deadline_s": 1.0, "created_at": 99.0},
                100.001,
            )
        )

    def test_remaining_ttl_is_bounded_and_fails_closed(self) -> None:
        maximum = TRANSIENT_PUBLICATION_DEADLINE_SECONDS
        self.assertEqual(remaining_transient_ttl({}, 100.0), maximum)
        self.assertEqual(remaining_transient_ttl({"deadline_s": 5.0}, 100.0), 0.0)
        self.assertEqual(
            remaining_transient_ttl(
                {"deadline_s": 5.0, "created_at": 99.0},
                100.0,
            ),
            4.0,
        )
        self.assertEqual(
            remaining_transient_ttl(
                {
                    "deadline_s": maximum,
                    "created_at": 100.0 + COMMAND_DEADLINE_FUTURE_SKEW_SECONDS,
                },
                100.0,
            ),
            maximum,
        )
        self.assertEqual(
            remaining_transient_ttl(
                {
                    "deadline_s": maximum,
                    "created_at": 100.0 + COMMAND_DEADLINE_FUTURE_SKEW_SECONDS + 0.001,
                },
                100.0,
            ),
            0.0,
        )
        self.assertEqual(
            remaining_transient_ttl(
                {"deadline_s": 5.0, "created_at": 95.0},
                100.0,
            ),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
