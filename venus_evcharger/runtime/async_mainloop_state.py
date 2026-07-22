# SPDX-License-Identifier: GPL-3.0-or-later
"""Async worker, control-command, and watchdog state."""

from __future__ import annotations

from collections import OrderedDict
import threading
import time
from typing import Any


class AsyncRuntimeState:
    """Own RAM-only worker, command, and watchdog coordination state."""

    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def _float_attr(value: Any, default: float = 0.0) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(default)

    def initialize(self) -> None:
        """Initialize RAM-only queues, worker flags, and timing diagnostics."""
        svc = self.service
        now = time.time()
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
        svc._update_worker_budget_seconds = max(5.0, self._float_attr(getattr(svc, "poll_interval_ms", None)) / 250.0)

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
            self._float_attr(getattr(svc, "auto_watchdog_stale_seconds", None), 180.0),
        )
        svc._mainloop_watchdog_log_path = "/run/dbus-venus-evcharger-mainloop-hang.log"

__all__ = ["AsyncRuntimeState"]
