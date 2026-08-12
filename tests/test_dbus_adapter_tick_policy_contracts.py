#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure contracts for adaptive DBus gateway cadence."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.tick_policy import (
    TickDemand,
    TickPolicy,
    adaptive_tick_seconds,
)


POLICY = TickPolicy(
    min_tick_seconds=0.2,
    max_tick_seconds=1.0,
    core_read_slo_seconds=5.0,
    queue_slo_seconds=10.0,
)


class DbusAdapterTickPolicyContracts(unittest.TestCase):
    def test_demand_count_normalizes_negative_values(self) -> None:
        self.assertEqual(TickDemand(-2, 3).operation_count, 3)
        self.assertEqual(TickDemand(2, -3).operation_count, 2)
        self.assertEqual(TickDemand(-2, -3).operation_count, 0)

    def test_idle_state_baselines_preserve_pressure_precedence(self) -> None:
        cases = (
            ("ok", "ok", 0.2),
            ("ok", "busy", 0.3),
            ("degraded", "ok", 0.5),
            ("degraded", "busy", 0.5),
            ("ok", "constrained", 1.0),
            ("degraded", "constrained", 1.0),
            ("protective", "ok", 1.0),
            ("protective", "busy", 1.0),
        )
        for circuit, resource, expected in cases:
            with self.subTest(circuit=circuit, resource=resource):
                self.assertAlmostEqual(
                    adaptive_tick_seconds(
                        POLICY,
                        TickDemand(),
                        circuit_state=circuit,
                        resource_state=resource,
                    ),
                    expected,
                )

    def test_critical_read_budget_accelerates_constrained_gateway(self) -> None:
        demand = TickDemand(
            critical_read_operations=2,
            core_read_age_seconds=4.0,
            operation_p95_ms=100.0,
        )
        self.assertAlmostEqual(
            adaptive_tick_seconds(
                POLICY,
                demand,
                circuit_state="ok",
                resource_state="constrained",
            ),
            0.3,
        )

    def test_queue_budget_and_earliest_deadline_control_cadence(self) -> None:
        queue_only = TickDemand(
            critical_queue_operations=2,
            queue_age_seconds=9.0,
        )
        mixed = TickDemand(
            critical_read_operations=1,
            critical_queue_operations=1,
            core_read_age_seconds=1.0,
            queue_age_seconds=9.5,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                POLICY,
                queue_only,
                circuit_state="ok",
                resource_state="constrained",
            ),
            0.4,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                POLICY,
                mixed,
                circuit_state="ok",
                resource_state="constrained",
            ),
            0.2,
        )

    def test_exhausted_budget_clamps_to_minimum_and_protective_stays_slow(self) -> None:
        overdue = TickDemand(
            critical_queue_operations=3,
            queue_age_seconds=11.0,
            operation_p95_ms=500.0,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                POLICY,
                overdue,
                circuit_state="ok",
                resource_state="constrained",
            ),
            0.2,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                POLICY,
                overdue,
                circuit_state="protective",
                resource_state="constrained",
            ),
            1.0,
        )

    def test_policy_normalizes_inverted_bounds_and_caps_state_floors(self) -> None:
        inverted = TickPolicy(0.0, -1.0, 5.0, 10.0)
        self.assertEqual(
            adaptive_tick_seconds(
                inverted,
                TickDemand(),
                circuit_state="ok",
                resource_state="busy",
            ),
            0.001,
        )
        capped = TickPolicy(0.4, 0.45, 5.0, 10.0)
        self.assertEqual(
            adaptive_tick_seconds(
                capped,
                TickDemand(),
                circuit_state="degraded",
                resource_state="ok",
            ),
            0.45,
        )

    def test_state_multipliers_remain_effective_above_fixed_floors(self) -> None:
        policy = TickPolicy(0.3, 2.0, 5.0, 10.0)
        self.assertEqual(
            adaptive_tick_seconds(
                policy,
                TickDemand(),
                circuit_state="degraded",
                resource_state="ok",
            ),
            0.75,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                TickPolicy(0.25, 2.0, 5.0, 10.0),
                TickDemand(),
                circuit_state="ok",
                resource_state="busy",
            ),
            0.375,
        )

    def test_single_source_deadlines_ignore_absent_work_class(self) -> None:
        wide = TickPolicy(0.1, 20.0, 5.0, 10.0)
        read_only = TickDemand(
            critical_read_operations=1,
            core_read_age_seconds=1.0,
        )
        queue_only = TickDemand(
            critical_queue_operations=1,
            queue_age_seconds=0.0,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                wide,
                read_only,
                circuit_state="ok",
                resource_state="constrained",
            ),
            3.2,
        )
        self.assertEqual(
            adaptive_tick_seconds(
                wide,
                queue_only,
                circuit_state="ok",
                resource_state="constrained",
            ),
            8.0,
        )

    def test_subsecond_age_and_deadline_boundaries_affect_cadence(self) -> None:
        policy = TickPolicy(0.1, 20.0, 5.0, 10.0)
        cases = (
            (TickDemand(critical_read_operations=1, core_read_age_seconds=4.5), 0.4),
            (TickDemand(critical_read_operations=1, core_read_age_seconds=0.25), 3.8),
            (TickDemand(critical_queue_operations=1, queue_age_seconds=0.25), 7.8),
        )
        for demand, expected in cases:
            with self.subTest(demand=demand):
                self.assertAlmostEqual(
                    adaptive_tick_seconds(
                        policy,
                        demand,
                        circuit_state="ok",
                        resource_state="constrained",
                    ),
                    expected,
                )

    def test_read_only_demand_does_not_inherit_queue_deadline(self) -> None:
        policy = TickPolicy(0.1, 20.0, 5.0, 1.0)
        self.assertEqual(
            adaptive_tick_seconds(
                policy,
                TickDemand(critical_read_operations=1),
                circuit_state="ok",
                resource_state="constrained",
            ),
            4.0,
        )


if __name__ == "__main__":
    unittest.main()
