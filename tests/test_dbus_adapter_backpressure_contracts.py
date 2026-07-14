#!/usr/bin/env python3
"""Behavioral contracts for DBus adapter backpressure policy."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter_health_backpressure import (
    backpressure_reasons,
    backpressure_slo_reasons,
    backpressure_snapshot,
    backpressure_state,
    slo_violations,
)


class DbusAdapterBackpressureContractTests(unittest.TestCase):
    def test_slo_violations_accept_only_explicit_sequences(self) -> None:
        self.assertEqual(slo_violations({}), [])
        self.assertEqual(slo_violations({"violated": ["a", "b"]}), ["a", "b"])
        self.assertEqual(slo_violations({"violated": ("a", "b")}), ["a", "b"])
        self.assertEqual(slo_violations({"violated": {"a"}}), ["a"])
        for unsupported in (None, "a", 3, object()):
            with self.subTest(unsupported=unsupported):
                self.assertEqual(slo_violations({"violated": unsupported}), [])

    def test_slo_reasons_filter_to_backpressure_relevant_violations(self) -> None:
        slo = {
            "violated": [
                "gui_publish_fresh",
                "queue_age_ok",
                "core_reads_fresh",
                "eventloop_gap_ok",
            ]
        }
        self.assertEqual(backpressure_slo_reasons(slo), ["queue_age_ok", "core_reads_fresh"])

    def test_reasons_preserve_policy_order_and_strict_queue_boundary(self) -> None:
        self.assertEqual(
            backpressure_reasons("ok", 10.0, {}, queue_max_age_seconds=10.0),
            [],
        )
        self.assertEqual(
            backpressure_reasons(
                "degraded",
                10.01,
                {"violated": ["core_reads_fresh"]},
                queue_max_age_seconds=10.0,
            ),
            ["dbus-degraded", "queue-age", "core_reads_fresh"],
        )

    def test_state_thresholds_are_strict_and_protective_wins(self) -> None:
        limit = 10.0
        self.assertEqual(backpressure_state("protective", 0.0, [], queue_max_age_seconds=limit), "protective")
        self.assertEqual(backpressure_state("degraded", 0.0, [], queue_max_age_seconds=limit), "slow")
        self.assertEqual(backpressure_state("ok", 20.0, ["queue-age"], queue_max_age_seconds=limit), "congested")
        self.assertEqual(backpressure_state("ok", 20.01, [], queue_max_age_seconds=limit), "slow")
        self.assertEqual(backpressure_state("ok", 0.0, ["core_reads_fresh"], queue_max_age_seconds=limit), "congested")
        self.assertEqual(backpressure_state("ok", 0.0, [], queue_max_age_seconds=limit), "ok")

    def test_snapshot_exposes_exact_core_policy_for_each_state(self) -> None:
        cases = (
            (
                "ok",
                {"oldest_command_age_s": 0.0},
                {},
                {
                    "state": "ok",
                    "core_should_throttle": False,
                    "suppress_optional_commands": False,
                    "prefer_coalescing": False,
                    "reason": "ok",
                },
            ),
            (
                "ok",
                {"oldest_command_age_s": 11.0},
                {"violated": ["queue_age_ok", "queue_age_ok"]},
                {
                    "state": "congested",
                    "core_should_throttle": True,
                    "suppress_optional_commands": False,
                    "prefer_coalescing": True,
                    "reason": "queue-age,queue_age_ok",
                },
            ),
            (
                "degraded",
                {"oldest_command_age_s": "invalid"},
                {},
                {
                    "state": "slow",
                    "core_should_throttle": True,
                    "suppress_optional_commands": True,
                    "prefer_coalescing": True,
                    "reason": "dbus-degraded",
                },
            ),
            (
                "protective",
                {},
                {},
                {
                    "state": "protective",
                    "core_should_throttle": True,
                    "suppress_optional_commands": True,
                    "prefer_coalescing": True,
                    "reason": "dbus-protective",
                },
            ),
        )
        for circuit_state, queue_health, slo, expected in cases:
            with self.subTest(state=circuit_state, queue_health=queue_health):
                self.assertEqual(
                    backpressure_snapshot(
                        circuit_state=circuit_state,
                        queue_health=queue_health,
                        slo=slo,
                        queue_max_age_seconds=10.0,
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
