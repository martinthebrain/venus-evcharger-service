#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Introspection contracts for DBus adapter process roles."""

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
from venus_evcharger.dbus_gateway_command_types import CommandMapping, CommandPayload


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

    def introspection_request_payload(self) -> CommandPayload: ...
    def enqueue_introspection_requests(self, payload: CommandMapping) -> int: ...
    def clear_introspection_request_payload(self) -> None: ...
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
    def background_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def grid_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def battery_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def pv_introspection_specs(self) -> list[tuple[str, str, int, str, str]]: ...
    def configured_or_prefixed_services(
        self,
        explicit_key: str,
        prefix_key: str,
        default_prefix: str,
    ) -> list[str]: ...
    def refresh_services_command(self, command: CommandMapping) -> CommandOutcome: ...
    def introspect_command_if_healthy(self, command: CommandMapping) -> CommandOutcome: ...
    def introspect_command(self, command: CommandMapping) -> CommandOutcome: ...
    def timed_introspection_result(self, service: str, path: str, timeout: float) -> tuple[CommandOutcome, object]: ...
    def read_introspection_xml(self, service: str, path: str, timeout: float) -> object: ...
    def drop_failed_introspection(self, service: str, path: str, error: BaseException) -> CommandOutcome: ...
    def record_introspection_xml(self, service: str, path: str, xml_data: object) -> None: ...
    def list_services(self) -> list[str]: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], Any]) -> Any: ...


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
