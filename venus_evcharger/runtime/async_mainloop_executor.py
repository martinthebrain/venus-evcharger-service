# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
"""Serialized runtime executor helpers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Mapping


class _RuntimeSupportAsyncMainloopExecutorMixin:
    def start_update_worker(self) -> None:
        """Enable periodic update cycles in the serialized runtime executor."""
        svc = self.service
        svc._update_worker_enabled = True
        self._start_runtime_executor()

    def _start_runtime_executor(self) -> None:
        """Start the single owner for mutable runtime state."""
        svc = self.service
        if getattr(svc, "_runtime_executor_thread", None) is not None:
            return
        thread = threading.Thread(target=self._runtime_executor_loop, name="evcharger-runtime-executor", daemon=True)
        svc._runtime_executor_thread = thread
        svc._update_worker_thread = thread
        svc._control_command_thread = thread
        thread.start()

    def schedule_update_cycle(self) -> bool:
        """Request one update cycle without running it in the caller thread."""
        svc = self.service
        if not bool(getattr(svc, "_update_worker_enabled", False)):
            return bool(svc._update())
        with svc._update_worker_lock:
            if svc._update_worker_pending or svc._update_worker_running:
                svc._update_worker_skipped_count += 1
            svc._update_worker_pending = True
            svc._update_worker_event.set()
            svc._runtime_executor_event.set()
        return True

    def _update_worker_loop(self) -> None:
        """Compatibility entry point for older tests; use the serialized executor."""
        self._runtime_executor_loop()

    def _runtime_executor_stop_requested(self) -> bool:
        svc = self.service
        return bool(
            svc._runtime_executor_stop_event.is_set()
            or svc._update_worker_stop_event.is_set()
            or svc._control_command_stop_event.is_set()
        )

    def _runtime_executor_loop(self) -> None:
        svc = self.service
        while not self._runtime_executor_stop_requested():
            self._runtime_executor_wait_for_work(svc)
            if not self._runtime_executor_should_continue():
                break
            self._runtime_executor_drain_available_work()

    def _runtime_executor_should_continue(self) -> bool:
        """Return whether the runtime executor should keep processing."""
        return not self._runtime_executor_stop_requested()

    def _runtime_executor_wait_for_work(self, svc: Any) -> None:
        """Wait for and clear runtime executor wake events."""
        svc._runtime_executor_event.wait(0.5)
        svc._runtime_executor_event.clear()
        svc._update_worker_event.clear()
        svc._control_command_event.clear()

    def _runtime_executor_drain_available_work(self) -> None:
        """Drain commands and update cycles until no more work is pending."""
        while self._runtime_executor_should_continue():
            if not self._runtime_executor_run_once():
                break

    def _runtime_executor_run_once(self) -> bool:
        """Run one serialized runtime executor pass."""
        gateway_work = self._drain_gateway_core_commands_once()
        did_work = self._drain_control_commands_once()
        return self._run_pending_update_cycle_once() or did_work or gateway_work

    def _drain_gateway_core_commands_once(self) -> bool:
        """Handle GUI/user commands delivered by the DBus gateway."""
        svc = self.service
        inbox = getattr(svc, "_gateway_core_commands", None)
        if inbox is None:
            return False
        pending = inbox.load_pending()
        if not pending:
            return False
        for path, payload in inbox.coalesce(pending):
            try:
                self._handle_gateway_core_command(payload)
            finally:
                inbox.remove(path)
        return True

    def _handle_gateway_core_command(self, payload: Mapping[str, Any]) -> None:
        """Translate one gateway command file into an existing control command."""
        if not self._is_gateway_user_command(payload):
            return
        if self._apply_gateway_write_if_supported(payload):
            return
        self._dispatch_gateway_control_command(payload)

    def _is_gateway_user_command(self, payload: Mapping[str, Any]) -> bool:
        """Return whether a gateway command is intended for the core command API."""
        kind = str(payload.get("kind") or payload.get("type") or "")
        return kind == "user_command"

    def _apply_gateway_write_if_supported(self, payload: Mapping[str, Any]) -> bool:
        """Let the DBus proxy consume gateway writes that map directly to local paths."""
        svc = self.service
        path = str(payload.get("path") or "")
        apply_gateway_write = getattr(getattr(svc, "_dbusservice", None), "apply_gateway_write", None)
        return bool(callable(apply_gateway_write) and apply_gateway_write(path, payload.get("value")))

    def _dispatch_gateway_control_command(self, payload: Mapping[str, Any]) -> None:
        """Dispatch a gateway command through the existing control command path."""
        svc = self.service
        path = str(payload.get("path") or "")
        command = svc._control_command_from_write(path, payload.get("value"), source="dbus")
        svc._handle_control_command(command)

    def _run_pending_update_cycle_once(self) -> bool:
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
            svc._update()
        except Exception:  # pylint: disable=broad-except
            logging.exception("Async update worker cycle failed")
        finally:
            duration = time.monotonic() - started
            svc._last_update_cycle_duration_seconds = duration
            svc._last_update_cycle_finished_at = time.time()
            with svc._update_worker_lock:
                svc._update_worker_running = False
            if duration > self._float_attr(getattr(svc, "_update_worker_budget_seconds", 5.0), 5.0):
                logging.warning("Update worker cycle exceeded budget: %.3fs", duration)
        return True
