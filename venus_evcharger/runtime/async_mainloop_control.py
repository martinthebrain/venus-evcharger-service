# SPDX-License-Identifier: GPL-3.0-or-later
"""Async control command queue helpers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.async_mainloop_types import require_control_command_queue

ASYNC_CONTROL_COMMAND_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _RuntimeSupportAsyncMainloopControlMixin:
    def enqueue_control_command(self: Any, command: ControlCommand) -> bool:
        """Coalesce DBus control commands for a background worker."""
        svc = self.service
        if not bool(getattr(svc, "_control_command_async_enabled", False)):
            result = svc._handle_control_command(command)
            return bool(result.accepted)
        queued_at = time.time()
        with svc._control_command_lock:
            pending = require_control_command_queue(svc._control_command_pending, "_control_command_pending")
            svc._control_command_sequence += 1
            if command.path in pending:
                del pending[command.path]
            pending[command.path] = (int(svc._control_command_sequence), queued_at, command)
            svc._desired_control_values[command.path] = command.value
            while len(pending) > int(getattr(svc, "_control_command_max_paths", 32)):
                dropped_path, _dropped = pending.popitem(last=False)
                svc._desired_control_values.pop(dropped_path, None)
            svc._control_command_event.set()
            svc._runtime_executor_event.set()
        return True

    def start_control_command_worker(self: Any) -> None:
        """Enable DBus command execution in the serialized runtime executor."""
        svc = self.service
        svc._control_command_async_enabled = True
        self._start_runtime_executor()

    def _control_command_worker_loop(self: Any) -> None:
        """Compatibility entry point for older tests; use the serialized executor."""
        self._runtime_executor_loop()

    def _drain_control_commands_once(self: Any) -> bool:
        svc = self.service
        with svc._control_command_lock:
            pending = require_control_command_queue(svc._control_command_pending, "_control_command_pending")
            commands = sorted(pending.values(), key=lambda item: item[0])
            pending.clear()
        if not commands:
            return False
        for _sequence, queued_at, command in commands:
            started = time.monotonic()
            svc._last_write_command_queue_lag_seconds = max(0.0, time.time() - queued_at)
            try:
                svc._handle_control_command(command)
            except ASYNC_CONTROL_COMMAND_ERRORS:
                logging.exception("Async control command failed path=%s", command.path)
            finally:
                duration = time.monotonic() - started
                svc._last_write_command_duration_seconds = duration
                if duration > self._float_attr(getattr(svc, "_write_command_budget_seconds", 2.0), 2.0):
                    logging.warning(
                        "Control command path=%s exceeded budget: %.3fs",
                        command.path,
                        duration,
                    )
        return True
