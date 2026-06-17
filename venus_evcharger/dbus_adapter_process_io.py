#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

import dbus

from venus_evcharger.dbus_adapter_components import DbusOperationDeferred
from venus_evcharger.dbus_adapter_process_protocols import DbusAdapterIoContext


class DbusAdapterIoMixin:
    def _poll_one_due_read_once(self: DbusAdapterIoContext) -> bool:
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

    def _maybe_refresh_services(self: DbusAdapterIoContext) -> None:
        self._refresh_services_if_due_once()

    def _refresh_services_if_due_once(self: DbusAdapterIoContext) -> bool:
        now = time.time()
        if not self.discovery.due(now=now, priority_allowed=self.circuit.allows_priority):
            return False
        try:
            self.cache.update_services(self._list_services())
            self.discovery.record_success(now=now)
            return True
        except DbusOperationDeferred:
            return False
        except Exception as error:  # pylint: disable=broad-except
            self.discovery.record_error(error, now=now)
            return True

    def _list_services(self: DbusAdapterIoContext) -> list[str]:
        def _read() -> list[str]:
            obj = self.connection.bus().get_object("org.freedesktop.DBus", "/org/freedesktop/DBus", introspect=False)
            iface = dbus.Interface(obj, "org.freedesktop.DBus")
            return [str(name) for name in iface.ListNames()]

        return cast(list[str], self._timed("read", _read))

    def _timed(self: DbusAdapterIoContext, kind: str, operation: Callable[[], Any]) -> Any:
        self.rate_limiter.require_due(kind)
        started = time.monotonic()
        try:
            result = operation()
            self.circuit.record_success((time.monotonic() - started) * 1000.0, kind=kind)
            return result
        except Exception as error:
            self.circuit.record_error(error, kind=kind)
            raise

    def _timed_local_publish(self: DbusAdapterIoContext, operation: Callable[[], Any]) -> Any:
        started = time.monotonic()
        try:
            result = operation()
            self.circuit.record_success((time.monotonic() - started) * 1000.0, kind="local_publish")
            return result
        except Exception as error:
            self.circuit.record_error(error, kind="local_publish")
            raise

    def _publish_cache(self: DbusAdapterIoContext) -> None:
        health = self._health_snapshot()
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
        self._append_health_log(health)
        self._write_introspection_snapshot()
