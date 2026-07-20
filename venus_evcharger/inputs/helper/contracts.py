# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow contracts between auto-input helper components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias, runtime_checkable

from venus_evcharger.dbus_gateway import GatewayReadKey
from venus_evcharger.dbus_gateway_command_types import CommandMapping
from venus_evcharger.energy import EnergySourceDefinition

Snapshot: TypeAlias = dict[str, object]
SubscriptionSpec: TypeAlias = tuple[str, str, str]


@runtime_checkable
class MainLoopPort(Protocol):  # pragma: no cover
    def run(self) -> None: ...

    def quit(self) -> None: ...


class GatewayReaderPort(Protocol):  # pragma: no cover
    def cached_value(self, service_name: str, path: str) -> float | int | None: ...

    def semantic_value(self, key: GatewayReadKey, *, reason: str) -> float | int | None: ...

    def service_names(self) -> list[str]: ...

    def service_available(self, service_name: str) -> bool: ...

    def request_value(self, service_name: str, path: str, *, priority: int, reason: str) -> None: ...

    def request_service_refresh(self) -> bool: ...

    def source_retry_ready(self, key: str) -> bool: ...

    def delay_source_retry(self, key: str) -> None: ...


class GatewayClientPathsPort(Protocol):  # pragma: no cover
    @property
    def cache_path(self) -> str: ...


class GatewayCommandClientPort(Protocol):  # pragma: no cover
    @property
    def paths(self) -> GatewayClientPathsPort: ...

    def enqueue_command(self, command: CommandMapping) -> str: ...

    def request_read_key(
        self,
        key: object,
        *,
        priority: str = "read",
        source: str = "core",
        reason: str = "",
    ) -> None: ...


class EnergySourceCatalogPort(Protocol):  # pragma: no cover
    def primary_source(self) -> EnergySourceDefinition: ...

    def primary_service_prefix(self) -> str: ...

    def source_has_readable_data(self, source: EnergySourceDefinition, service_name: str) -> bool: ...

    def battery_service_has_soc(self, service_name: str) -> bool: ...


class EnergyServiceResolverPort(Protocol):  # pragma: no cover
    def resolve(self, source: EnergySourceDefinition) -> str: ...

    def invalidate_primary(self) -> None: ...


class PvServiceResolverPort(Protocol):  # pragma: no cover
    def resolve_pv_services(self) -> list[str]: ...


class PvGridReaderPort(Protocol):  # pragma: no cover
    def pv_power(self) -> float | None: ...

    def grid_power(self) -> float | None: ...


class BatterySnapshotReaderPort(Protocol):  # pragma: no cover
    def battery_snapshot(self) -> Snapshot: ...


class SourceReaderPort(Protocol):  # pragma: no cover
    def pv_power(self) -> float | None: ...

    def battery_snapshot(self) -> Snapshot: ...

    def grid_power(self) -> float | None: ...


class SnapshotPort(Protocol):  # pragma: no cover
    def poll(self) -> bool: ...

    def refresh_source(self, source_name: str, now: float | None = None) -> None: ...

    def refresh_all(self, now: float | None = None) -> None: ...

    def heartbeat(self) -> bool: ...

    def write_lifecycle(self, state: str, now: float | None = None) -> None: ...


class SubscriptionPort(Protocol):  # pragma: no cover
    def refresh(self) -> bool: ...

    def schedule_refresh(self) -> None: ...

    def reset(self) -> None: ...


class SnapshotWriterPort(Protocol):  # pragma: no cover
    def write(self, payload: Mapping[str, object]) -> None: ...


class WarningSink(Protocol):  # pragma: no cover
    def __call__(self, key: str, interval_seconds: float, message: str, *args: object) -> None: ...
