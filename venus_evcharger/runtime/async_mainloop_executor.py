# SPDX-License-Identifier: GPL-3.0-or-later
"""Serialized runtime executor helpers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from venus_evcharger.control import ControlCommand
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.core_commands import CoreControlCommand, parse_core_control_command
from venus_evcharger.runtime.contracts import ControlCommandQueuePort

ASYNC_UPDATE_CYCLE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class RuntimeExecutor:
    """Serialize gateway commands, control commands, and update cycles."""

    def __init__(self, service: Any, control_commands: ControlCommandQueuePort) -> None:
        self.service = service
        self.control_commands = control_commands

    def start_update_worker(self) -> None:
        """Enable periodic update cycles in the serialized runtime executor."""
        svc = self.service
        svc._update_worker_enabled = True
        self.start()

    def start(self) -> None:
        """Start the single owner for mutable runtime state."""
        svc = self.service
        if getattr(svc, "_runtime_executor_thread", None) is not None:
            return
        thread = threading.Thread(target=self._executor_loop, name="evcharger-runtime-executor", daemon=True)
        svc._runtime_executor_thread = thread
        svc._update_worker_thread = thread
        svc._control_command_thread = thread
        thread.start()

    def start_control_command_worker(self) -> None:
        """Enable command execution in this serialized executor."""
        self.service._control_command_async_enabled = True
        self.start()

    def schedule_update_cycle(self) -> bool:
        """Request one update cycle without running it in the caller thread."""
        svc = self.service
        if not _update_worker_enabled(svc):
            return bool(svc.update.update())
        with svc._update_worker_lock:
            if svc._update_worker_pending or svc._update_worker_running:
                svc._update_worker_skipped_count += 1
            svc._update_worker_pending = True
            svc._update_worker_event.set()
            svc._runtime_executor_event.set()
        return True

    def stop_requested(self) -> bool:
        svc = self.service
        return bool(
            svc._runtime_executor_stop_event.is_set()
            or svc._update_worker_stop_event.is_set()
            or svc._control_command_stop_event.is_set()
        )

    def _executor_loop(self) -> None:
        svc = self.service
        while not self.stop_requested():
            self.wait_for_work(svc)
            if self.should_continue():
                self.drain_available_work()

    def should_continue(self) -> bool:
        """Return whether the runtime executor should keep processing."""
        return not self.stop_requested()

    @staticmethod
    def wait_for_work(svc: Any) -> None:
        """Wait for and clear runtime executor wake events."""
        svc._runtime_executor_event.wait(0.5)
        svc._runtime_executor_event.clear()
        svc._update_worker_event.clear()
        svc._control_command_event.clear()

    def drain_available_work(self) -> None:
        """Drain commands and update cycles until no more work is pending."""
        while self.should_continue() and self.run_once():
            pass

    def run_once(self) -> bool:
        """Run one serialized runtime executor pass."""
        core_command_work = self.drain_core_commands_once()
        did_work = self.control_commands.drain_once()
        return bool(self.run_pending_update_once() or did_work or core_command_work)

    def drain_core_commands_once(self) -> bool:
        """Handle external control commands delivered through the IPC boundary."""
        svc = self.service
        inbox = getattr(svc, "_core_command_mailbox", None)
        if inbox is None:
            return False
        pending = inbox.load_pending()
        if not pending:
            return False
        for path, payload in inbox.coalesce(pending):
            try:
                self.handle_core_command(payload, command_file=path)
            finally:
                inbox.remove(path)
        return True

    def handle_core_command(self, payload: CommandMapping, *, command_file: str = "") -> None:
        """Validate and dispatch one command from the core IPC mailbox."""
        command = parse_core_control_command(payload)
        if command is None:
            logging.warning("Dropping invalid core control command file=%s", command_file or "unknown")
            return
        _log_core_control_command(command, command_file=command_file, now=time.time())
        self.dispatch_core_control_command(command)

    def dispatch_core_control_command(self, command: CoreControlCommand) -> None:
        """Dispatch a validated semantic IPC command to the control core."""
        svc = self.service
        control_command = ControlCommand(
            name=command.name,
            target=command.target,
            value=command.value,
            source=command.source,
            command_id=command.command_id,
        )
        svc.auto.handle_command(control_command)

    def run_pending_update_once(self) -> bool:
        svc = self.service
        with svc._update_worker_lock:
            if not svc._update_worker_pending:
                return False
            svc._update_worker_pending = False
            svc._update_worker_running = True
        started_at = time.time()
        started = time.monotonic()
        svc._last_update_cycle_started_at = started_at
        try:
            svc.update.update()
        except ASYNC_UPDATE_CYCLE_ERRORS:
            logging.exception("Async update worker cycle failed")
        finally:
            duration = time.monotonic() - started
            svc._last_update_cycle_duration_seconds = duration
            svc._last_update_cycle_finished_at = time.time()
            with svc._update_worker_lock:
                svc._update_worker_running = False
            if duration > _update_worker_budget_seconds(svc):
                logging.warning("Update worker cycle exceeded budget: %.3fs", duration)
        return True


__all__ = ["RuntimeExecutor"]


def _update_worker_enabled(svc: Any) -> bool:
    if not hasattr(svc, "_update_worker_enabled"):
        return False
    return bool(svc._update_worker_enabled)


def _update_worker_budget_seconds(svc: Any) -> float:
    raw = svc._update_worker_budget_seconds if hasattr(svc, "_update_worker_budget_seconds") else 5.0
    budget = finite_float_or_none(raw)
    return budget if budget is not None else 5.0


def _log_core_control_command(command: CoreControlCommand, *, command_file: str, now: float) -> None:
    logging.info(
        "Core control command source=%s origin=%s id=%s name=%s target=%s value=%r age_s=%s file=%s",
        command.source,
        command.origin,
        command.command_id,
        command.name,
        command.target,
        command.value,
        _core_command_age_label(command, now),
        command_file or "unknown",
    )


def _core_command_age_label(command: CoreControlCommand, now: float) -> str:
    if command.created_at <= 0.0:
        return "unknown"
    return f"{max(0.0, float(now) - command.created_at):.3f}"
