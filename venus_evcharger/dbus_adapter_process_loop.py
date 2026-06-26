#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import os
import time

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from venus_evcharger.dbus_adapter_process_protocol_loop import DbusAdapterLoopContext


class DbusAdapterLoopMixin:
    def run(self: DbusAdapterLoopContext) -> None:  # pragma: no cover - Venus DBus/GLib process loop
        DBusGMainLoop(set_as_default=True)
        self._install_signal_handlers()
        os.makedirs(self.paths.run_dir, exist_ok=True)
        os.makedirs(self.paths.command_dir, exist_ok=True)
        os.makedirs(self.paths.core_command_dir, exist_ok=True)
        self.start_socket()
        self.ensure_dbus_service()
        self._main_loop = GLib.MainLoop()
        GLib.timeout_add(max(50, int(self.min_tick_seconds * 1000)), self._tick)
        try:
            self._main_loop.run()
        finally:
            self._stop = True
            self.close_socket()

    def _tick(self: DbusAdapterLoopContext) -> bool:
        tick_started = time.monotonic()
        if self._stop:
            self.close_socket()
            return False
        if tick_started < self._next_work_tick_monotonic:
            return True
        self._last_tick_at = time.time()
        self._last_tick_monotonic = tick_started
        try:
            self.process_socket_once()
            self.process_introspection_requests_once()
            self.process_one_dbus_operation_once()
            self.publish_cache()
        except Exception as error:  # pylint: disable=broad-except
            self.circuit.record_error(error)
            logging.exception("DBus adapter tick failed: %s", error)
        finally:
            self._last_tick_duration_ms = (time.monotonic() - tick_started) * 1000.0
            self.tick_health.record(
                duration_ms=self._last_tick_duration_ms,
                expected_interval_s=self.tick_seconds,
                now=tick_started,
            )
            self.update_adaptive_tick()
            self._next_work_tick_monotonic = time.monotonic() + self.tick_seconds
        return not self._stop

    def update_adaptive_tick(self: DbusAdapterLoopContext) -> None:
        resources = self.resource_monitor.snapshot()
        self._last_resource_snapshot = resources
        self.apply_slo_regulation()
        resource_state = str(resources.get("state", "ok"))
        if float(self.tick_health.snapshot().get("max_tick_duration_ms_60s", 0.0) or 0.0) > self.slo_mainloop_gap_max_ms:
            resource_state = "busy"
        self.tick_seconds = self.adaptive_tick_seconds(
            circuit_state=self.circuit.state(),
            resource_state=resource_state,
        )

    def adaptive_tick_seconds(
        self: DbusAdapterLoopContext,
        *,
        circuit_state: str,
        resource_state: str,
    ) -> float:
        if circuit_state == "protective" or resource_state == "constrained":
            return self.max_tick_seconds
        if circuit_state == "degraded":
            return min(self.max_tick_seconds, max(self.min_tick_seconds * 2.5, 0.5))
        if resource_state == "busy":
            return min(self.max_tick_seconds, max(self.min_tick_seconds * 1.5, 0.3))
        return self.min_tick_seconds

    def process_one_dbus_operation_once(self: DbusAdapterLoopContext) -> bool:
        if self._refresh_initial_services_once():
            return True
        self.enqueue_background_introspection_if_due()
        if self._priority_read_performed():
            return True
        return self._process_standard_operation_once()

    def _refresh_initial_services_once(self: DbusAdapterLoopContext) -> bool:
        return not self.cache.services and self.refresh_services_if_due_once()

    def _priority_read_performed(self: DbusAdapterLoopContext) -> bool:
        return self._reads_need_priority() and self.poll_one_due_read_once()

    def _process_standard_operation_once(self: DbusAdapterLoopContext) -> bool:
        local_publish_count = self.write_scheduler.process_local_publish_burst()
        if self._process_preferred_read_or_write():
            return True
        return self.refresh_services_if_due_once() or local_publish_count > 0

    def _reads_need_priority(self: DbusAdapterLoopContext) -> bool:
        return self.read_executor.has_pending_aggregate() or self._core_reads_stale()

    def _core_reads_stale(self: DbusAdapterLoopContext) -> bool:
        now = time.time()
        for key in ("grid_power_w", "pv_power_w", "battery_soc"):
            if self._core_read_age(key, now) > self.slo_core_read_max_age_seconds:
                return True
        return False

    def _core_read_age(self: DbusAdapterLoopContext, key: str, now: float) -> float:
        entry = self.cache.values.get(key)
        if not entry:
            return self.slo_core_read_max_age_seconds + 1.0
        updated_at = float(entry.get("updated_at", 0.0) or 0.0)
        return now - updated_at if updated_at > 0.0 else self.slo_core_read_max_age_seconds + 1.0

    def _process_preferred_read_or_write(self: DbusAdapterLoopContext) -> bool:
        if self._prefer_read_next:
            return self._try_read_then_write()
        return self._try_write_then_read()

    def _try_read_then_write(self: DbusAdapterLoopContext) -> bool:
        if self.poll_one_due_read_once():
            self._prefer_read_next = self._reads_need_priority()
            return True
        return self._try_scheduled_write(prefer_read_next=True)

    def _try_write_then_read(self: DbusAdapterLoopContext) -> bool:
        if self._try_scheduled_write(prefer_read_next=True):
            return True
        if self.poll_one_due_read_once():
            self._prefer_read_next = False
            return True
        return False

    def _try_scheduled_write(self: DbusAdapterLoopContext, *, prefer_read_next: bool) -> bool:
        if not self.write_scheduler.process_one(include_local_publish=False):
            return False
        self._prefer_read_next = prefer_read_next
        return True
