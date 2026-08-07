# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway latency windows."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Mapping
from typing import TypedDict


class LatencySummary(TypedDict):
    samples_60s: int
    timeouts_60s: int
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float


def _empty_latency_summary() -> LatencySummary:
    return {
        "samples_60s": 0,
        "timeouts_60s": 0,
        "avg_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "p99_latency_ms": 0.0,
        "max_latency_ms": 0.0,
    }


class LatencyWindow:
    """Rolling latency/error window used by the adapter circuit breaker."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self._latencies: deque[tuple[float, float]] = deque()
        self._timeouts: deque[float] = deque()

    def record_latency(self, latency_ms: float, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        self._latencies.append((current, max(0.0, float(latency_ms))))
        self._prune(current)

    def record_timeout(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        self._timeouts.append(current)
        self._prune(current)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._latencies and self._latencies[0][0] < cutoff:
            self._latencies.popleft()
        while self._timeouts and self._timeouts[0] < cutoff:
            self._timeouts.popleft()

    def summary(self, *, now: float | None = None) -> LatencySummary:
        current = time.monotonic() if now is None else float(now)
        self._prune(current)
        latencies = [latency for _timestamp, latency in self._latencies]
        return {
            "samples_60s": len(latencies),
            "timeouts_60s": len(self._timeouts),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "p95_latency_ms": _percentile(latencies, 0.95),
            "p99_latency_ms": _percentile(latencies, 0.99),
            "max_latency_ms": max(latencies) if latencies else 0.0,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(float(fraction) * len(ordered)))
    return ordered[rank - 1]


ATTRIBUTION_OVERFLOW_KEY = "<other>"


class BoundedLatencyAttribution:
    """Keep source-level latency windows within fixed memory bounds."""

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        max_operation_kinds: int = 8,
        max_sources_per_kind: int = 16,
    ) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_operation_kinds = max(1, int(max_operation_kinds))
        self.max_sources_per_kind = max(1, int(max_sources_per_kind))
        self._windows: dict[str, dict[str, LatencyWindow]] = {}

    def record_latency(
        self,
        kind: str,
        source: str,
        latency_ms: float,
        *,
        now: float | None = None,
    ) -> None:
        self._window(kind, source).record_latency(latency_ms, now=now)

    def record_timeout(
        self,
        kind: str,
        source: str,
        *,
        now: float | None = None,
    ) -> None:
        self._window(kind, source).record_timeout(now=now)

    def source_summary(
        self,
        kind: str,
        source: str,
        *,
        now: float | None = None,
    ) -> LatencySummary:
        window = self._existing_window(kind, source)
        if window is None:
            return _empty_latency_summary()
        return window.summary(now=now)

    def summary(
        self,
        *,
        now: float | None = None,
    ) -> dict[str, dict[str, LatencySummary]]:
        return {
            kind: {
                source: window.summary(now=now)
                for source, window in sorted(sources.items())
            }
            for kind, sources in sorted(self._windows.items())
        }

    def _window(self, kind: str, source: str) -> LatencyWindow:
        operation_key = self._bounded_key(
            self._windows,
            kind,
            self.max_operation_kinds,
        )
        sources = self._windows.setdefault(operation_key, {})
        source_key = self._bounded_key(
            sources,
            source,
            self.max_sources_per_kind,
        )
        return sources.setdefault(
            source_key,
            LatencyWindow(self.window_seconds),
        )

    def _existing_window(self, kind: str, source: str) -> LatencyWindow | None:
        sources = self._windows.get(kind)
        if sources is None:
            sources = self._windows.get(ATTRIBUTION_OVERFLOW_KEY)
        if sources is None:
            return None
        window = sources.get(source)
        return sources.get(ATTRIBUTION_OVERFLOW_KEY) if window is None else window

    @staticmethod
    def _bounded_key(
        values: Mapping[str, object],
        requested: str,
        limit: int,
    ) -> str:
        if requested in values:
            return requested
        if requested != ATTRIBUTION_OVERFLOW_KEY and len(values) < limit - 1:
            return requested
        return ATTRIBUTION_OVERFLOW_KEY
