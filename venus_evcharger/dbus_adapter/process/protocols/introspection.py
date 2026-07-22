#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Introspection contracts for adapter process components."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Protocol, TypeVar

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.scheduling import DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.energy import EnergyRefreshRequest

_T = TypeVar("_T")


class DbusAdapterIntrospectionContext(Protocol):  # pragma: no cover
    """Introspection request and background-discovery surface."""

    config: configparser.ConfigParser
    connection: DbusConnectionManager
    circuit: DbusCircuitBreaker
    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    discovery: DbusDiscoveryManager
    energy_discovery: DbusEnergyDiscoveryManager
    read_executor: DbusReadExecutor
    read_scheduler: DbusReadScheduler
    dbus_introspection_enabled: bool
    _introspection_queue_depth: int
    _last_introspection_full_scan_at: float

    def enqueue_introspection_command(
        self,
        service: str,
        path: str,
        *,
        priority: int,
        source: str,
        reason: str,
    ) -> None: ...
    def background_introspection_due(self, now: float) -> bool: ...
    def refresh_energy_inputs_command(self, command: CommandMapping) -> CommandOutcome: ...
    def _energy_refresh_keys(self, request: EnergyRefreshRequest) -> tuple[str, ...]: ...
    def _stale_refresh_keys(self, keys: tuple[str, ...], max_age_seconds: float) -> tuple[str, ...]: ...
    def introspect_command_if_healthy(self, command: CommandMapping) -> CommandOutcome: ...
    def introspect_command(self, command: CommandMapping) -> CommandOutcome: ...
    def timed_introspection_result(self, service: str, path: str, timeout: float) -> tuple[CommandOutcome, object]: ...
    def read_introspection_xml(self, service: str, path: str, timeout: float) -> object: ...
    def drop_failed_introspection(self, service: str, path: str, error: BaseException) -> CommandOutcome: ...
    def record_introspection_xml(self, service: str, path: str, xml_data: object) -> None: ...
    def list_services(self) -> list[str]: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...


class DbusAdapterIntrospectionSnapshotContext(Protocol):  # pragma: no cover
    """Snapshot surface for advisory introspection findings."""

    cache: DbusCacheStore
    dbus_introspection_enabled: bool
    dbus_introspection_snapshot_path: str
    _introspection_queue_depth: int
    _last_introspection_full_scan_at: float

    def write_introspection_snapshot(self) -> None: ...
    def introspection_services_snapshot(self, now: float) -> dict[str, CommandPayload]: ...
    def introspection_cache_entries(self) -> list[tuple[str, CommandPayload]]: ...
    def split_introspection_cache_key(self, key: str) -> tuple[str, str]: ...
    def add_introspection_service_entry(
        self,
        services: dict[str, CommandPayload],
        service: str,
        path: str,
        entry: CommandMapping,
        now: float,
    ) -> None: ...
    def introspection_finding(self, entry: CommandMapping, now: float) -> CommandPayload: ...
