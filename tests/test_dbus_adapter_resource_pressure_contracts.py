# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource pressure and hysteresis contracts."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.resource_pressure import (
    BUSY_CPU_PERCENT,
    BUSY_EXIT_CPU_PERCENT,
    BUSY_EXIT_LOAD_PER_CPU,
    BUSY_EXIT_MEM_AVAILABLE_KB,
    BUSY_LOAD_PER_CPU,
    BUSY_MEM_AVAILABLE_KB,
    CONSTRAINED_CPU_PERCENT,
    CONSTRAINED_EXIT_CPU_PERCENT,
    CONSTRAINED_EXIT_LOAD_PER_CPU,
    CONSTRAINED_EXIT_MEM_AVAILABLE_KB,
    CONSTRAINED_LOAD_PER_CPU,
    CONSTRAINED_MEM_AVAILABLE_KB,
    ResourceStateLatch,
    resource_state,
)


class TestResourcePressureContracts(unittest.TestCase):
    def test_latch_starts_ok_and_observes_cpu_pressure(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=5.0)
        self.assertEqual(latch.state, "ok")
        self.assertIsNone(latch._recovery)
        self.assertEqual(
            latch.observe(
                load_per_cpu=0.1,
                cpu_pct=CONSTRAINED_CPU_PERCENT,
                mem_available_kb=100000.0,
                now=0.0,
            ),
            "constrained",
        )

    def test_constrained_evidence_preserves_original_causes_across_hysteresis(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=5.0)
        self.assertEqual(
            latch.observe(
                load_per_cpu=CONSTRAINED_LOAD_PER_CPU,
                cpu_pct=CONSTRAINED_CPU_PERCENT,
                mem_available_kb=CONSTRAINED_MEM_AVAILABLE_KB - 1.0,
                now=10.0,
                observed_at=1000.0,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.pressure_evidence_payload(),
            {
                "active": True,
                "triggered_at": 1000.0,
                "causes": ["load", "cpu", "memory"],
                "load_per_cpu_1m": CONSTRAINED_LOAD_PER_CPU,
                "system_cpu_pct": CONSTRAINED_CPU_PERCENT,
                "mem_available_kb": CONSTRAINED_MEM_AVAILABLE_KB - 1.0,
            },
        )
        latch.observe(
            load_per_cpu=0.1,
            cpu_pct=10.0,
            mem_available_kb=100000.0,
            now=11.0,
            observed_at=1001.0,
        )
        active_evidence = latch.pressure_evidence_payload()
        self.assertIsNotNone(active_evidence)
        assert active_evidence is not None
        self.assertTrue(active_evidence["active"])
        latch.observe(
            load_per_cpu=0.1,
            cpu_pct=10.0,
            mem_available_kb=100000.0,
            now=16.0,
            observed_at=1006.0,
        )
        inactive_evidence = latch.pressure_evidence_payload()
        self.assertIsNotNone(inactive_evidence)
        assert inactive_evidence is not None
        self.assertFalse(inactive_evidence["active"])

    def test_critical_cause_replaces_load_only_evidence_during_constrained_hold(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=10.0)
        latch.observe(
            load_per_cpu=CONSTRAINED_LOAD_PER_CPU,
            cpu_pct=10.0,
            mem_available_kb=100000.0,
            now=1.0,
            observed_at=100.0,
        )
        latch.observe(
            load_per_cpu=0.5,
            cpu_pct=CONSTRAINED_CPU_PERCENT,
            mem_available_kb=100000.0,
            now=2.0,
            observed_at=101.0,
        )

        evidence = latch.pressure_evidence_payload()
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["triggered_at"], 101.0)
        self.assertEqual(evidence["causes"], ["cpu"])
        self.assertEqual(evidence["system_cpu_pct"], CONSTRAINED_CPU_PERCENT)

    def test_classification_thresholds_are_exact(self) -> None:
        self.assertEqual(resource_state(CONSTRAINED_LOAD_PER_CPU, 0.0, 100000.0), "constrained")
        self.assertEqual(resource_state(0.0, CONSTRAINED_CPU_PERCENT, 100000.0), "constrained")
        self.assertEqual(resource_state(0.0, 0.0, CONSTRAINED_MEM_AVAILABLE_KB - 1.0), "constrained")
        self.assertEqual(resource_state(0.0, 0.0, CONSTRAINED_MEM_AVAILABLE_KB), "busy")
        self.assertEqual(resource_state(BUSY_LOAD_PER_CPU, 0.0, 100000.0), "busy")
        self.assertEqual(resource_state(0.0, BUSY_CPU_PERCENT, 100000.0), "busy")
        self.assertEqual(resource_state(0.0, 0.0, BUSY_MEM_AVAILABLE_KB - 1.0), "busy")
        self.assertEqual(resource_state(0.0, 0.0, BUSY_MEM_AVAILABLE_KB), "ok")
        self.assertEqual(resource_state(None, None, None), "busy")
        self.assertEqual(resource_state(None, BUSY_CPU_PERCENT, None), "busy")
        self.assertEqual(resource_state(CONSTRAINED_LOAD_PER_CPU, None, None), "constrained")

    def test_latch_escalates_immediately_and_holds_recovery(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=10.0)
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=100000.0, now=0.0), "ok")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=65535.0, now=1.0), "busy")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=80000.0, now=2.0), "busy")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=80000.0, now=11.99), "busy")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=80000.0, now=12.0), "ok")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=32000.0, now=13.0), "constrained")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=50000.0, now=14.0), "constrained")
        self.assertEqual(latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=50000.0, now=24.0), "busy")

    def test_busy_exit_margin_restarts_hold_after_each_flap(self) -> None:
        for load, cpu, memory in (
            (BUSY_EXIT_LOAD_PER_CPU, 10.0, 100000.0),
            (0.1, BUSY_EXIT_CPU_PERCENT, 100000.0),
            (0.1, 10.0, BUSY_EXIT_MEM_AVAILABLE_KB - 1.0),
        ):
            with self.subTest(load=load, cpu=cpu, memory=memory):
                latch = ResourceStateLatch(recovery_hold_seconds=5.0)
                latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=65000.0, now=0.0)
                self.assertEqual(
                    latch.observe(load_per_cpu=load, cpu_pct=cpu, mem_available_kb=memory, now=1.0),
                    "busy",
                )
                self.assertEqual(
                    latch.observe(load_per_cpu=load, cpu_pct=cpu, mem_available_kb=memory, now=6.1),
                    "busy",
                )
                self.assertEqual(
                    latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=80000.0, now=7.0),
                    "busy",
                )
                self.assertEqual(
                    latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=80000.0, now=12.0),
                    "ok",
                )

    def test_busy_exit_accepts_exact_memory_margin(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=0.0)
        self.assertEqual(
            latch.observe(
                load_per_cpu=0.1,
                cpu_pct=10.0,
                mem_available_kb=BUSY_MEM_AVAILABLE_KB - 1.0,
                now=0.0,
            ),
            "busy",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=0.1,
                cpu_pct=10.0,
                mem_available_kb=BUSY_EXIT_MEM_AVAILABLE_KB,
                now=1.0,
            ),
            "ok",
        )

    def test_constrained_exit_margin_holds_each_pressure_dimension(self) -> None:
        for load, cpu, memory in (
            (CONSTRAINED_EXIT_LOAD_PER_CPU, 10.0, 100000.0),
            (0.1, CONSTRAINED_EXIT_CPU_PERCENT, 100000.0),
            (0.1, 10.0, CONSTRAINED_EXIT_MEM_AVAILABLE_KB - 1.0),
        ):
            with self.subTest(load=load, cpu=cpu, memory=memory):
                latch = ResourceStateLatch(recovery_hold_seconds=0.0)
                latch.observe(load_per_cpu=2.0, cpu_pct=10.0, mem_available_kb=100000.0, now=0.0)
                self.assertEqual(
                    latch.observe(load_per_cpu=load, cpu_pct=cpu, mem_available_kb=memory, now=1.0),
                    "constrained",
                )
                self.assertEqual(
                    latch.observe(load_per_cpu=0.1, cpu_pct=10.0, mem_available_kb=100000.0, now=2.0),
                    "ok",
                )

    def test_further_improvement_does_not_restart_constrained_recovery(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=10.0)
        self.assertEqual(
            latch.observe(
                load_per_cpu=2.0,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=0.0,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=1.0,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=1.0,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=0.1,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=5.0,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=0.1,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=11.0,
            ),
            "ok",
        )

    def test_recovery_worsening_restarts_hold_and_escalation_is_immediate(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=5.0)
        latch.observe(
            load_per_cpu=2.0,
            cpu_pct=10.0,
            mem_available_kb=100000.0,
            now=0.0,
        )
        latch.observe(
            load_per_cpu=0.1,
            cpu_pct=10.0,
            mem_available_kb=100000.0,
            now=1.0,
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=1.0,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=4.0,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=1.0,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=8.99,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=1.0,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=9.0,
            ),
            "busy",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=2.0,
                cpu_pct=10.0,
                mem_available_kb=100000.0,
                now=9.1,
            ),
            "constrained",
        )

    def test_unavailable_dimensions_never_claim_recovery(self) -> None:
        latch = ResourceStateLatch(recovery_hold_seconds=0.0)
        self.assertEqual(
            latch.observe(
                load_per_cpu=None,
                cpu_pct=None,
                mem_available_kb=None,
                now=0.0,
            ),
            "busy",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=None,
                cpu_pct=None,
                mem_available_kb=1000.0,
                now=1.0,
            ),
            "constrained",
        )
        self.assertEqual(
            latch.observe(
                load_per_cpu=None,
                cpu_pct=None,
                mem_available_kb=None,
                now=2.0,
            ),
            "constrained",
        )


if __name__ == "__main__":
    unittest.main()
