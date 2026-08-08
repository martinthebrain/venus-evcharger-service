#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact rate and circuit-breaker contracts for asynchronous DBus work."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger import dbus_gateway_latency as latency_module
from venus_evcharger.dbus_adapter import rate as rate_module
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker


def _latency_summary(
    *,
    samples: int,
    timeouts: int,
    average: float,
    p95: float,
    p99: float,
    maximum: float,
) -> dict[str, object]:
    return {
        "samples_60s": samples,
        "timeouts_60s": timeouts,
        "avg_latency_ms": average,
        "p95_latency_ms": p95,
        "p99_latency_ms": p99,
        "max_latency_ms": maximum,
    }


EMPTY_LATENCY_SUMMARY = _latency_summary(
    samples=0,
    timeouts=0,
    average=0.0,
    p95=0.0,
    p99=0.0,
    maximum=0.0,
)


class _Clock:
    def __init__(self, *, epoch: float = 0.0, monotonic_at: float) -> None:
        self._epoch = epoch
        self._monotonic_at = monotonic_at

    def time(self) -> float:
        return self._epoch

    def monotonic(self) -> float:
        return self._monotonic_at


class DbusAdapterRateAsyncContractTests(unittest.TestCase):
    """Pin accounting and deadline behavior exposed to the async broker."""

    def test_epoch_deadlines_convert_once_to_monotonic_boundaries(self) -> None:
        breaker = DbusCircuitBreaker()

        with patch.object(rate_module, "time", _Clock(epoch=1000.0, monotonic_at=50.0)):
            breaker.degraded_until = 1010.0
            breaker.protective_until = 1005.0

        self.assertEqual(breaker.degraded_until, 1010.0)
        self.assertEqual(breaker.protective_until, 1005.0)
        self.assertEqual(breaker.state(monotonic_at=54.999), "protective")
        self.assertEqual(breaker.state(monotonic_at=55.0), "degraded")
        self.assertEqual(breaker.state(monotonic_at=59.999), "degraded")
        self.assertEqual(breaker.state(monotonic_at=60.0), "ok")

        fractional_deadline = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(epoch=0.0, monotonic_at=10.0)):
            fractional_deadline.degraded_until = 0.5
        self.assertEqual(fractional_deadline.state(monotonic_at=10.25), "degraded")
        self.assertEqual(fractional_deadline.state(monotonic_at=10.5), "ok")

        past_deadline = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(epoch=1000.0, monotonic_at=50.0)):
            past_deadline.degraded_until = 999.0
        self.assertEqual(past_deadline.state(monotonic_at=49.999), "degraded")
        self.assertEqual(past_deadline.state(monotonic_at=50.0), "ok")

        breaker.degraded_until = -5.0
        breaker.protective_until = 0.0
        self.assertEqual(breaker.degraded_until, 0.0)
        self.assertEqual(breaker.protective_until, 0.0)
        self.assertEqual(breaker.state(monotonic_at=0.0), "ok")

    def test_initial_health_and_duration_clamps_are_exact(self) -> None:
        breaker = DbusCircuitBreaker(degraded_seconds=0.25, protective_seconds=-9.0)

        with patch.object(rate_module, "time", _Clock(monotonic_at=12.0)):
            health = breaker.health()

        self.assertEqual(breaker.degraded_seconds, 1.0)
        self.assertEqual(breaker.protective_seconds, 1.0)
        self.assertEqual(
            health,
            {
                "state": "ok",
                "degraded_until": 0.0,
                "last_success_at": 0.0,
                "last_error": "",
                "errors_60s": 0,
                "successes_60s": 0,
                "consecutive_failures": 0,
                "operations": {},
                "operation_sources": {},
                **EMPTY_LATENCY_SUMMARY,
            },
        )
        self.assertEqual(
            breaker.latencies_by_source.max_operation_kinds,
            rate_module.DBUS_LATENCY_OPERATION_KIND_LIMIT,
        )
        self.assertEqual(
            breaker.latencies_by_source.max_sources_per_kind,
            rate_module.DBUS_LATENCY_SOURCES_PER_KIND_LIMIT,
        )

    def test_success_resets_failure_state_and_attributes_latency_exactly(self) -> None:
        breaker = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(epoch=1000.0, monotonic_at=50.0)):
            breaker.record_error(RuntimeError("transient"), kind="write")
        with patch.object(rate_module, "time", _Clock(epoch=1001.0, monotonic_at=51.0)):
            breaker.record_success(
                12.5,
                kind=" optional_read ",
                source="pv.service/Power",
            )
            health = breaker.health()

        measured = _latency_summary(
            samples=1,
            timeouts=0,
            average=12.5,
            p95=12.5,
            p99=12.5,
            maximum=12.5,
        )
        self.assertEqual(
            health,
            {
                "state": "ok",
                "degraded_until": 0.0,
                "last_success_at": 1001.0,
                "last_error": "",
                "errors_60s": 1,
                "successes_60s": 1,
                "consecutive_failures": 0,
                "operations": {"optional_read": measured},
                "operation_sources": {
                    "optional_read": {"pv.service/Power": measured}
                },
                **measured,
            },
        )

    def test_errors_distinguish_missing_zero_and_timeout_latency(self) -> None:
        breaker = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(epoch=100.0, monotonic_at=10.0)):
            breaker.record_error(
                RuntimeError("without latency"),
                kind=" write ",
                source="relay",
            )
        with patch.object(rate_module, "time", _Clock(epoch=101.0, monotonic_at=11.0)):
            breaker.record_error(
                RuntimeError("zero latency"),
                kind=" write ",
                source="relay",
                latency_ms=0.0,
            )
        with patch.object(rate_module, "time", _Clock(epoch=102.0, monotonic_at=12.0)):
            breaker.record_error(
                TimeoutError("timed out"),
                kind=" write ",
                source="relay",
                latency_ms=80.0,
            )
            health = breaker.health()

        measured = _latency_summary(
            samples=2,
            timeouts=1,
            average=40.0,
            p95=80.0,
            p99=80.0,
            maximum=80.0,
        )
        self.assertEqual(
            health,
            {
                "state": "ok",
                "degraded_until": 0.0,
                "last_success_at": 0.0,
                "last_error": "timed out",
                "errors_60s": 3,
                "successes_60s": 0,
                "consecutive_failures": 3,
                "operations": {"write": measured},
                "operation_sources": {"write": {"relay": measured}},
                **measured,
            },
        )

    def test_default_sources_are_never_fabricated_for_success_or_error(self) -> None:
        breaker = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(epoch=1000.0, monotonic_at=10.0)):
            breaker.record_success(10.0, kind="read")
            breaker.record_error(
                RuntimeError("plain"),
                kind="write",
                latency_ms=20.0,
            )
            health = breaker.health()

        self.assertEqual(health["operation_sources"], {})

    def test_explicit_error_timestamps_control_all_latency_windows(self) -> None:
        breaker = DbusCircuitBreaker()
        with (
            patch.object(rate_module, "time", _Clock(epoch=1000.0, monotonic_at=10.0)),
            patch.object(latency_module.time, "monotonic", return_value=500.0),
        ):
            breaker.record_error(
                TimeoutError("source timeout"),
                kind="optional_read",
                source="source",
                latency_ms=800.0,
            )

        with (
            patch.object(rate_module, "time", _Clock(monotonic_at=70.001)),
            patch.object(latency_module.time, "monotonic", return_value=10.0),
        ):
            health = breaker.health()

        self.assertEqual(
            health,
            {
                "state": "ok",
                "degraded_until": 0.0,
                "last_success_at": 0.0,
                "last_error": "source timeout",
                "errors_60s": 0,
                "successes_60s": 0,
                "consecutive_failures": 1,
                "operations": {"optional_read": EMPTY_LATENCY_SUMMARY},
                "operation_sources": {
                    "optional_read": {"source": EMPTY_LATENCY_SUMMARY}
                },
                **EMPTY_LATENCY_SUMMARY,
            },
        )

    def test_optional_interval_factors_use_exact_sample_and_latency_boundaries(self) -> None:
        under_sampled = self._breaker_with_optional_latencies(1000.0, 1000.0)
        below_slow = self._breaker_with_optional_latencies(249.999, 249.999, 249.999)
        slow = self._breaker_with_optional_latencies(250.0, 250.0, 250.0)
        below_very_slow = self._breaker_with_optional_latencies(749.999, 749.999, 749.999)
        very_slow = self._breaker_with_optional_latencies(750.0, 750.0, 750.0)

        with patch.object(rate_module, "time", _Clock(monotonic_at=10.0)):
            self.assertEqual(under_sampled.optional_source_interval_factor("source"), 1.0)
            self.assertEqual(below_slow.optional_source_interval_factor("source"), 1.0)
            self.assertEqual(
                slow.optional_source_interval_factor("source"),
                rate_module.OPTIONAL_SLOW_SOURCE_INTERVAL_FACTOR,
            )
            self.assertEqual(
                below_very_slow.optional_source_interval_factor("source"),
                rate_module.OPTIONAL_SLOW_SOURCE_INTERVAL_FACTOR,
            )
            self.assertEqual(
                very_slow.optional_source_interval_factor("source"),
                rate_module.OPTIONAL_VERY_SLOW_SOURCE_INTERVAL_FACTOR,
            )
            self.assertEqual(very_slow.optional_source_interval_factor("other"), 1.0)

        with patch.object(rate_module, "time", _Clock(monotonic_at=70.001)):
            self.assertEqual(very_slow.optional_source_interval_factor("source"), 1.0)

        clock_contract = self._breaker_with_optional_latencies(750.0, 750.0, 750.0)
        with (
            patch.object(rate_module, "time", _Clock(monotonic_at=70.001)),
            patch.object(latency_module.time, "monotonic", return_value=10.0),
        ):
            self.assertEqual(clock_contract.optional_source_interval_factor("source"), 1.0)

    def test_optional_failures_measure_source_without_tripping_global_timeout(self) -> None:
        breaker = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(monotonic_at=20.0)):
            breaker.record_optional_source_failure(
                TimeoutError("sleeping source timeout"),
                source="pv.sleeping/Power",
                latency_ms=900.0,
            )
        with patch.object(rate_module, "time", _Clock(monotonic_at=21.0)):
            breaker.record_optional_source_failure(
                RuntimeError("plain failure"),
                source="pv.sleeping/Power",
                latency_ms=60.0,
            )
        with patch.object(rate_module, "time", _Clock(monotonic_at=22.0)):
            breaker.record_optional_source_failure(
                TimeoutError("unattributed"),
                source="",
                latency_ms=70.0,
            )
            health = breaker.health()

        aggregate = _latency_summary(
            samples=3,
            timeouts=0,
            average=1030.0 / 3.0,
            p95=900.0,
            p99=900.0,
            maximum=900.0,
        )
        source = _latency_summary(
            samples=2,
            timeouts=1,
            average=480.0,
            p95=900.0,
            p99=900.0,
            maximum=900.0,
        )
        self.assertEqual(
            health,
            {
                "state": "ok",
                "degraded_until": 0.0,
                "last_success_at": 0.0,
                "last_error": "",
                "errors_60s": 0,
                "successes_60s": 0,
                "consecutive_failures": 0,
                "operations": {"optional_read": aggregate},
                "operation_sources": {
                    "optional_read": {"pv.sleeping/Power": source}
                },
                **aggregate,
            },
        )

        with patch.object(rate_module, "time", _Clock(monotonic_at=80.0)):
            self.assertEqual(breaker.health(), health)
        with patch.object(rate_module, "time", _Clock(monotonic_at=80.001)):
            aged_health = breaker.health()
        self.assertEqual(
            aged_health["operation_sources"],
            {
                "optional_read": {
                    "pv.sleeping/Power": _latency_summary(
                        samples=1,
                        timeouts=0,
                        average=60.0,
                        p95=60.0,
                        p99=60.0,
                        maximum=60.0,
                    )
                }
            },
        )

    def test_optional_failure_timestamps_age_all_measurements_together(self) -> None:
        breaker = DbusCircuitBreaker()
        for monotonic_at in (20.0, 21.0, 22.0):
            with (
                patch.object(rate_module, "time", _Clock(monotonic_at=monotonic_at)),
                patch.object(latency_module.time, "monotonic", return_value=500.0),
            ):
                breaker.record_optional_source_failure(
                    TimeoutError("optional source timeout"),
                    source="source",
                    latency_ms=900.0,
                )

        with (
            patch.object(rate_module, "time", _Clock(monotonic_at=82.001)),
            patch.object(latency_module.time, "monotonic", return_value=22.0),
        ):
            health = breaker.health()

        self.assertEqual(
            health,
            {
                "state": "ok",
                "degraded_until": 0.0,
                "last_success_at": 0.0,
                "last_error": "",
                "errors_60s": 0,
                "successes_60s": 0,
                "consecutive_failures": 0,
                "operations": {"optional_read": EMPTY_LATENCY_SUMMARY},
                "operation_sources": {
                    "optional_read": {"source": EMPTY_LATENCY_SUMMARY}
                },
                **EMPTY_LATENCY_SUMMARY,
            },
        )

    def test_timeout_thresholds_extend_deadlines_only_with_later_monotonic_time(self) -> None:
        breaker = DbusCircuitBreaker(degraded_seconds=30.0, protective_seconds=60.0)

        self._record_timeout(breaker, epoch=1000.0, monotonic_at=10.0)
        self._record_timeout(breaker, epoch=1000.0, monotonic_at=10.0)
        self.assertEqual(breaker.state(monotonic_at=10.0), "ok")

        self._record_timeout(breaker, epoch=1000.0, monotonic_at=10.0)
        self.assertEqual(breaker.degraded_until, 1030.0)
        self.assertEqual(breaker.state(monotonic_at=10.0), "degraded")

        self._record_timeout(breaker, epoch=1005.0, monotonic_at=10.0)
        self.assertEqual(breaker.degraded_until, 1030.0)
        self._record_timeout(breaker, epoch=1001.0, monotonic_at=11.0)
        self.assertEqual(breaker.degraded_until, 1031.0)
        self.assertEqual(breaker.state(monotonic_at=11.0), "degraded")

        self._record_timeout(breaker, epoch=2000.0, monotonic_at=20.0)
        self.assertEqual(breaker.protective_until, 2060.0)
        self.assertEqual(breaker.state(monotonic_at=20.0), "protective")

        self._record_timeout(breaker, epoch=2010.0, monotonic_at=20.0)
        self.assertEqual(breaker.protective_until, 2060.0)
        self._record_timeout(breaker, epoch=2001.0, monotonic_at=21.0)
        self.assertEqual(breaker.protective_until, 2061.0)
        self.assertEqual(breaker.state(monotonic_at=80.999), "protective")
        self.assertEqual(breaker.state(monotonic_at=81.0), "ok")

    @staticmethod
    def _breaker_with_optional_latencies(*latencies: float) -> DbusCircuitBreaker:
        breaker = DbusCircuitBreaker()
        with patch.object(rate_module, "time", _Clock(epoch=1000.0, monotonic_at=10.0)):
            for latency in latencies:
                breaker.record_success(
                    latency,
                    kind="optional_read",
                    source="source",
                )
        return breaker

    @staticmethod
    def _record_timeout(
        breaker: DbusCircuitBreaker,
        *,
        epoch: float,
        monotonic_at: float,
    ) -> None:
        with patch.object(rate_module, "time", _Clock(epoch=epoch, monotonic_at=monotonic_at)):
            breaker.record_error(TimeoutError("timeout"))


if __name__ == "__main__":
    unittest.main()
