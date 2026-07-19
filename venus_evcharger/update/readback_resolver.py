# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve fresh immutable backend readbacks for one update-cycle instant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import time
from typing import Protocol, TypeVar

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.ports.readback import ReadbackStore, TimedChargerState, TimedSwitchState

TimedSnapshot = TypeVar("TimedSnapshot", TimedChargerState, TimedSwitchState)


class ReadbackFreshnessSettings(Protocol):
    """Runtime settings that define the trusted readback age budget."""

    @property
    def _worker_poll_interval_seconds(self) -> object: ...

    @property
    def auto_shelly_soft_fail_seconds(self) -> object: ...


@dataclass(frozen=True, slots=True)
class FreshReadbacks:
    """Fresh charger and switch snapshots resolved for one timestamp."""

    charger: TimedChargerState | None
    switch: TimedSwitchState | None


class ReadbackResolver:
    """Apply one centralized freshness policy to atomic store snapshots."""

    DEFAULT_MAX_AGE_SECONDS = 2.0
    MIN_MAX_AGE_SECONDS = 1.0

    def __init__(
        self,
        store: ReadbackStore,
        settings: ReadbackFreshnessSettings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._settings = settings
        self._clock = clock

    def current_time(self, now: float | None = None) -> float:
        """Return an explicit timestamp or the injected runtime clock."""
        return float(self._clock()) if now is None else float(now)

    def max_age_seconds(self) -> float:
        """Return the strictest positive runtime freshness budget."""
        candidates = [self.DEFAULT_MAX_AGE_SECONDS]
        poll_interval = self._positive_candidate(self._settings._worker_poll_interval_seconds)
        if poll_interval is not None:
            candidates.append(poll_interval * 2.0)
        soft_fail = self._positive_candidate(self._settings.auto_shelly_soft_fail_seconds)
        if soft_fail is not None:
            candidates.append(soft_fail)
        return max(self.MIN_MAX_AGE_SECONDS, min(candidates))

    def resolve(self, now: float | None = None) -> FreshReadbacks:
        """Return only snapshots inside the inclusive symmetric age boundary."""
        current = self.current_time(now)
        snapshots = self._store.snapshot()
        max_age = self.max_age_seconds()
        return FreshReadbacks(
            charger=self._fresh_snapshot(snapshots.charger, current, max_age),
            switch=self._fresh_snapshot(snapshots.switch, current, max_age),
        )

    @staticmethod
    def _positive_candidate(value: object) -> float | None:
        candidate = finite_float_or_none(value)
        if candidate is None or candidate <= 0.0:
            return None
        return float(candidate)

    @staticmethod
    def _fresh_snapshot(
        snapshot: TimedSnapshot | None,
        now: float,
        max_age: float,
    ) -> TimedSnapshot | None:
        if snapshot is None:
            return None
        if not math.isfinite(snapshot.captured_at):
            return None
        if abs(float(now) - snapshot.captured_at) > max_age:
            return None
        return snapshot


__all__ = ["FreshReadbacks", "ReadbackFreshnessSettings", "ReadbackResolver"]
