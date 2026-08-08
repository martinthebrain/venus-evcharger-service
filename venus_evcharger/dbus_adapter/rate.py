# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus rate limiting, connection management, and circuit breaking."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress

import dbus

from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
from venus_evcharger.dbus_gateway import LatencyWindow
from venus_evcharger.dbus_gateway_latency import BoundedLatencyAttribution
from venus_evcharger.ipc.command_types import CommandPayload

DBUS_OPERATION_KIND = "dbus"
DBUS_EXCEPTION_ATTRIBUTE = "DBusException"


def _dbus_exception_type() -> type[BaseException]:
    if not hasattr(dbus, DBUS_EXCEPTION_ATTRIBUTE):
        return RuntimeError
    candidate = getattr(dbus, DBUS_EXCEPTION_ATTRIBUTE)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return candidate
    return RuntimeError


DBUS_DEGRADED_TIMEOUTS_PER_MINUTE = 3
DBUS_PROTECTIVE_TIMEOUTS_PER_MINUTE = 5
DBUS_LATENCY_OPERATION_KIND_LIMIT = 8
DBUS_LATENCY_SOURCES_PER_KIND_LIMIT = 16
OPTIONAL_SLOW_SOURCE_MIN_SAMPLES = 3
OPTIONAL_SLOW_SOURCE_P95_MS = 250.0
OPTIONAL_VERY_SLOW_SOURCE_P99_MS = 750.0
OPTIONAL_SLOW_SOURCE_INTERVAL_FACTOR = 3.0
OPTIONAL_VERY_SLOW_SOURCE_INTERVAL_FACTOR = 5.0
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
DBUS_GATEWAY_OPERATION_ERRORS: tuple[type[BaseException], ...] = (
    _dbus_exception_type(),
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _normalized_kind(kind: str) -> str:
    normalized = str(kind).strip()
    return normalized if normalized else DBUS_OPERATION_KIND


def _normalized_priority(priority: str) -> str:
    normalized = str(priority).strip().lower()
    return normalized if normalized else "diagnostic"


def _epoch_deadline_to_monotonic(deadline: float) -> float:
    if deadline <= 0.0:
        return 0.0
    return time.monotonic() + max(0.0, deadline - time.time())


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
        self.latencies_by_source = BoundedLatencyAttribution(
            max_operation_kinds=DBUS_LATENCY_OPERATION_KIND_LIMIT,
            max_sources_per_kind=DBUS_LATENCY_SOURCES_PER_KIND_LIMIT,
        )
        self._degraded_until_monotonic = 0.0
        self._protective_until_monotonic = 0.0
        self._degraded_until_epoch = 0.0
        self._protective_until_epoch = 0.0
        self.degraded_seconds = max(1.0, float(degraded_seconds))
        self.protective_seconds = max(1.0, float(protective_seconds))
        self.last_success_at = 0.0
        self.last_error = ""
        self._errors: deque[tuple[float, str]] = deque()
        self._successes: deque[tuple[float, str]] = deque()
        self.consecutive_failures = 0

    @property
    def degraded_until(self) -> float:
        return self._degraded_until_epoch

    @degraded_until.setter
    def degraded_until(self, value: float) -> None:
        self._degraded_until_epoch = max(0.0, float(value))
        self._degraded_until_monotonic = _epoch_deadline_to_monotonic(
            self._degraded_until_epoch
        )

    @property
    def protective_until(self) -> float:
        return self._protective_until_epoch

    @protective_until.setter
    def protective_until(self, value: float) -> None:
        self._protective_until_epoch = max(0.0, float(value))
        self._protective_until_monotonic = _epoch_deadline_to_monotonic(
            self._protective_until_epoch
        )

    def record_success(
        self,
        latency_ms: float,
        *,
        kind: str = DBUS_OPERATION_KIND,
        source: str = "",
    ) -> None:
        captured_at = time.time()
        monotonic_at = time.monotonic()
        normalized_kind = _normalized_kind(kind)
        self._record_latency(
            latency_ms,
            kind=normalized_kind,
            source=source,
            monotonic_at=monotonic_at,
        )
        self._successes.append((monotonic_at, normalized_kind))
        self._prune_events(monotonic_at)
        self.last_success_at = captured_at
        self.last_error = ""
        self.consecutive_failures = 0

    def record_error(
        self,
        error: BaseException,
        *,
        kind: str = DBUS_OPERATION_KIND,
        source: str = "",
        latency_ms: float | None = None,
    ) -> None:
        captured_at = time.time()
        monotonic_at = time.monotonic()
        normalized_kind = _normalized_kind(kind)
        self.last_error = str(error)
        self._errors.append((monotonic_at, normalized_kind))
        self._prune_events(monotonic_at)
        self.consecutive_failures += 1
        if latency_ms is not None:
            self._record_latency(
                latency_ms,
                kind=normalized_kind,
                source=source,
                monotonic_at=monotonic_at,
            )
        if self._looks_like_timeout(error):
            self._record_timeout(
                kind=normalized_kind,
                source=source,
                monotonic_at=monotonic_at,
            )
            count = self.latencies.summary(now=monotonic_at)["timeouts_60s"]
            if count > DBUS_PROTECTIVE_TIMEOUTS_PER_MINUTE:
                self._extend_protective_deadline(
                    monotonic_at=monotonic_at,
                    captured_at=captured_at,
                )
            elif count >= DBUS_DEGRADED_TIMEOUTS_PER_MINUTE:
                self._extend_degraded_deadline(
                    monotonic_at=monotonic_at,
                    captured_at=captured_at,
                )

    def state(
        self,
        *,
        now: float | None = None,
        monotonic_at: float | None = None,
    ) -> str:
        if now is not None:
            return self._state_for_epoch(float(now))
        current = time.monotonic() if monotonic_at is None else float(monotonic_at)
        if current < self._protective_until_monotonic:
            return "protective"
        if current < self._degraded_until_monotonic:
            return "degraded"
        return "ok"

    def allows_priority(self, priority: str) -> bool:
        rank = PRIORITY_RANKS.get(_normalized_priority(priority), DEFAULT_PRIORITY_RANK)
        state = self.state()
        if state == "protective":
            return rank <= PROTECTIVE_MAX_ALLOWED_PRIORITY_RANK
        if state == "degraded":
            return rank <= DEGRADED_MAX_ALLOWED_PRIORITY_RANK
        return True

    def health(self) -> CommandPayload:
        monotonic_at = time.monotonic()
        self._prune_events(monotonic_at)
        summary = self.latencies.summary(now=monotonic_at)
        operations = {
            kind: window.summary(now=monotonic_at)
            for kind, window in sorted(self.latencies_by_kind.items())
        }
        return {
            "state": self.state(),
            "degraded_until": max(self.degraded_until, self.protective_until),
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "errors_60s": len(self._errors),
            "successes_60s": len(self._successes),
            "consecutive_failures": self.consecutive_failures,
            "operations": operations,
            "operation_sources": self.latencies_by_source.summary(
                now=monotonic_at
            ),
            **summary,
        }

    def optional_source_interval_factor(self, source: str) -> float:
        """Return a conservative scheduler multiplier for one optional source."""
        summary = self.latencies_by_source.source_summary(
            "optional_read",
            str(source),
            now=time.monotonic(),
        )
        if summary["samples_60s"] < OPTIONAL_SLOW_SOURCE_MIN_SAMPLES:
            return 1.0
        if summary["p99_latency_ms"] >= OPTIONAL_VERY_SLOW_SOURCE_P99_MS:
            return OPTIONAL_VERY_SLOW_SOURCE_INTERVAL_FACTOR
        if summary["p95_latency_ms"] >= OPTIONAL_SLOW_SOURCE_P95_MS:
            return OPTIONAL_SLOW_SOURCE_INTERVAL_FACTOR
        return 1.0

    def record_optional_source_failure(
        self,
        error: BaseException,
        *,
        source: str,
        latency_ms: float,
    ) -> None:
        """Measure an optional source failure without tripping the circuit."""
        monotonic_at = time.monotonic()
        self._record_latency(
            latency_ms,
            kind="optional_read",
            source=source,
            monotonic_at=monotonic_at,
        )
        if source and self._looks_like_timeout(error):
            self.latencies_by_source.record_timeout(
                "optional_read",
                str(source),
                now=monotonic_at,
            )

    def _record_latency(
        self,
        latency_ms: float,
        *,
        kind: str,
        source: str,
        monotonic_at: float,
    ) -> None:
        self.latencies.record_latency(latency_ms, now=monotonic_at)
        self._kind_window(kind).record_latency(latency_ms, now=monotonic_at)
        if source:
            self.latencies_by_source.record_latency(
                kind,
                str(source),
                latency_ms,
                now=monotonic_at,
            )

    def _record_timeout(
        self,
        *,
        kind: str,
        source: str,
        monotonic_at: float,
    ) -> None:
        self.latencies.record_timeout(now=monotonic_at)
        self._kind_window(kind).record_timeout(now=monotonic_at)
        if source:
            self.latencies_by_source.record_timeout(
                kind,
                str(source),
                now=monotonic_at,
            )

    def _extend_degraded_deadline(
        self,
        *,
        monotonic_at: float,
        captured_at: float,
    ) -> None:
        candidate = monotonic_at + self.degraded_seconds
        if candidate > self._degraded_until_monotonic:
            self._degraded_until_monotonic = candidate
            self._degraded_until_epoch = captured_at + self.degraded_seconds

    def _extend_protective_deadline(
        self,
        *,
        monotonic_at: float,
        captured_at: float,
    ) -> None:
        candidate = monotonic_at + self.protective_seconds
        if candidate > self._protective_until_monotonic:
            self._protective_until_monotonic = candidate
            self._protective_until_epoch = captured_at + self.protective_seconds

    def _state_for_epoch(self, captured_at: float) -> str:
        if captured_at < self.protective_until:
            return "protective"
        if captured_at < self.degraded_until:
            return "degraded"
        return "ok"

    def _kind_window(self, kind: str) -> LatencyWindow:
        normalized = _normalized_kind(kind)
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
        return dbus_error_is_timeout(error)


class DbusConnectionManager:
    """Own the private system bus connection."""

    def __init__(self) -> None:
        self._bus: object | None = None

    def bus(self) -> object:
        if self._bus is None:
            self._bus = dbus.SystemBus(private=True)
        return self._bus

    def connect(self) -> None:
        """Establish the private bus connection before the GLib loop starts."""
        self.bus()

    def send_async(
        self,
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object:
        """Return the real dbus-python PendingCall for one bounded request."""
        raw_call_async = getattr(self.bus(), "call_async", None)
        if not callable(raw_call_async):
            raise TypeError("DBus bus does not provide call_async")
        call_async: Callable[..., object] = raw_call_async
        return call_async(
            request.service,
            request.path,
            request.interface,
            request.method_name,
            request.signature,
            request.args,
            reply_handler,
            error_handler,
            timeout=request.timeout_seconds,
            require_main_loop=True,
        )

    def reset(self) -> None:
        close = getattr(self._bus, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        self._bus = None


def dbus_error_is_timeout(error: BaseException) -> bool:
    """Return whether a DBus failure represents a transient missing reply."""
    if isinstance(error, TimeoutError):
        return True
    detail = str(error).lower()
    name = _dbus_error_name(error)
    reply_markers = ("timeout", "timed out", "noreply", "no_reply")
    return any(marker in detail for marker in reply_markers) or "noreply" in name


def _dbus_error_name(error: BaseException) -> str:
    getter = getattr(error, "get_dbus_name", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).lower()
    except DBUS_GATEWAY_OPERATION_ERRORS:
        return ""
