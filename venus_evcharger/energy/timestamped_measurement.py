# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral timestamp contract for observed domain values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Generic, TypeGuard, TypeVar

ValueT = TypeVar("ValueT")


@dataclass(frozen=True, slots=True)
class TimestampedMeasurement(Generic[ValueT]):
    """Keep a value and both of its clocks present or absent as one unit."""

    value: ValueT | None
    captured_at: float | None
    observed_monotonic: float | None

    def __post_init__(self) -> None:
        present = (
            self.value is not None,
            self.captured_at is not None,
            self.observed_monotonic is not None,
        )
        if any(present) and not all(present):
            raise ValueError("Timestamped measurement requires value and both timestamps together")
        if self.available and not self._timestamps_valid():
            raise ValueError("Timestamped measurement timestamps must be finite and non-negative")

    @classmethod
    def unavailable(cls) -> TimestampedMeasurement[ValueT]:
        """Return the canonical representation of an unavailable observation."""
        return cls(None, None, None)

    @classmethod
    def observed(
        cls,
        value: ValueT,
        *,
        captured_at: float,
        observed_monotonic: float,
    ) -> TimestampedMeasurement[ValueT]:
        """Create one complete observation."""
        return cls(value, float(captured_at), float(observed_monotonic))

    @classmethod
    def from_optional(
        cls,
        value: ValueT | None,
        captured_at: float | None,
        observed_monotonic: float | None,
    ) -> TimestampedMeasurement[ValueT]:
        """Normalize an incomplete or invalid boundary payload to unavailable."""
        if value is None:
            return cls.unavailable()
        timestamps = _normalized_timestamps(captured_at, observed_monotonic)
        if timestamps is None:
            return cls.unavailable()
        return cls.observed(
            value,
            captured_at=timestamps[0],
            observed_monotonic=timestamps[1],
        )

    @property
    def available(self) -> bool:
        """Return whether this instance carries one complete observation."""
        return self.value is not None

    def age_seconds(self, monotonic_at: float) -> float | None:
        """Return non-negative age using only the monotonic clock."""
        if self.observed_monotonic is None:
            return None
        return max(0.0, float(monotonic_at) - self.observed_monotonic)

    def fresh(
        self,
        monotonic_at: float,
        *,
        max_age_seconds: float,
        future_tolerance_seconds: float = 0.0,
    ) -> bool:
        """Evaluate freshness without consulting wall-clock time."""
        if self.observed_monotonic is None:
            return False
        age_seconds = float(monotonic_at) - self.observed_monotonic
        return -float(future_tolerance_seconds) <= age_seconds <= float(max_age_seconds)

    def _timestamps_valid(self) -> bool:
        return _valid_timestamp(self.captured_at) and _valid_timestamp(self.observed_monotonic)


def _valid_timestamp(value: float | None) -> TypeGuard[float]:
    return value is not None and math.isfinite(value) and value >= 0.0


def _normalized_timestamps(
    captured_at: float | None,
    observed_monotonic: float | None,
) -> tuple[float, float] | None:
    if not _valid_timestamp(captured_at):
        return None
    if not _valid_timestamp(observed_monotonic):
        return None
    return float(captured_at), float(observed_monotonic)


__all__ = ["TimestampedMeasurement"]
