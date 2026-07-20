# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for scheduled DBus reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar, Unpack

from venus_evcharger.dbus_adapter.read.spec import ReadSpec
from venus_evcharger.dbus_gateway_cache import CacheValueMetadata, ExternalReadMetadata
from venus_evcharger.dbus_gateway_command_types import CommandPayload
from venus_evcharger.dbus_gateway_core import CacheFreshnessKind

_T = TypeVar("_T")


class ReadCacheProtocol(Protocol):  # pragma: no cover
    """Cache surface required by scheduled DBus reads."""

    @property
    def services(self) -> Mapping[str, CommandPayload]: ...

    @property
    def values(self) -> Mapping[str, CommandPayload]: ...

    def update_value(
        self,
        key: str,
        value: object,
        *,
        metadata: CacheValueMetadata | None = None,
        **metadata_fields: object,
    ) -> None: ...

    def update_external_read(
        self,
        key: str,
        value: object,
        **metadata_fields: Unpack[ExternalReadMetadata],
    ) -> None: ...

    def mark_error(
        self,
        key: str,
        *,
        source: str,
        error: BaseException | str,
        now: float | None = None,
        freshness_kind: CacheFreshnessKind | None = None,
    ) -> None: ...


class ReadSchedulerProtocol(Protocol):  # pragma: no cover
    """Read-scheduler surface required by requested refreshes."""

    @property
    def specs(self) -> Mapping[str, ReadSpec]: ...


class ReadConnectionProtocol(Protocol):  # pragma: no cover
    """DBus connection surface required by read execution."""

    def get_object(self, bus_name: str, object_path: str, *, introspect: bool = False) -> object: ...


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

    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...
