# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal building blocks for the dedicated DBus adapter process."""

from __future__ import annotations

import time
from typing import Any, Callable, Literal, Mapping

import dbus

from venus_evcharger.dbus_gateway import LatencyWindow, write_json_file


class DbusOperationDeferred(RuntimeError):
    """Raised when rate limiting defers one DBus operation without blocking."""


CommandOutcome = Literal["applied", "dropped", "deferred"]


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
        self.degraded_until = 0.0
        self.protective_until = 0.0
        self.degraded_seconds = max(1.0, float(degraded_seconds))
        self.protective_seconds = max(1.0, float(protective_seconds))
        self.last_success_at = 0.0
        self.last_error = ""

    def record_success(self, latency_ms: float) -> None:
        self.latencies.record_latency(latency_ms)
        self.last_success_at = time.time()
        self.last_error = ""

    def record_error(self, error: BaseException) -> None:
        self.last_error = str(error)
        if self._looks_like_timeout(error):
            now = time.time()
            self.latencies.record_timeout(now=now)
            count = int(self.latencies.summary(now=now)["timeouts_60s"])
            if count > 5:
                self.protective_until = max(self.protective_until, now + self.protective_seconds)
            elif count >= 3:
                self.degraded_until = max(self.degraded_until, now + self.degraded_seconds)

    def state(self, *, now: float | None = None) -> str:
        current = time.time() if now is None else float(now)
        if current < self.protective_until:
            return "protective"
        if current < self.degraded_until:
            return "degraded"
        return "ok"

    def allows_priority(self, priority: str) -> bool:
        rank = {"safety": 0, "user": 1, "publish": 2, "read": 3, "optional": 4, "discovery": 5, "diagnostic": 6}.get(
            str(priority or "diagnostic"),
            6,
        )
        state = self.state()
        if state == "protective":
            return rank <= 3
        if state == "degraded":
            return rank <= 4
        return True

    def health(self) -> dict[str, Any]:
        summary = self.latencies.summary()
        return {
            "state": self.state(),
            "degraded_until": max(self.degraded_until, self.protective_until),
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            **summary,
        }

    @staticmethod
    def _looks_like_timeout(error: BaseException) -> bool:
        detail = str(error).lower()
        getter = getattr(error, "get_dbus_name", None)
        name = ""
        if callable(getter):
            try:
                name = str(getter()).lower()
            except Exception:  # pylint: disable=broad-except
                name = ""
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
            try:
                close()
            except Exception:  # pylint: disable=broad-except
                pass
        self._bus = None


class DbusReadScheduler:
    """Track due times for fixed DBus read groups."""

    def __init__(self, specs: Mapping[str, Mapping[str, Any]]) -> None:
        self.specs: dict[str, dict[str, Any]] = {str(key): dict(value) for key, value in specs.items()}
        self.next_read_at: dict[str, float] = {key: 0.0 for key in self.specs}

    def next_due(
        self,
        *,
        now: float,
        circuit_state: str,
        priority_allowed: Callable[[str], bool],
    ) -> tuple[str, Mapping[str, Any], float] | None:
        for key, spec in self.specs.items():
            interval = self._effective_interval(spec, circuit_state)
            if now < self.next_read_at.get(key, 0.0):
                continue
            if priority_allowed(str(spec.get("priority", "read"))):
                return key, spec, interval
            return None
        return None

    def record_success(self, key: str, *, now: float, interval: float) -> None:
        self.next_read_at[str(key)] = float(now) + max(0.0, float(interval))

    def record_error(self, key: str, *, now: float, interval: float) -> None:
        self.record_success(key, now=now, interval=interval)

    @staticmethod
    def _effective_interval(spec: Mapping[str, Any], circuit_state: str) -> float:
        interval = float(spec.get("interval", 2.0))
        if circuit_state == "protective":
            return interval * 5.0
        if circuit_state == "degraded":
            return interval * 3.0
        return interval


class DbusDiscoveryManager:
    """Track service discovery cadence and diagnostic state."""

    def __init__(self, *, interval_seconds: float) -> None:
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.next_scan_at = 0.0
        self.last_success_at = 0.0
        self.last_error = ""

    def due(self, *, now: float, priority_allowed: Callable[[str], bool]) -> bool:
        return now >= self.next_scan_at and priority_allowed("discovery")

    def record_success(self, *, now: float) -> None:
        self.last_success_at = float(now)
        self.last_error = ""
        self.next_scan_at = float(now) + self.interval_seconds

    def record_error(self, error: BaseException, *, now: float) -> None:
        self.last_error = str(error)
        self.next_scan_at = float(now) + min(60.0, self.interval_seconds)


class AtomicJsonWriter:
    """Small explicit adapter-side wrapper for atomic JSON writes."""

    def write(self, path: str, payload: Mapping[str, Any]) -> None:
        write_json_file(path, payload)

