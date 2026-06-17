# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway latency windows."""

from __future__ import annotations

from collections import deque
from typing import Any

from venus_evcharger.dbus_gateway_core import _now

class LatencyWindow:
    """Rolling latency/error window used by the adapter circuit breaker."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self._latencies: deque[tuple[float, float]] = deque()
        self._timeouts: deque[float] = deque()

    def record_latency(self, latency_ms: float, *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self._latencies.append((current, max(0.0, float(latency_ms))))
        self._prune(current)

    def record_timeout(self, *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self._timeouts.append(current)
        self._prune(current)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._latencies and self._latencies[0][0] < cutoff:
            self._latencies.popleft()
        while self._timeouts and self._timeouts[0] < cutoff:
            self._timeouts.popleft()

    def summary(self, *, now: float | None = None) -> dict[str, Any]:
        current = _now() if now is None else float(now)
        self._prune(current)
        latencies = [latency for _timestamp, latency in self._latencies]
        return {
            "timeouts_60s": len(self._timeouts),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "max_latency_ms": max(latencies) if latencies else 0.0,
        }

