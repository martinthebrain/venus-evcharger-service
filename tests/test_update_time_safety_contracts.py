# SPDX-License-Identifier: GPL-3.0-or-later
"""Time-safety contracts for update-layer runtime decisions."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.update.pm_snapshot import PmSnapshotResolver
from venus_evcharger.update.relay_charger_current_targets import (
    ChargerCurrentTargetPolicy,
    _is_month_window_pair,
    _is_month_windows,
    _is_time_window,
)
from venus_evcharger.update.state import UpdateStateController


@dataclass
class _SessionState:
    charging_started_at: float | None
    energy_at_start: float


class UpdateTimeSafetyContractTests(unittest.TestCase):
    def test_learned_power_future_tolerance_matches_pm_snapshot_contract(self) -> None:
        tolerance = ChargerCurrentTargetPolicy.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS

        self.assertEqual(tolerance, PmSnapshotResolver.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS)
        self.assertEqual(tolerance, 1.0)

    def test_learned_power_accepts_future_tolerance_boundary_and_rejects_beyond_it(self) -> None:
        now = 100.0
        tolerance = ChargerCurrentTargetPolicy.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS

        self.assertFalse(ChargerCurrentTargetPolicy.learned_target_stale(now, now + tolerance, None))
        self.assertTrue(ChargerCurrentTargetPolicy.learned_target_stale(now, now + tolerance + 0.001, None))

    def test_learned_power_uses_policy_specific_future_tolerance(self) -> None:
        class TightTolerancePolicy(ChargerCurrentTargetPolicy):
            FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS = 0.25

        self.assertFalse(TightTolerancePolicy.learned_target_stale(100.0, 100.25, None))
        self.assertTrue(TightTolerancePolicy.learned_target_stale(100.0, 100.251, None))

    def test_schedule_window_guards_reject_wrong_container_shapes(self) -> None:
        self.assertFalse(_is_time_window([1, 2]))
        self.assertFalse(_is_time_window((1,)))
        self.assertTrue(_is_time_window((1, 2)))

        self.assertFalse(_is_month_window_pair([(1, 2), (3, 4)]))
        self.assertFalse(_is_month_window_pair(((1, 2),)))
        self.assertFalse(_is_month_window_pair(((1, 2), (3, 4), (5, 6))))
        self.assertTrue(_is_month_window_pair(((1, 2), (3, 4))))

        self.assertFalse(_is_month_windows(((1, 2), (3, 4))))
        self.assertTrue(_is_month_windows({1: ((1, 2), (3, 4))}))

    def test_derived_target_rejects_implausibly_future_learning_timestamp(self) -> None:
        service = SimpleNamespace(
            learned_charge_power_state="stable",
            learned_charge_power_watts=2300.0,
            learned_charge_power_voltage=230.0,
            learned_charge_power_phase="L1",
            learned_charge_power_updated_at=101.001,
            auto_policy=AutoPolicy(),
            min_current=6.0,
            max_current=16.0,
            voltage_mode="phase",
        )

        self.assertIsNone(ChargerCurrentTargetPolicy.derived_learned_target(service, 100.0))
        service.learned_charge_power_updated_at = 101.0
        self.assertEqual(ChargerCurrentTargetPolicy.derived_learned_target(service, 100.0), 10.0)

    def test_active_session_time_never_becomes_negative_when_clock_moves_back(self) -> None:
        service = _SessionState(charging_started_at=105.0, energy_at_start=2.0)

        self.assertEqual(UpdateStateController._active_session_state(service, 2.5, 100.0), (0, 0.5))
        self.assertEqual(service.charging_started_at, 105.0)

    def test_active_session_time_boundary_starts_at_zero_then_increases(self) -> None:
        service = _SessionState(charging_started_at=100.0, energy_at_start=2.0)

        self.assertEqual(UpdateStateController._active_session_state(service, 2.0, 100.0), (0, 0.0))
        self.assertEqual(UpdateStateController._active_session_state(service, 2.0, 101.0), (1, 0.0))


if __name__ == "__main__":
    unittest.main()
