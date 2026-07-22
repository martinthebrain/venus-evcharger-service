# SPDX-License-Identifier: GPL-3.0-or-later
"""Async control command queue helpers."""

from __future__ import annotations

import logging
import time
from typing import Any

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.async_control_types import require_control_command_queue

ASYNC_CONTROL_COMMAND_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class ControlCommandQueue:
    """Coalesce and execute control commands for one runtime service."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def enqueue(self, command: ControlCommand) -> bool:
        """Coalesce semantic control commands for the background worker."""
        svc = self.service
        if not svc._control_command_async_enabled:
            result = svc.auto.handle_command(command)
            return bool(result.accepted)
        queued_at = time.time()
        with svc._control_command_lock:
            pending = require_control_command_queue(svc._control_command_pending, "_control_command_pending")
            svc._control_command_sequence += 1
            if command.target in pending:
                del pending[command.target]
            pending[command.target] = (int(svc._control_command_sequence), queued_at, command)
            svc._desired_control_values[command.target] = command.value
            while len(pending) > int(getattr(svc, "_control_command_max_paths", 32)):
                dropped_path = next(iter(pending))
                del pending[dropped_path]
                svc._desired_control_values.pop(dropped_path, None)
            svc._control_command_event.set()
            svc._runtime_executor_event.set()
        return True

    def drain_once(self) -> bool:
        svc = self.service
        with svc._control_command_lock:
            pending = require_control_command_queue(svc._control_command_pending, "_control_command_pending")
            commands = list(pending.values())
            pending.clear()
        if not commands:
            return False
        for _sequence, queued_at, command in commands:
            started = time.monotonic()
            svc._last_write_command_queue_lag_seconds = max(0.0, time.time() - queued_at)
            try:
                svc.auto.handle_command(command)
            except ASYNC_CONTROL_COMMAND_ERRORS:
                logging.exception("Async control command failed target=%s", command.target)
            finally:
                duration = time.monotonic() - started
                svc._last_write_command_duration_seconds = duration
                budget = _float_attr(getattr(svc, "_write_command_budget_seconds", None), 2.0)
                if duration > budget:
                    logging.warning(
                        "Control command target=%s exceeded budget: %.3fs",
                        command.target,
                        duration,
                    )
        return True


def _float_attr(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(default)


__all__ = ["ControlCommandQueue"]
