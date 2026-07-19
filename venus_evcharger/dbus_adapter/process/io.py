#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exclusive low-level DBus IO for the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import TypeVar

import dbus

from venus_evcharger.dbus_adapter.process.introspection_snapshot import DbusAdapterIntrospectionSnapshot
from venus_evcharger.dbus_adapter.process.protocols.io import DbusAdapterIoContext
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred

_T = TypeVar("_T")


class DbusAdapterIo(DbusAdapterIntrospectionSnapshot):
    def poll_one_due_read_once(self: DbusAdapterIoContext) -> bool:
        now = time.time()
        due = self.read_scheduler.next_due(
            now=now,
            circuit_state=self.circuit.state(),
            priority_allowed=self.circuit.allows_priority,
        )
        if due is None:
            return False
        key, spec, interval = due
        outcome = self.read_executor.poll_read_spec(key, spec)
        if outcome == "applied":
            self.read_scheduler.record_success(key, now=now, interval=interval)
        elif outcome == "dropped":
            self.read_scheduler.record_error(key, now=now, interval=interval)
        return outcome != "deferred" or bool(self.read_executor.last_operation_performed)

    def maybe_refresh_services(self: DbusAdapterIoContext) -> None:
        self.refresh_services_if_due_once()

    def refresh_services_if_due_once(self: DbusAdapterIoContext) -> bool:
        now = time.time()
        if not self.discovery.due(now=now, priority_allowed=self.circuit.allows_priority):
            return False
        try:
            self.cache.update_services(self.list_services())
            self.commands.remove_coalesced("refresh:services")
            self.discovery.record_success(now=now)
            return True
        except DbusOperationDeferred:
            return False
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            self.commands.remove_coalesced("refresh:services")
            self.discovery.record_error(error, now=now)
            return True

    def list_services(self: DbusAdapterIoContext) -> list[str]:
        def _read() -> object:
            obj = self.connection.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus", introspect=False)
            iface = dbus.Interface(obj, "org.freedesktop.DBus")
            return iface.ListNames()

        return _service_names(self.timed_dbus_operation("read", _read))

    def timed_dbus_operation(self: DbusAdapterIoContext, kind: str, operation: Callable[[], _T]) -> _T:
        self.rate_limiter.require_due(kind)
        started = time.monotonic()
        try:
            result = operation()
            self.circuit.record_success((time.monotonic() - started) * 1000.0, kind=kind)
            return result
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            self.circuit.record_error(error, kind=kind)
            raise

    def timed_local_publish(self: DbusAdapterIoContext, operation: Callable[[], _T]) -> _T:
        started = time.monotonic()
        try:
            result = operation()
            self.circuit.record_success((time.monotonic() - started) * 1000.0, kind="local_publish")
            return result
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            self.circuit.record_error(error, kind="local_publish")
            raise

    def publish_cache(self: DbusAdapterIoContext) -> None:
        health = self.health_snapshot()
        self.cache.health.update(health)
        if self.cache_publish_interval_seconds > 0.0:
            now = time.monotonic()
            if (
                self.cache.sequence == self._last_cache_publish_sequence
                and now - self._last_cache_publish_monotonic < self.cache_publish_interval_seconds
            ):
                return
            self._last_cache_publish_monotonic = now
            self._last_cache_publish_sequence = self.cache.sequence
        self.cache.write_snapshot_files()
        self.append_health_log(health)
        self.write_introspection_snapshot()


def _service_names(value: object) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise TypeError("DBus ListNames returned a non-iterable service list")
    return [str(name) for name in value]
