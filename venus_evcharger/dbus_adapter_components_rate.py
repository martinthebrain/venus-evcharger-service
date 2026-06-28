# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter component helpers."""

from __future__ import annotations

import time
from collections import deque
from contextlib import suppress
from typing import Any

import dbus

from venus_evcharger.dbus_gateway import LatencyWindow
from venus_evcharger.dbus_gateway_command_types import CommandPayload

DBUS_DEGRADED_TIMEOUTS_PER_MINUTE = 3
DBUS_PROTECTIVE_TIMEOUTS_PER_MINUTE = 5
PRIORITY_RANKS = {
    "safety": 0,
    "user": 1,
    "publish": 2,
    "read": 3,
    "optional": 4,
    "discovery": 5,
    "diagnostic": 6,
}
PROTECTIVE_MAX_ALLOWED_PRIORITY_RANK = PRIORITY_RANKS["read"]
DEGRADED_MAX_ALLOWED_PRIORITY_RANK = PRIORITY_RANKS["optional"]
DEFAULT_PRIORITY_RANK = PRIORITY_RANKS["diagnostic"]
_DBUS_EXCEPTION_TYPE: type[BaseException] = getattr(dbus, "DBusException", RuntimeError)
DBUS_GATEWAY_OPERATION_ERRORS: tuple[type[BaseException], ...] = (
    _DBUS_EXCEPTION_TYPE,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class DbusOperationDeferred(RuntimeError):
    """Raised when rate limiting defers one DBus operation without blocking."""


class DbusRateLimiter:
    """Global DBus rate limiter for reads, writes, and introspection."""

    def __init__(
        self,
        *,
        read_interval_seconds: float = 0.25,
        write_interval_seconds: float = 0.35,
        introspection_interval_seconds: float = 2.0,
    ) -> None:
        self.intervals = {
            "read": max(0.0, float(read_interval_seconds)),
            "write": max(0.0, float(write_interval_seconds)),
            "introspection": max(0.0, float(introspection_interval_seconds)),
        }
        self.next_at = {"read": 0.0, "write": 0.0, "introspection": 0.0}

    def due(self, kind: str, *, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else float(now)) >= self.next_at[kind]

    def mark(self, kind: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        self.next_at[kind] = current + self.intervals[kind]

    def require_due(self, kind: str) -> None:
        if not self.due(kind):
            raise DbusOperationDeferred(kind)
        self.mark(kind)


class DbusCircuitBreaker:
    """Classify DBus health and suppress optional work when unstable."""

    def __init__(self, *, degraded_seconds: float = 60.0, protective_seconds: float = 180.0) -> None:
        self.latencies = LatencyWindow()
        self.latencies_by_kind: dict[str, LatencyWindow] = {}
        self.degraded_until = 0.0
        self.protective_until = 0.0
        self.degraded_seconds = max(1.0, float(degraded_seconds))
        self.protective_seconds = max(1.0, float(protective_seconds))
        self.last_success_at = 0.0
        self.last_error = ""
        self._errors: deque[tuple[float, str]] = deque()
        self._successes: deque[tuple[float, str]] = deque()
        self.consecutive_failures = 0

    def record_success(self, latency_ms: float, *, kind: str = "dbus") -> None:
        now = time.time()
        self.latencies.record_latency(latency_ms, now=now)
        self._kind_window(kind).record_latency(latency_ms, now=now)
        self._successes.append((now, str(kind or "dbus")))
        self._prune_events(now)
        self.last_success_at = now
        self.last_error = ""
        self.consecutive_failures = 0

    def record_error(self, error: BaseException, *, kind: str = "dbus") -> None:
        now = time.time()
        self.last_error = str(error)
        self._errors.append((now, str(kind or "dbus")))
        self._prune_events(now)
        self.consecutive_failures += 1
        if self._looks_like_timeout(error):
            self.latencies.record_timeout(now=now)
            self._kind_window(kind).record_timeout(now=now)
            count = int(self.latencies.summary(now=now)["timeouts_60s"])
            if count > DBUS_PROTECTIVE_TIMEOUTS_PER_MINUTE:
                self.protective_until = max(self.protective_until, now + self.protective_seconds)
            elif count >= DBUS_DEGRADED_TIMEOUTS_PER_MINUTE:
                self.degraded_until = max(self.degraded_until, now + self.degraded_seconds)

    def state(self, *, now: float | None = None) -> str:
        current = time.time() if now is None else float(now)
        if current < self.protective_until:
            return "protective"
        if current < self.degraded_until:
            return "degraded"
        return "ok"

    def allows_priority(self, priority: str) -> bool:
        rank = PRIORITY_RANKS.get(str(priority or "diagnostic"), DEFAULT_PRIORITY_RANK)
        state = self.state()
        if state == "protective":
            return rank <= PROTECTIVE_MAX_ALLOWED_PRIORITY_RANK
        if state == "degraded":
            return rank <= DEGRADED_MAX_ALLOWED_PRIORITY_RANK
        return True

    def health(self) -> CommandPayload:
        now = time.time()
        self._prune_events(now)
        summary = self.latencies.summary(now=now)
        operations = {kind: window.summary(now=now) for kind, window in sorted(self.latencies_by_kind.items())}
        return {
            "state": self.state(),
            "degraded_until": max(self.degraded_until, self.protective_until),
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "errors_60s": len(self._errors),
            "successes_60s": len(self._successes),
            "consecutive_failures": self.consecutive_failures,
            "operations": operations,
            **summary,
        }

    def _kind_window(self, kind: str) -> LatencyWindow:
        normalized = str(kind or "dbus")
        if normalized not in self.latencies_by_kind:
            self.latencies_by_kind[normalized] = LatencyWindow()
        return self.latencies_by_kind[normalized]

    def _prune_events(self, now: float) -> None:
        cutoff = now - 60.0
        while self._errors and self._errors[0][0] < cutoff:
            self._errors.popleft()
        while self._successes and self._successes[0][0] < cutoff:
            self._successes.popleft()

    @staticmethod
    def _looks_like_timeout(error: BaseException) -> bool:
        detail = str(error).lower()
        name = _dbus_error_name(error)
        return "timeout" in detail or "noreply" in detail or "no_reply" in detail or "noreply" in name


class DbusConnectionManager:
    """Own the private system bus connection."""

    def __init__(self) -> None:
        self._bus: Any = None

    def bus(self) -> Any:
        if self._bus is None:
            self._bus = dbus.SystemBus(private=True)
        return self._bus

    def reset(self) -> None:
        close = getattr(self._bus, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        self._bus = None


def _dbus_error_name(error: BaseException) -> str:
    getter = getattr(error, "get_dbus_name", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).lower()
    except DBUS_GATEWAY_OPERATION_ERRORS:
        return ""
