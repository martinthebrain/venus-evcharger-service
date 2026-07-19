# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for atomic readback ownership and centralized freshness."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
import math
from types import SimpleNamespace
import unittest

from venus_evcharger.backend.models import ChargerState, SwitchState
from venus_evcharger.ports.readback import ReadbackSnapshots, TimedChargerState, TimedSwitchState
from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.update.readback_resolver import ReadbackResolver


@dataclass
class _Settings:
    _worker_poll_interval_seconds: object = 1.0
    auto_shelly_soft_fail_seconds: object = 10.0


class _CountingStore:
    def __init__(self, snapshots: ReadbackSnapshots) -> None:
        self.snapshots = snapshots
        self.calls = 0

    def snapshot(self) -> ReadbackSnapshots:
        self.calls += 1
        return self.snapshots


def _charger(*, enabled: bool = True) -> ChargerState:
    return ChargerState(
        enabled=enabled,
        current_amps=12.0,
        phase_selection="P1",
        actual_current_amps=11.5,
        power_w=2645.0,
        energy_kwh=4.25,
        status_text="charging",
        fault_text=None,
    )


def _switch(*, enabled: bool = True) -> SwitchState:
    return SwitchState(
        enabled=enabled,
        phase_selection="P1",
        feedback_closed=enabled,
        interlock_ok=True,
    )


class TestReadbackResolverContracts(unittest.TestCase):
    def test_store_replaces_complete_snapshots_and_clears_each_source_independently(self) -> None:
        store = InMemoryReadbackStore()
        self.assertEqual(store.snapshot(), ReadbackSnapshots(charger=None, switch=None))

        charger = TimedChargerState(_charger(), 100.0)
        switch = TimedSwitchState(_switch(), 101.0)
        store.replace_charger(charger)
        store.replace_switch(switch)
        self.assertEqual(store.snapshot(), ReadbackSnapshots(charger=charger, switch=switch))

        store.replace_charger(None)
        self.assertEqual(store.snapshot(), ReadbackSnapshots(charger=None, switch=switch))
        store.replace_switch(None)
        self.assertEqual(store.snapshot(), ReadbackSnapshots(charger=None, switch=None))

    def test_snapshots_are_immutable_values(self) -> None:
        charger = TimedChargerState(_charger(), 100.0)
        switch = TimedSwitchState(_switch(), 100.0)
        combined = ReadbackSnapshots(charger=charger, switch=switch)

        for value, attribute in (
            (charger, "captured_at"),
            (switch, "captured_at"),
            (combined, "charger"),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, attribute, None)

    def test_resolver_reads_one_atomic_store_view(self) -> None:
        charger = TimedChargerState(_charger(), 100.0)
        switch = TimedSwitchState(_switch(), 100.0)
        store = _CountingStore(ReadbackSnapshots(charger=charger, switch=switch))

        resolved = ReadbackResolver(store, _Settings()).resolve(100.0)

        self.assertEqual(resolved.charger, charger)
        self.assertEqual(resolved.switch, switch)
        self.assertEqual(store.calls, 1)

    def test_freshness_boundary_is_inclusive_and_symmetric_for_future_timestamps(self) -> None:
        store = InMemoryReadbackStore()
        resolver = ReadbackResolver(store, _Settings())

        for captured_at, expected_fresh in (
            (98.0, True),
            (102.0, True),
            (97.999, False),
            (102.001, False),
        ):
            with self.subTest(captured_at=captured_at):
                store.replace_charger(TimedChargerState(_charger(), captured_at))
                store.replace_switch(TimedSwitchState(_switch(), captured_at))
                resolved = resolver.resolve(100.0)
                self.assertEqual(resolved.charger is not None, expected_fresh)
                self.assertEqual(resolved.switch is not None, expected_fresh)

    def test_non_finite_timestamps_and_missing_snapshots_are_never_fresh(self) -> None:
        store = InMemoryReadbackStore()
        resolver = ReadbackResolver(store, _Settings())
        resolved = resolver.resolve(100.0)
        self.assertIsNone(resolved.charger)
        self.assertIsNone(resolved.switch)

        for captured_at in (math.nan, math.inf, -math.inf):
            with self.subTest(captured_at=captured_at):
                store.replace_charger(TimedChargerState(_charger(), captured_at))
                store.replace_switch(TimedSwitchState(_switch(), captured_at))
                resolved = resolver.resolve(100.0)
                self.assertIsNone(resolved.charger)
                self.assertIsNone(resolved.switch)

    def test_strictest_positive_budget_is_floored_at_one_second(self) -> None:
        cases = (
            (_Settings(), 2.0),
            (_Settings(0.4, 10.0), 1.0),
            (_Settings(3.0, 1.5), 1.5),
            (_Settings(0.0, -1.0), 2.0),
            (_Settings("invalid", math.nan), 2.0),
        )
        for settings, expected in cases:
            with self.subTest(settings=settings):
                self.assertEqual(ReadbackResolver(InMemoryReadbackStore(), settings).max_age_seconds(), expected)

    def test_explicit_time_bypasses_injected_clock(self) -> None:
        clock_calls = 0

        def clock() -> float:
            nonlocal clock_calls
            clock_calls += 1
            return 123.0

        resolver = ReadbackResolver(InMemoryReadbackStore(), _Settings(), clock)
        self.assertEqual(resolver.current_time(99.0), 99.0)
        self.assertEqual(clock_calls, 0)
        self.assertEqual(resolver.current_time(), 123.0)
        self.assertEqual(clock_calls, 1)

    def test_runtime_settings_expose_the_complete_freshness_contract(self) -> None:
        store = InMemoryReadbackStore()
        settings = SimpleNamespace(
            _worker_poll_interval_seconds=0.4,
            auto_shelly_soft_fail_seconds=10.0,
        )
        resolver = ReadbackResolver(store, settings)

        self.assertEqual(resolver.max_age_seconds(), 1.0)


if __name__ == "__main__":
    unittest.main()
