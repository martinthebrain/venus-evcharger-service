# SPDX-License-Identifier: GPL-3.0-or-later
"""Rolling event-loop timing diagnostics."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.resource_metrics import average
from venus_evcharger.ipc.command_types import CommandPayload


@dataclass(frozen=True, slots=True)
class _TickSample:
    timestamp: float
    duration_ms: float
    late: bool
    gap_ms: float
    late_gap: bool
    callback_lateness_ms: float
    late_callback: bool
    scheduler_pause_ms: float


class TickHealth:
    """Track recent event-loop timing without touching DBus."""

    def __init__(self, *, window_seconds: float = 60.0) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self._ticks: deque[_TickSample] = deque()
        self._last_tick_start = 0.0

    def record(
        self,
        *,
        duration_ms: float,
        expected_interval_s: float,
        scheduled_at: float | None = None,
        now: float | None = None,
    ) -> None:
        current = time.monotonic() if now is None else float(now)
        late_limit_ms = max(0.0, float(expected_interval_s)) * 2000.0
        gap_ms = self._tick_gap_ms(current)
        callback_lateness_ms = self._callback_lateness_ms(
            current,
            scheduled_at,
        )
        self._last_tick_start = current
        self._ticks.append(
            _TickSample(
                current,
                max(0.0, float(duration_ms)),
                float(duration_ms) > late_limit_ms,
                gap_ms,
                gap_ms > late_limit_ms,
                callback_lateness_ms,
                callback_lateness_ms > late_limit_ms,
                max(0.0, float(expected_interval_s)) * 1000.0,
            )
        )
        self._prune(current)

    def snapshot(self, *, now: float | None = None) -> CommandPayload:
        current = time.monotonic() if now is None else float(now)
        self._prune(current)
        durations = self._durations()
        gaps = self._gaps()
        callback_lateness = self._callback_lateness()
        scheduler_pauses = self._scheduler_pauses()
        return {
            "tick_count_60s": len(self._ticks),
            "avg_tick_duration_ms_60s": average(durations),
            "max_tick_duration_ms_60s": self._maximum(durations),
            "late_ticks_60s": self._late_count(),
            "avg_tick_gap_ms_60s": average(gaps),
            "max_tick_gap_ms_60s": self._maximum(gaps),
            "late_tick_gap_count_60s": self._late_gap_count(),
            "avg_glib_callback_lateness_ms_60s": average(
                callback_lateness
            ),
            "max_glib_callback_lateness_ms_60s": self._maximum(
                callback_lateness
            ),
            "late_glib_callback_count_60s": self._late_callback_count(),
            "avg_scheduler_pause_ms_60s": average(scheduler_pauses),
            "max_scheduler_pause_ms_60s": self._maximum(scheduler_pauses),
            "avg_blocking_time_ms_60s": average(durations),
            "max_blocking_time_ms_60s": self._maximum(durations),
        }

    def _durations(self) -> list[float]:
        return [sample.duration_ms for sample in self._ticks]

    def _gaps(self) -> list[float]:
        return [sample.gap_ms for sample in self._ticks if sample.gap_ms > 0.0]

    def _late_count(self) -> int:
        return sum(sample.late for sample in self._ticks)

    def _late_gap_count(self) -> int:
        return sum(sample.late_gap for sample in self._ticks)

    def _callback_lateness(self) -> list[float]:
        return [sample.callback_lateness_ms for sample in self._ticks]

    def _late_callback_count(self) -> int:
        return sum(sample.late_callback for sample in self._ticks)

    def _scheduler_pauses(self) -> list[float]:
        return [sample.scheduler_pause_ms for sample in self._ticks]

    @staticmethod
    def _maximum(values: list[float]) -> float:
        return max(values) if values else 0.0

    def _tick_gap_ms(self, current: float) -> float:
        if self._last_tick_start <= 0.0:
            return 0.0
        return max(0.0, (current - self._last_tick_start) * 1000.0)

    @staticmethod
    def _callback_lateness_ms(
        current: float,
        scheduled_at: float | None,
    ) -> float:
        if scheduled_at is None or scheduled_at <= 0.0:
            return 0.0
        return max(0.0, (current - scheduled_at) * 1000.0)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._ticks and self._ticks[0].timestamp < cutoff:
            self._ticks.popleft()
