# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded health counters for the transient publication queue."""

from __future__ import annotations

from venus_evcharger.ipc.command_types import CommandPayload

FAST_PUBLICATION_SUCCESS_SAMPLE_COUNT = 25
FAST_PUBLICATION_SUCCESS_SAMPLE_SECONDS = 5.0


class FastPublicationMetrics:
    """Track queue outcomes and sparsely sample successful publications."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._last_success_sample_at = 0.0
        self._successes_since_sample = 0

    def increment(self, key: str, amount: int = 1) -> None:
        self._counts[key] = self._counts.get(key, 0) + amount

    def record_outcome(self, state: str, now: float) -> bool:
        self.increment(state)
        if state != "applied":
            return True
        self._successes_since_sample += 1
        if not self._success_sample_due(now):
            return False
        self._last_success_sample_at = now
        self._successes_since_sample = 0
        self.increment("applied_samples")
        return True

    def snapshot(self) -> CommandPayload:
        return {
            "counts": dict(sorted(self._counts.items())),
            "last_success_sample_at": self._last_success_sample_at,
            "successes_since_sample": self._successes_since_sample,
        }

    def _success_sample_due(self, now: float) -> bool:
        return (
            self._last_success_sample_at <= 0.0
            or self._successes_since_sample >= FAST_PUBLICATION_SUCCESS_SAMPLE_COUNT
            or now - self._last_success_sample_at >= FAST_PUBLICATION_SUCCESS_SAMPLE_SECONDS
        )


__all__ = ["FastPublicationMetrics"]
