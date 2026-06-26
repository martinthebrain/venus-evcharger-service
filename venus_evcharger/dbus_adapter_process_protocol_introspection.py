#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Introspection contracts for DBus adapter process mixins."""

from __future__ import annotations

import configparser
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from venus_evcharger.dbus_adapter_components import (
    CommandOutcome,
    DbusCircuitBreaker,
    DbusConnectionManager,
    DbusDiscoveryManager,
)
from venus_evcharger.dbus_adapter_read import DbusReadExecutor
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox


class DbusAdapterIntrospectionContext(Protocol):  # pragma: no cover
    """Introspection request and background-discovery surface."""

    config: configparser.ConfigParser
    connection: DbusConnectionManager
    circuit: DbusCircuitBreaker
    cache: DbusCacheStore
    commands: DbusCommandInbox
    discovery: DbusDiscoveryManager
    read_executor: DbusReadExecutor
    dbus_introspection_enabled: bool
    dbus_introspection_request_path: str
    _introspection_queue_depth: int
    _last_introspection_full_scan_at: float

    def _read_introspection_request_payload(self) -> dict[str, Any]: ...
    def _enqueue_introspection_requests(self, payload: Mapping[str, Any]) -> int: ...
    def _clear_introspection_request_payload(self) -> None: ...
    def _enqueue_introspection_command(
        self,
        service: str,
        path: str,
        *,
        priority: int,
        source: str,
        reason: str,
    ) -> None: ...
    def _background_introspection_due(self, now: float) -> bool: ...
    def _background_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def _grid_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def _battery_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def _pv_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def _configured_or_prefixed_services(
        self,
        explicit_key: str,
        prefix_key: str,
        default_prefix: str,
    ) -> list[str]: ...
    def _refresh_services_command(self, command: Mapping[str, Any]) -> CommandOutcome: ...
    def _introspect_command_if_healthy(self, command: Mapping[str, Any]) -> CommandOutcome: ...
    def _introspect_command(self, command: Mapping[str, Any]) -> CommandOutcome: ...
    def _timed_introspection_result(self, service: str, path: str, timeout: float) -> tuple[CommandOutcome, Any]: ...
    def _read_introspection_xml(self, service: str, path: str, timeout: float) -> Any: ...
    def _drop_failed_introspection(self, service: str, path: str, error: BaseException) -> CommandOutcome: ...
    def _record_introspection_xml(self, service: str, path: str, xml_data: Any) -> None: ...
    def _list_services(self) -> list[str]: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], Any]) -> Any: ...


class DbusAdapterIntrospectionSnapshotContext(Protocol):  # pragma: no cover
    """Snapshot surface for advisory introspection findings."""

    cache: DbusCacheStore
    dbus_introspection_enabled: bool
    dbus_introspection_snapshot_path: str
    _introspection_queue_depth: int
    _last_introspection_full_scan_at: float

    def _introspection_services_snapshot(self, now: float) -> dict[str, Any]: ...
    def _introspection_cache_entries(self) -> list[tuple[str, dict[str, Any]]]: ...
    def _split_introspection_cache_key(self, key: str) -> tuple[str, str]: ...
    def _add_introspection_service_entry(
        self,
        services: dict[str, Any],
        service: str,
        path: str,
        entry: Mapping[str, Any],
        now: float,
    ) -> None: ...
    def _introspection_finding(self, entry: Mapping[str, Any], now: float) -> dict[str, Any]: ...
