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

    def _poll_one_due_read_once(self) -> bool: ...
    def _refresh_services_if_due_once(self) -> bool: ...
    def _list_services(self) -> list[str]: ...
    def _timed(self, kind: str, operation: Callable[[], Any]) -> Any: ...
    def _health_snapshot(self) -> dict[str, Any]: ...
    def _append_health_log(self, health: Mapping[str, Any]) -> None: ...
    def _write_introspection_snapshot(self) -> None: ...
