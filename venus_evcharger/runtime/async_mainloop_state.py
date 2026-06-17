# SPDX-License-Identifier: GPL-3.0-or-later
"""Async runtime state and DBus-thread guards."""

from __future__ import annotations

from collections import OrderedDict
import logging
import threading
import time
from typing import Any


class _RuntimeSupportAsyncMainloopStateMixin:
    @staticmethod
    def _float_attr(value: Any, default: float = 0.0) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(default)

    def initialize_async_runtime_state(self: Any) -> None:
        """Initialize RAM-only queues, worker flags, and timing diagnostics."""
        svc = self.service
        now = time.time()
        svc._dbus_mainloop_thread_id = None
        svc._dbus_async_publish_enabled = False
        svc._dbus_publish_queue_lock = threading.Lock()
        svc._dbus_publish_pending = OrderedDict()
        svc._dbus_publish_bump_pending = 0
        svc._dbus_publish_oldest_queued_at = None
        svc._dbus_publish_dropped_count = 0
        svc._dbus_publish_max_paths = 256
        svc._dbus_publish_budget_seconds = 0.1
        svc._dbus_publish_flush_interval_ms = 200
        svc._last_publish_flush_duration_seconds = 0.0
        svc._last_dbus_publish_queue_lag_seconds = 0.0
        svc._companion_publish_lock = threading.Lock()
        svc._companion_publish_pending = False
        svc._companion_publish_requested_at = None
        svc._companion_publish_now = None

        svc._update_worker_enabled = False
        svc._runtime_executor_event = threading.Event()
        svc._runtime_executor_stop_event = threading.Event()
        svc._runtime_executor_thread = None
        svc._update_worker_event = threading.Event()
        svc._update_worker_stop_event = threading.Event()
        svc._update_worker_lock = threading.Lock()
        svc._update_worker_thread = None
        svc._update_worker_running = False
        svc._update_worker_pending = False
        svc._update_worker_skipped_count = 0
        svc._last_update_cycle_duration_seconds = 0.0
        svc._last_update_cycle_started_at = None
        svc._last_update_cycle_finished_at = None
        svc._update_worker_budget_seconds = max(5.0, self._float_attr(getattr(svc, "poll_interval_ms", 1000), 1000) / 250.0)

        svc._control_command_async_enabled = False
        svc._control_command_event = threading.Event()
        svc._control_command_stop_event = threading.Event()
        svc._control_command_lock = threading.Lock()
        svc._control_command_thread = None
        svc._control_command_pending = OrderedDict()
        svc._control_command_sequence = 0
        svc._control_command_max_paths = 32
        svc._last_write_command_duration_seconds = 0.0
        svc._last_write_command_queue_lag_seconds = 0.0
        svc._write_command_budget_seconds = 2.0
        svc._desired_control_values = {}

        svc._mainloop_heartbeat_at = now
        svc._mainloop_watchdog_stop_event = threading.Event()
        svc._mainloop_watchdog_thread = None
        svc._mainloop_watchdog_interval_seconds = 1.0
        svc._mainloop_watchdog_stale_seconds = max(
            30.0,
            self._float_attr(getattr(svc, "auto_watchdog_stale_seconds", 180.0), 180.0),
        )
        svc._mainloop_watchdog_log_path = "/run/dbus-venus-evcharger-mainloop-hang.log"

    def mark_mainloop_thread(self: Any) -> None:
        """Remember which thread owns VeDbusService writes."""
        svc = self.service
        svc._dbus_mainloop_thread_id = threading.get_ident()
        svc._dbus_async_publish_enabled = True

    def dbus_publish_direct_allowed(self: Any) -> bool:
        """Return whether the caller may touch ``VeDbusService`` directly."""
        svc = self.service
        if not bool(getattr(svc, "_dbus_async_publish_enabled", False)):
            return True
        mainloop_thread_id = getattr(svc, "_dbus_mainloop_thread_id", None)
        return mainloop_thread_id is None or threading.get_ident() == int(mainloop_thread_id)

    def assert_dbus_mainloop_thread(self: Any, operation: str = "dbus access") -> None:
        """Raise when code tries to touch a DBus service outside the GLib thread."""
        if self.dbus_publish_direct_allowed():
            return
        message = f"{operation} attempted outside GLib/DBus mainloop thread"
        logging.error(message)
        raise RuntimeError(message)
