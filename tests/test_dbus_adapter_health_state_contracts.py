#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for stable gateway health verdicts."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.health.state import (
    GatewayHealthStateLatch,
    higher_health_state,
    operational_health_state,
    performance_health_state,
)


class GatewayHealthStateContractsTests(unittest.TestCase):
    def test_component_states_are_normalized_and_ranked(self) -> None:
        self.assertEqual(operational_health_state("ok"), "ok")
        self.assertEqual(operational_health_state("unknown"), "ok")
        self.assertEqual(operational_health_state("degraded"), "degraded")
        self.assertEqual(operational_health_state("protective"), "protective")
        self.assertEqual(higher_health_state("ok", "degraded"), "degraded")
        self.assertEqual(
            higher_health_state("protective", "degraded"),
            "protective",
        )

    def test_performance_state_uses_existing_slo_and_pressure_evidence(self) -> None:
        cases = (
            ("ok", "ok", "ok", "ok"),
            ("violated", "ok", "ok", "degraded"),
            ("ok", "busy", "ok", "ok"),
            ("ok", "ok", "congested", "degraded"),
            ("ok", "ok", "slow", "degraded"),
            ("ok", "constrained", "ok", "protective"),
            ("ok", "ok", "protective", "protective"),
        )
        for slo, resources, backpressure, expected in cases:
            with self.subTest(
                slo=slo,
                resources=resources,
                backpressure=backpressure,
            ):
                self.assertEqual(
                    performance_health_state(
                        slo_state=slo,
                        resource_state=resources,
                        backpressure_state=backpressure,
                    ),
                    expected,
                )

    def test_latch_escalates_immediately_and_recovers_after_stable_hold(self) -> None:
        latch = GatewayHealthStateLatch(recovery_hold_seconds=2.0)
        self.assertFalse(latch.recovery_pending)
        initial = latch.observe(
            "ok",
            "ok",
            monotonic_at=1.0,
            captured_at=10.0,
        )
        self.assertEqual((initial.state, initial.changed_at), ("ok", 0.0))
        self.assertFalse(initial.recovery_pending)

        degraded = latch.observe(
            "degraded",
            "ok",
            monotonic_at=2.0,
            captured_at=11.0,
        )
        self.assertEqual((degraded.state, degraded.changed_at), ("degraded", 11.0))

        protective = latch.observe(
            "degraded",
            "protective",
            monotonic_at=3.0,
            captured_at=12.0,
        )
        self.assertEqual(
            (protective.state, protective.changed_at),
            ("protective", 12.0),
        )

        pending_degraded = latch.observe(
            "degraded",
            "ok",
            monotonic_at=4.0,
            captured_at=13.0,
        )
        self.assertTrue(pending_degraded.recovery_pending)
        self.assertEqual(pending_degraded.state, "protective")

        pending_ok = latch.observe(
            "ok",
            "ok",
            monotonic_at=4.5,
            captured_at=14.0,
        )
        self.assertTrue(pending_ok.recovery_pending)
        still_pending = latch.observe(
            "ok",
            "ok",
            monotonic_at=6.49,
            captured_at=14.5,
        )
        self.assertEqual(still_pending.state, "protective")

        recovered = latch.observe(
            "ok",
            "ok",
            monotonic_at=6.5,
            captured_at=15.0,
        )
        self.assertEqual((recovered.state, recovered.changed_at), ("ok", 15.0))
        self.assertFalse(recovered.recovery_pending)

        unchanged = latch.observe(
            "ok",
            "ok",
            monotonic_at=7.0,
            captured_at=16.0,
        )
        self.assertEqual(unchanged.changed_at, 15.0)

    def test_zero_hold_and_epoch_outputs_are_clamped(self) -> None:
        latch = GatewayHealthStateLatch(recovery_hold_seconds=-1.0)
        escalated = latch.observe(
            "degraded",
            "ok",
            monotonic_at=-1.0,
            captured_at=-2.0,
        )
        self.assertEqual(escalated.changed_at, 0.0)
        recovered = latch.observe(
            "ok",
            "ok",
            monotonic_at=-1.0,
            captured_at=-3.0,
        )
        self.assertEqual(recovered.state, "ok")
        self.assertEqual(recovered.changed_at, 0.0)
        self.assertFalse(recovered.recovery_pending)

    def test_default_recovery_hold_is_exactly_ten_seconds(self) -> None:
        latch = GatewayHealthStateLatch()
        latch.observe(
            "protective",
            "ok",
            monotonic_at=10.0,
            captured_at=20.0,
        )
        latch.observe(
            "ok",
            "ok",
            monotonic_at=20.0,
            captured_at=21.0,
        )
        pending = latch.observe(
            "ok",
            "ok",
            monotonic_at=29.999,
            captured_at=22.0,
        )
        self.assertEqual(pending.state, "protective")
        recovered = latch.observe(
            "ok",
            "ok",
            monotonic_at=30.0,
            captured_at=23.0,
        )
        self.assertEqual(recovered.state, "ok")

    def test_recovery_clock_accepts_zero_as_monotonic_origin(self) -> None:
        latch = GatewayHealthStateLatch(recovery_hold_seconds=2.0)
        latch.observe(
            "degraded",
            "ok",
            monotonic_at=-1.0,
            captured_at=10.0,
        )
        latch.observe(
            "ok",
            "ok",
            monotonic_at=0.0,
            captured_at=11.0,
        )
        pending = latch.observe(
            "ok",
            "ok",
            monotonic_at=1.999,
            captured_at=12.0,
        )
        self.assertEqual(pending.state, "degraded")
        recovered = latch.observe(
            "ok",
            "ok",
            monotonic_at=2.0,
            captured_at=13.0,
        )
        self.assertEqual(recovered.state, "ok")


if __name__ == "__main__":
    unittest.main()
