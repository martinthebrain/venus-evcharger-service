# SPDX-License-Identifier: GPL-3.0-or-later
"""Async worker, control-command, and watchdog state."""

from __future__ import annotations

from collections import OrderedDict
import os
import threading
import time
from typing import Any


PROCESS_HEARTBEAT_INTERVAL_SECONDS = 5.0


def _process_heartbeat_path(service: Any) -> str:
    """Derive a RAM-only heartbeat path from the event-state filename."""
    runtime_path = str(getattr(service, "runtime_state_path", "")).strip()
    filename = os.path.basename(runtime_path)
    if filename in {"", ".", ".."}:
        filename = f"dbus-venus-evcharger-{getattr(service, 'deviceinstance', 0)}.json"
    stem = filename[:-5] if filename.endswith(".json") else filename
    return f"/run/{stem}.heartbeat.json"


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
        epoch_now = time.time()
        monotonic_now = time.monotonic()
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

        svc._mainloop_heartbeat_at = epoch_now
        svc._mainloop_heartbeat_monotonic = monotonic_now
        svc._mainloop_watchdog_stop_event = threading.Event()
        svc._mainloop_watchdog_thread = None
        svc._mainloop_watchdog_interval_seconds = 1.0
        svc._mainloop_watchdog_stale_seconds = max(
            30.0,
            self._float_attr(getattr(svc, "auto_watchdog_stale_seconds", None), 180.0),
        )
        svc._mainloop_watchdog_log_path = "/run/dbus-venus-evcharger-mainloop-hang.log"
        svc._process_heartbeat_path = _process_heartbeat_path(svc)
        svc._process_heartbeat_interval_seconds = PROCESS_HEARTBEAT_INTERVAL_SECONDS
        svc._process_heartbeat_last_write_monotonic = None
        process_started_at = self._float_attr(getattr(svc, "started_at", None), epoch_now)
        svc._process_started_at = process_started_at if process_started_at > 0.0 else epoch_now

__all__ = ["AsyncRuntimeState"]
