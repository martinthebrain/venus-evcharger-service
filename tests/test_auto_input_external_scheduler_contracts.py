# SPDX-License-Identifier: GPL-3.0-or-later
"""Backoff, fairness, and hard-cycle budgets for external source polling."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.energy.read_steps import (
    EnergySourceReadStep,
    completed_read,
    pending_read,
)
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalPollingPolicy,
    projection_measurement_status,
)
from venus_evcharger.inputs.helper.external_scheduler import (
    EnergyConnectorRuntime,
    ExternalSourceScheduler,
    _valid_observed_at,
)


class _Clock:
    def __init__(self) -> None:
        self.current = 0.0

    def __call__(self) -> float:
        return self.current


class _BudgetReader:
    def __init__(self, clock: _Clock, elapsed_per_call: float) -> None:
        self.clock = clock
        self.elapsed_per_call = elapsed_per_call
        self.calls: list[str] = []
        self.timeout_limits: list[float] = []

    def __call__(
        self,
        runtime: object,
        source: EnergySourceDefinition,
        now: float,
    ) -> EnergySourceReadStep:
        del now
        limiter = getattr(runtime, "bounded_request_timeout_seconds")
        self.timeout_limits.append(float(limiter(10.0)))
        self.calls.append(source.source_id)
        self.clock.current += self.elapsed_per_call
        raise TimeoutError(source.source_id)


def _definition(source_id: str) -> EnergySourceDefinition:
    return EnergySourceDefinition(
        source_id=source_id,
        connector_type="command_json",
        config_path=f"/{source_id}.ini",
    )


def _policy(
    *,
    budget: float = 1.0,
    backoff_base: float = 2.0,
    backoff_max: float = 5.0,
) -> ExternalPollingPolicy:
    return ExternalPollingPolicy(
        poll_interval_seconds=1.0,
        backoff_base_seconds=backoff_base,
        backoff_max_seconds=backoff_max,
        last_good_max_age_seconds=10.0,
        cycle_budget_seconds=budget,
    )


class ExternalSourceSchedulerContracts(unittest.TestCase):
    def test_time_budget_stops_work_and_round_robin_serves_deferred_source_next(self) -> None:
        clock = _Clock()
        reader = _BudgetReader(clock, elapsed_per_call=0.6)
        scheduler = ExternalSourceScheduler(
            tuple(_definition(source_id) for source_id in ("a", "b", "c")),
            _policy(budget=1.0),
            10.0,
            reader,
            monotonic=clock,
        )

        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning"):
            first = scheduler.poll(0.0)
            clock.current = 1.0
            second = scheduler.poll(1.0)
            clock.current = 2.0
            third = scheduler.poll(2.0)

        self.assertEqual(reader.calls, ["a", "b", "c"])
        for actual, expected in zip(reader.timeout_limits, (1.0, 1.0, 1.0), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual([poll.poll_status for poll in first], ["failed", "deferred_budget", "deferred_budget"])
        self.assertEqual(first[0].next_poll_at, 2.6)
        self.assertEqual(second[1].poll_status, "failed")
        self.assertEqual(third[2].poll_status, "failed")

    def test_exponential_backoff_suppresses_attempts_and_caps_delay(self) -> None:
        clock = _Clock()
        reader = _BudgetReader(clock, elapsed_per_call=0.0)
        scheduler = ExternalSourceScheduler(
            (_definition("a"),),
            _policy(),
            2.0,
            reader,
            monotonic=clock,
        )

        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning") as warning:
            first = scheduler.poll(0.0)
            clock.current = 1.0
            suppressed = scheduler.poll(1.0)
            clock.current = 2.0
            second = scheduler.poll(2.0)
            clock.current = 6.0
            third = scheduler.poll(6.0)

        self.assertEqual(reader.calls, ["a", "a", "a"])
        self.assertEqual(first[0].next_poll_at, 2.0)
        self.assertEqual(suppressed[0].poll_status, "backoff")
        self.assertEqual(second[0].next_poll_at, 6.0)
        self.assertEqual(third[0].next_poll_at, 11.0)
        self.assertEqual(third[0].consecutive_failures, 3)
        warning.assert_called_once()

    def test_runtime_timeout_without_deadline_and_policy_validation_boundaries(self) -> None:
        clock = _Clock()
        runtime = EnergyConnectorRuntime(2.0, None, clock)
        self.assertEqual(runtime.bounded_request_timeout_seconds(3.0), 2.0)
        runtime.begin_attempt(0.5)
        self.assertEqual(runtime.bounded_request_timeout_seconds(3.0), 0.5)
        clock.current = 1.0
        self.assertEqual(runtime.bounded_request_timeout_seconds(3.0), 0.001)

        invalid = (
            {"poll_interval_seconds": 0.0},
            {"poll_interval_seconds": float("nan")},
            {"poll_interval_seconds": float("inf")},
            {"backoff_base_seconds": 0.0},
            {"backoff_max_seconds": 0.0},
            {"last_good_max_age_seconds": -1.0},
            {"cycle_budget_seconds": 0.0},
            {"backoff_base_seconds": 2.0, "backoff_max_seconds": 1.0},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                ExternalPollingPolicy(**overrides)
        self.assertEqual(projection_measurement_status("fresh"), "fresh")
        self.assertEqual(projection_measurement_status("stale"), "stale")
        with self.assertRaisesRegex(ValueError, "Unsupported contributing"):
            projection_measurement_status("missing")

    def test_offline_or_empty_return_is_a_failed_attempt_not_an_observation(self) -> None:
        results = iter(
            (
                EnergySourceSnapshot(
                    source_id="a",
                    role="battery",
                    service_name="a",
                    soc=50.0,
                    online=False,
                    captured_at=1.0,
                ),
                EnergySourceSnapshot(
                    source_id="a",
                    role="battery",
                    service_name="a",
                    online=True,
                    captured_at=3.0,
                ),
            )
        )

        def reader(
            _runtime: object,
            _source: EnergySourceDefinition,
            _now: float,
        ) -> EnergySourceReadStep:
            return completed_read(next(results))

        clock = _Clock()
        scheduler = ExternalSourceScheduler(
            (_definition("a"),),
            _policy(backoff_base=1.0),
            2.0,
            reader,
            monotonic=clock,
        )
        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning") as warning:
            offline = scheduler.poll(1.0)
            clock.current = 1.0
            empty = scheduler.poll(2.0)

        self.assertEqual(offline[0].measurement_status, "missing")
        self.assertIsNone(offline[0].observed_at)
        self.assertEqual(empty[0].consecutive_failures, 2)
        warning.assert_called_once()

    def test_epoch_skew_is_diagnostic_but_non_finite_times_fail_closed(self) -> None:
        outcomes: list[tuple[str, str, float | None]] = []
        for captured_at in (11.0, -0.1, float("nan"), float("inf")):
            snapshot = EnergySourceSnapshot(
                source_id="a",
                role="battery",
                service_name="a",
                soc=50.0,
                online=True,
                captured_at=captured_at,
            )

            def reader(
                _runtime: object,
                _source: EnergySourceDefinition,
                _now: float,
                *,
                result: EnergySourceSnapshot = snapshot,
            ) -> EnergySourceReadStep:
                return completed_read(result)

            clock = _Clock()
            scheduler = ExternalSourceScheduler(
                (_definition("a"),),
                _policy(backoff_base=1.0, backoff_max=1.0),
                2.0,
                reader,
                monotonic=clock,
            )
            with patch(
                "venus_evcharger.inputs.helper.external_scheduler.logging.warning"
            ):
                poll = scheduler.poll(10.0)[0]
            outcomes.append(
                (poll.poll_status, poll.measurement_status, poll.observed_at)
            )

        self.assertEqual(outcomes[0], ("success", "fresh", 11.0))
        self.assertEqual(
            outcomes[1:],
            [("failed", "missing", None)] * 3,
        )
        self.assertFalse(_valid_observed_at(None))

    def test_in_progress_source_is_resumed_without_blocking_other_sources(self) -> None:
        clock = _Clock()
        calls: list[str] = []

        def reader(
            _runtime: object,
            source: EnergySourceDefinition,
            _now: float,
        ) -> EnergySourceReadStep:
            calls.append(source.source_id)
            return pending_read()

        scheduler = ExternalSourceScheduler(
            (_definition("a"), _definition("b")),
            _policy(),
            2.0,
            reader,
            monotonic=clock,
        )

        first = scheduler.poll(10.0)
        second = scheduler.poll(10.0)

        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(
            [poll.poll_status for poll in first],
            ["in_progress", "deferred_budget"],
        )
        self.assertEqual(
            [poll.poll_status for poll in second],
            ["in_progress", "in_progress"],
        )

    def test_success_interval_starts_after_completed_io(self) -> None:
        clock = _Clock()
        calls = 0

        def reader(
            _runtime: object,
            source: EnergySourceDefinition,
            now: float,
        ) -> EnergySourceReadStep:
            nonlocal calls
            calls += 1
            clock.current += 0.6
            return completed_read(
                EnergySourceSnapshot(
                    source_id=source.source_id,
                    role="battery",
                    service_name=source.source_id,
                    soc=50.0,
                    online=True,
                    captured_at=now,
                )
            )

        scheduler = ExternalSourceScheduler(
            (_definition("a"),),
            _policy(),
            2.0,
            reader,
            monotonic=clock,
        )

        first = scheduler.poll(10.0)
        clock.current = 1.5
        early = scheduler.poll(11.5)
        clock.current = 1.6
        due = scheduler.poll(11.6)

        self.assertEqual(calls, 2)
        self.assertEqual(first[0].next_poll_at, 11.6)
        self.assertEqual(early[0].poll_status, "idle")
        self.assertEqual(due[0].poll_status, "success")


if __name__ == "__main__":
    unittest.main()
