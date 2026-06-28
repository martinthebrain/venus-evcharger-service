# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts used by the DBus gateway read executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from venus_evcharger.dbus_adapter_read_types import ReadSpec


class ReadCacheProtocol(Protocol):  # pragma: no cover
    """Cache surface required by scheduled DBus reads."""

    @property
    def services(self) -> Mapping[str, Mapping[str, Any]]: ...

    @property
    def values(self) -> Mapping[str, Mapping[str, Any]]: ...

    def update_value(
        self,
        key: str,
        value: Any,
        *,
        metadata: Any | None = None,
        **metadata_fields: Any,
    ) -> None: ...

    def mark_error(
        self,
        key: str,
        *,
        source: str,
        error: BaseException | str,
        now: float | None = None,
    ) -> None: ...


class ReadSchedulerProtocol(Protocol):  # pragma: no cover
    """Read-scheduler surface required by requested refreshes."""

    @property
    def specs(self) -> Mapping[str, ReadSpec]: ...


class ReadConnectionProtocol(Protocol):  # pragma: no cover
    """DBus connection surface required by read execution."""

    def bus(self) -> Any: ...


class ReadRateLimiterProtocol(Protocol):  # pragma: no cover
    """Rate limiter surface required by direct read execution."""

    def require_due(self, kind: str) -> None: ...


class ReadCircuitProtocol(Protocol):  # pragma: no cover
    """Circuit-breaker surface required by direct read execution."""

    def record_success(self, latency_ms: float, *, kind: str = "dbus") -> None: ...


class DbusReadAdapter(Protocol):  # pragma: no cover
    """Adapter surface required by the DBus read executor."""

    @property
    def cache(self) -> ReadCacheProtocol: ...

    @property
    def connection(self) -> ReadConnectionProtocol: ...

    @property
    def read_scheduler(self) -> ReadSchedulerProtocol: ...

    @property
    def rate_limiter(self) -> ReadRateLimiterProtocol: ...

    @property
    def circuit(self) -> ReadCircuitProtocol: ...

    def timed_dbus_operation(self, kind: str, operation: Callable[[], Any]) -> Any: ...
