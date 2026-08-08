# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for scheduled DBus reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, Unpack

from venus_evcharger.dbus_adapter.async_broker import DbusAsyncOperationBroker
from venus_evcharger.dbus_adapter.async_protocols import AsyncDbusConnection
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.spec import ReadSpec
from venus_evcharger.dbus_gateway_cache_metadata import CacheValueMetadata, ExternalReadMetadata
from venus_evcharger.dbus_gateway_core import CacheFreshnessKind
from venus_evcharger.ipc.command_types import CommandPayload


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

    def mark_unavailable(
        self,
        key: str,
        *,
        source: str,
        error: BaseException | str,
        retry_after_seconds: float,
        now: float | None = None,
    ) -> None: ...


class ReadSchedulerProtocol(Protocol):  # pragma: no cover
    """Read-scheduler surface required by requested refreshes."""

    @property
    def specs(self) -> Mapping[str, ReadSpec]: ...


class ReadCircuitProtocol(Protocol):  # pragma: no cover
    """Circuit-breaker surface required by direct read execution."""

    def optional_source_interval_factor(self, source: str) -> float: ...


class DbusReadAdapter(Protocol):  # pragma: no cover
    """Adapter surface required by the DBus read executor."""

    @property
    def cache(self) -> ReadCacheProtocol: ...

    @property
    def connection(self) -> AsyncDbusConnection: ...

    @property
    def read_scheduler(self) -> ReadSchedulerProtocol: ...

    @property
    def energy_discovery(self) -> DbusEnergyDiscoveryManager: ...

    @property
    def circuit(self) -> ReadCircuitProtocol: ...

    @property
    def operation_broker(self) -> DbusAsyncOperationBroker: ...
