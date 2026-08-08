# SPDX-License-Identifier: GPL-3.0-or-later
"""Static boundaries required by asynchronous DBus operation execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from venus_evcharger.dbus_adapter.async_request import DbusWireRequest


class AsyncRateLimiter(Protocol):  # pragma: no cover - structural contract
    """Rate-limiter boundary used by the broker."""

    def require_due(self, kind: str) -> None: ...


class AsyncCircuitBreaker(Protocol):  # pragma: no cover - structural contract
    """Circuit metrics required by asynchronous operation execution."""

    def record_success(
        self,
        latency_ms: float,
        *,
        kind: str = "dbus",
        source: str = "",
    ) -> None: ...

    def record_error(
        self,
        error: BaseException,
        *,
        kind: str = "dbus",
        source: str = "",
        latency_ms: float | None = None,
    ) -> None: ...

    def record_optional_source_failure(
        self,
        error: BaseException,
        *,
        source: str,
        latency_ms: float,
    ) -> None: ...


class AsyncDbusConnection(Protocol):  # pragma: no cover - structural contract
    """Submit low-level callback DBus calls through one private connection."""

    def send_async(
        self,
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object: ...


class DbusConnectionLifecycle(Protocol):  # pragma: no cover
    """Own the complete process-level connection lifecycle and transport."""

    def send_async(
        self,
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object: ...
    def connect(self) -> None: ...
    def reset(self) -> None: ...


__all__ = [
    "AsyncCircuitBreaker",
    "AsyncDbusConnection",
    "AsyncRateLimiter",
    "DbusConnectionLifecycle",
]
