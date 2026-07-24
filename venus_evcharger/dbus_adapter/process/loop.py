#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""GLib main-loop orchestration for the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import os
import time

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from venus_evcharger.dbus_adapter.process.health import GatewayControlSnapshot
from venus_evcharger.dbus_adapter.process.protocols.loop import DbusAdapterLoopContext
from venus_evcharger.dbus_gateway_core import float_or_zero

GATEWAY_TICK_RECOVERY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)
EVCS_REGISTRATION_COMMAND_KIND = "register_evcs"


class DbusAdapterLoop:
    def __init__(self, context: DbusAdapterLoopContext) -> None:
        self._context = context

    def run(self) -> None:  # pragma: no cover - Venus DBus/GLib process loop
        context = self._context
        DBusGMainLoop(set_as_default=True)
        context.runtime_role.install_signal_handlers()
        os.makedirs(context.paths.run_dir, exist_ok=True)
        os.makedirs(context.paths.command_dir, exist_ok=True)
        os.makedirs(context.paths.core_command_dir, exist_ok=True)
        context.socket_role.start_socket()
        main_loop = GLib.MainLoop()
        context._main_loop = main_loop
        GLib.timeout_add(max(50, int(context.min_tick_seconds * 1000)), self.tick)
        try:
            main_loop.run()
        finally:
            context._stop = True
            context.socket_role.close_socket()

    def tick(self) -> bool:
        context = self._context
        tick_started = time.monotonic()
        pending_tick_active = False
        if context._stop:
            context.socket_role.close_socket()
            return False
        if tick_started < context._next_work_tick_monotonic:
            return True
        expected_tick_interval_seconds = context.tick_seconds
        context._last_tick_at = time.time()
        context._last_tick_monotonic = tick_started
        try:
            context.socket_role.process_socket_once()
            context.write_scheduler.begin_tick()
            pending_tick_active = True
            self.process_one_dbus_operation_once()
            control = context.health_role.control_snapshot()
            self.update_adaptive_tick(control)
            context.io_role.publish_cache(control)
        except GATEWAY_TICK_RECOVERY_ERRORS as error:
            context.circuit.record_error(error)
            logging.exception("DBus adapter tick failed: %s", error)
        finally:
            if pending_tick_active:
                context.write_scheduler.end_tick()
            context._last_tick_duration_ms = (time.monotonic() - tick_started) * 1000.0
            context.tick_health.record(
                duration_ms=context._last_tick_duration_ms,
                expected_interval_s=expected_tick_interval_seconds,
                now=tick_started,
            )
            context._next_work_tick_monotonic = time.monotonic() + context.tick_seconds
        return not context._stop

    def update_adaptive_tick(
        self,
        snapshot: GatewayControlSnapshot | None = None,
    ) -> None:
        context = self._context
        control = context.health_role.apply_slo_regulation(snapshot)
        resource_state = control.resource_state
        if (
            resource_state == "ok"
            and control.eventloop_max_duration_ms > context.slo_mainloop_gap_max_ms
        ):
            resource_state = "busy"
        context.tick_seconds = self.adaptive_tick_seconds(
            circuit_state=context.circuit.state(),
            resource_state=resource_state,
        )

    def adaptive_tick_seconds(
        self,
        *,
        circuit_state: str,
        resource_state: str,
    ) -> float:
        context = self._context
        if circuit_state == "protective" or resource_state == "constrained":
            return float(context.max_tick_seconds)
        if circuit_state == "degraded":
            return min(
                float(context.max_tick_seconds),
                max(float(context.min_tick_seconds) * 2.5, 0.5),
            )
        if resource_state == "busy":
            return min(
                float(context.max_tick_seconds),
                max(float(context.min_tick_seconds) * 1.5, 0.3),
            )
        return float(context.min_tick_seconds)

    def process_one_dbus_operation_once(self) -> bool:
        if not self._context.publication_role.evcs_service_registered:
            return self.process_evcs_registration_once()
        if self.refresh_initial_services_once():
            return True
        self._context.introspection_role.enqueue_background_introspection_if_due()
        local_publish_count = self._context.write_scheduler.process_local_publish_burst()
        if self._context.write_scheduler.process_urgent_once():
            return True
        if self.priority_read_performed():
            return True
        return self.process_standard_operation_once(local_publish_count)

    def process_evcs_registration_once(self) -> bool:
        """Register our own service before touching any external DBus service."""
        return bool(
            self._context.write_scheduler.process_one(
                include_local_publish=False,
                required_kind=EVCS_REGISTRATION_COMMAND_KIND,
            )
        )

    def refresh_initial_services_once(self) -> bool:
        return not self._context.cache.services and self._context.io_role.refresh_services_if_due_once()

    def priority_read_performed(self) -> bool:
        return self.reads_need_priority() and self._context.io_role.poll_one_due_read_once()

    def process_standard_operation_once(self, local_publish_count: int = 0) -> bool:
        if self.process_preferred_read_or_write():
            return True
        return bool(
            self._context.io_role.refresh_services_if_due_once()
            or local_publish_count > 0
        )

    def reads_need_priority(self) -> bool:
        return self._context.read_executor.has_pending_aggregate() or self.core_reads_stale()

    def core_reads_stale(self) -> bool:
        now = time.time()
        for key in ("grid_power_w", "pv_power_w", "battery_soc"):
            if self.core_read_age(key, now) > self._context.slo_core_read_max_age_seconds:
                return True
        return False

    def core_read_age(self, key: str, now: float) -> float:
        context = self._context
        entry = context.cache.values.get(key)
        if not entry:
            return float(context.slo_core_read_max_age_seconds) + 1.0
        updated_at = float_or_zero(entry.get("updated_at"))
        return (
            now - float(updated_at)
            if updated_at > 0.0
            else float(context.slo_core_read_max_age_seconds) + 1.0
        )

    def process_preferred_read_or_write(self) -> bool:
        if self._context._prefer_read_next:
            return self.try_read_then_write()
        return self.try_write_then_read()

    def try_read_then_write(self) -> bool:
        if self._context.io_role.poll_one_due_read_once():
            self._context._prefer_read_next = self.reads_need_priority()
            return True
        return self.try_scheduled_write(prefer_read_next=True)

    def try_write_then_read(self) -> bool:
        if self.try_scheduled_write(prefer_read_next=True):
            return True
        if self._context.io_role.poll_one_due_read_once():
            self._context._prefer_read_next = False
            return True
        return False

    def try_scheduled_write(self, *, prefer_read_next: bool) -> bool:
        context = self._context
        if not context.write_scheduler.process_one(include_local_publish=False):
            return False
        context._prefer_read_next = (
            False if context.write_scheduler.last_scheduled_outcome == "deferred" else prefer_read_next
        )
        return True
