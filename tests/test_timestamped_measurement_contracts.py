#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for atomic value and timestamp observations."""

from __future__ import annotations

import math
import unittest

from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement


class TimestampedMeasurementContractTests(unittest.TestCase):
    def test_available_and_unavailable_observations_have_exact_shapes(self) -> None:
        unavailable = TimestampedMeasurement[float].unavailable()
        observed = TimestampedMeasurement.observed(
            12.5,
            captured_at=20,
            observed_monotonic=30,
        )

        self.assertEqual(unavailable, TimestampedMeasurement(None, None, None))
        self.assertFalse(unavailable.available)
        self.assertEqual(observed, TimestampedMeasurement(12.5, 20.0, 30.0))
        self.assertTrue(observed.available)

    def test_direct_contract_rejects_every_partial_shape(self) -> None:
        partials = (
            (1.0, None, None),
            (None, 2.0, None),
            (None, None, 3.0),
            (1.0, 2.0, None),
            (1.0, None, 3.0),
            (None, 2.0, 3.0),
        )
        for partial in partials:
            with self.subTest(partial=partial), self.assertRaisesRegex(
                ValueError,
                "^Timestamped measurement requires value and both timestamps together$",
            ):
                TimestampedMeasurement(*partial)

    def test_direct_contract_rejects_invalid_complete_timestamps(self) -> None:
        invalid = (-1.0, math.inf, -math.inf, math.nan)
        for timestamp in invalid:
            with self.subTest(captured_at=timestamp), self.assertRaisesRegex(
                ValueError,
                "^Timestamped measurement timestamps must be finite and non-negative$",
            ):
                TimestampedMeasurement.observed(
                    1.0,
                    captured_at=timestamp,
                    observed_monotonic=2.0,
                )
            with self.subTest(observed_monotonic=timestamp), self.assertRaisesRegex(
                ValueError,
                "^Timestamped measurement timestamps must be finite and non-negative$",
            ):
                TimestampedMeasurement.observed(
                    1.0,
                    captured_at=2.0,
                    observed_monotonic=timestamp,
                )

    def test_zero_is_a_valid_clock_origin(self) -> None:
        self.assertEqual(
            TimestampedMeasurement.observed(
                1.0,
                captured_at=0.0,
                observed_monotonic=0.0,
            ),
            TimestampedMeasurement(1.0, 0.0, 0.0),
        )

    def test_boundary_normalization_never_emits_partial_measurements(self) -> None:
        complete = TimestampedMeasurement.from_optional(7.0, 8.0, 9.0)
        self.assertEqual(complete, TimestampedMeasurement(7.0, 8.0, 9.0))

        invalid_payloads = (
            (None, 8.0, 9.0),
            (7.0, None, 9.0),
            (7.0, 8.0, None),
            (7.0, -1.0, 9.0),
            (7.0, 8.0, math.inf),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(
                    TimestampedMeasurement.from_optional(*payload),
                    TimestampedMeasurement.unavailable(),
                )

    def test_age_and_freshness_use_only_the_monotonic_clock(self) -> None:
        measurement = TimestampedMeasurement.observed(
            5.0,
            captured_at=10_000.0,
            observed_monotonic=100.0,
        )

        self.assertEqual(measurement.age_seconds(102.5), 2.5)
        self.assertEqual(measurement.age_seconds(99.0), 0.0)
        self.assertTrue(measurement.fresh(105.0, max_age_seconds=5.0))
        self.assertFalse(measurement.fresh(105.001, max_age_seconds=5.0))
        self.assertTrue(
            measurement.fresh(
                99.0,
                max_age_seconds=5.0,
                future_tolerance_seconds=1.0,
            )
        )
        self.assertFalse(
            measurement.fresh(
                98.999,
                max_age_seconds=5.0,
                future_tolerance_seconds=1.0,
            )
        )
        unavailable = TimestampedMeasurement[float].unavailable()
        self.assertIsNone(unavailable.age_seconds(1.0))
        self.assertFalse(unavailable.fresh(1.0, max_age_seconds=5.0))


if __name__ == "__main__":
    unittest.main()
