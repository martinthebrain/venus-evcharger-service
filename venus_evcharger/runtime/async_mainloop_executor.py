# SPDX-License-Identifier: GPL-3.0-or-later
"""Serialized runtime executor helpers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Mapping

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.core.return_contracts import require_bool
from venus_evcharger.runtime.async_mainloop_state import _RuntimeAsyncMainloopState

ASYNC_UPDATE_CYCLE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _RuntimeAsyncMainloopExecutor(_RuntimeAsyncMainloopState):
    def start_update_worker(self: Any) -> None:
        """Enable periodic update cycles in the serialized runtime executor."""
        svc = self.service
        svc._update_worker_enabled = True
        self._start_runtime_executor()

    def _start_runtime_executor(self: Any) -> None:
        """Start the single owner for mutable runtime state."""
        svc = self.service
        if getattr(svc, "_runtime_executor_thread", None) is not None:
            return
        thread = threading.Thread(target=self._runtime_executor_loop, name="evcharger-runtime-executor", daemon=True)
        svc._runtime_executor_thread = thread
        svc._update_worker_thread = thread
        svc._control_command_thread = thread
        thread.start()

    def schedule_update_cycle(self: Any) -> bool:
        """Request one update cycle without running it in the caller thread."""
        svc = self.service
        if not _update_worker_enabled(svc):
            return bool(svc._update())
        with svc._update_worker_lock:
            if svc._update_worker_pending or svc._update_worker_running:
                svc._update_worker_skipped_count += 1
            svc._update_worker_pending = True
            svc._update_worker_event.set()
            svc._runtime_executor_event.set()
        return True

    def _runtime_executor_stop_requested(self: Any) -> bool:
        svc = self.service
        return bool(
            svc._runtime_executor_stop_event.is_set()
            or svc._update_worker_stop_event.is_set()
            or svc._control_command_stop_event.is_set()
        )

    def _runtime_executor_loop(self: Any) -> None:
        svc = self.service
        while not self._runtime_executor_stop_requested():
            self._runtime_executor_wait_for_work(svc)
            if self._runtime_executor_should_continue():
                self._runtime_executor_drain_available_work()

    def _runtime_executor_should_continue(self: Any) -> bool:
        """Return whether the runtime executor should keep processing."""
        return not self._runtime_executor_stop_requested()

    def _runtime_executor_wait_for_work(self: Any, svc: Any) -> None:
        """Wait for and clear runtime executor wake events."""
        svc._runtime_executor_event.wait(0.5)
        svc._runtime_executor_event.clear()
        svc._update_worker_event.clear()
        svc._control_command_event.clear()

    def _runtime_executor_drain_available_work(self: Any) -> None:
        """Drain commands and update cycles until no more work is pending."""
        while self._runtime_executor_should_continue() and self._runtime_executor_run_once():
            pass

    def _runtime_executor_run_once(self: Any) -> bool:
        """Run one serialized runtime executor pass."""
        gateway_work = self._drain_gateway_core_commands_once()
        did_work = self._drain_control_commands_once()
        return bool(self._run_pending_update_cycle_once() or did_work or gateway_work)

    def _drain_gateway_core_commands_once(self: Any) -> bool:
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
                self._handle_gateway_core_command(payload, command_file=path)
            finally:
                inbox.remove(path)
        return True

    def _handle_gateway_core_command(self: Any, payload: Mapping[str, Any], *, command_file: str = "") -> None:
        """Translate one gateway command file into an existing control command."""
        if not self._is_gateway_user_command(payload):
            return
        _log_gateway_user_command(payload, command_file=command_file, now=time.time())
        if self._apply_gateway_write_if_supported(payload):
            return
        self._dispatch_gateway_control_command(payload)

    def _is_gateway_user_command(self: Any, payload: Mapping[str, Any]) -> bool:
        """Return whether a gateway command is intended for the core command API."""
        return (payload.get("kind") or payload.get("type")) == "user_command"

    def _apply_gateway_write_if_supported(self: Any, payload: Mapping[str, Any]) -> bool:
        """Let the DBus proxy consume gateway writes that map directly to local paths."""
        svc = self.service
        path = str(payload.get("path") or "")
        apply_gateway_write = getattr(getattr(svc, "_dbusservice", None), "apply_gateway_write", None)
        if not callable(apply_gateway_write):
            return False
        return require_bool(apply_gateway_write(path, payload.get("value")), "apply_gateway_write")

    def _dispatch_gateway_control_command(self: Any, payload: Mapping[str, Any]) -> None:
        """Dispatch a gateway command through the existing control command path."""
        svc = self.service
        path = str(payload.get("path") or "")
        command = svc._control_command_from_write(path, payload.get("value"), source="dbus")
        svc._handle_control_command(command)

    def _run_pending_update_cycle_once(self: Any) -> bool:
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


def _update_worker_enabled(svc: Any) -> bool:
    if not hasattr(svc, "_update_worker_enabled"):
        return False
    return bool(svc._update_worker_enabled)


def _update_worker_budget_seconds(svc: Any) -> float:
    raw = svc._update_worker_budget_seconds if hasattr(svc, "_update_worker_budget_seconds") else 5.0
    budget = finite_float_or_none(raw)
    return budget if budget is not None else 5.0


def _log_gateway_user_command(payload: Mapping[str, Any], *, command_file: str, now: float) -> None:
    logging.info(
        "Gateway user command source=%s origin=%s id=%s path=%s value=%r age_s=%s file=%s",
        _gateway_command_text(payload, "source", "unknown"),
        _gateway_command_text(payload, "origin", "unknown"),
        _gateway_command_text(payload, "id", "unknown"),
        _gateway_command_text(payload, "path", ""),
        payload.get("value"),
        _gateway_command_age_label(payload, now),
        command_file or "unknown",
    )


def _gateway_command_text(payload: Mapping[str, Any], key: str, default: str) -> str:
    text = str(payload.get(key) or "").strip()
    return text or default


def _gateway_command_age_label(payload: Mapping[str, Any], now: float) -> str:
    created_at = finite_float_or_none(payload.get("created_at"))
    if created_at is None or created_at <= 0.0:
        return "unknown"
    return f"{max(0.0, float(now) - created_at):.3f}"
