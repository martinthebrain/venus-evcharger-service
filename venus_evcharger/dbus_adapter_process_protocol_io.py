#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""IO contract for DBus adapter process mixins."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from venus_evcharger.dbus_adapter_components import (
    DbusCircuitBreaker,
    DbusConnectionManager,
    DbusDiscoveryManager,
    DbusRateLimiter,
    DbusReadScheduler,
)
from venus_evcharger.dbus_adapter_read import DbusReadExecutor
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox
from venus_evcharger.dbus_gateway_command_types import CommandPayload


class DbusAdapterIoContext(Protocol):  # pragma: no cover
    """DBus read, discovery, timing, and cache-publish surface."""

    connection: DbusConnectionManager
    rate_limiter: DbusRateLimiter
    circuit: DbusCircuitBreaker
    cache: DbusCacheStore
    commands: DbusCommandInbox
    discovery: DbusDiscoveryManager
    read_scheduler: DbusReadScheduler
    read_executor: DbusReadExecutor
    cache_publish_interval_seconds: float
    _last_cache_publish_monotonic: float
    _last_cache_publish_sequence: int

    def poll_one_due_read_once(self) -> bool: ...
    def refresh_services_if_due_once(self) -> bool: ...
    def list_services(self) -> list[str]: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], Any]) -> Any: ...
    def health_snapshot(self) -> CommandPayload: ...
    def append_health_log(self, health: Mapping[str, object]) -> None: ...
    def write_introspection_snapshot(self) -> None: ...
