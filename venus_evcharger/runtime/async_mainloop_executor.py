# SPDX-License-Identifier: GPL-3.0-or-later
"""Serialized runtime executor helpers."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from venus_evcharger.control import ControlCommand
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.ipc.command_mailbox import (
    CommandMailboxReader,
    MailboxLockTimeout,
    MailboxScanUnavailable,
)
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.core_commands import (
    CoreControlCommand,
    core_command_retry_delay,
    parse_core_control_command,
)
from venus_evcharger.runtime.async_mainloop_control import ASYNC_CONTROL_COMMAND_ERRORS
from venus_evcharger.runtime.contracts import ControlCommandQueuePort

ASYNC_UPDATE_CYCLE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
CORE_MAILBOX_POLL_ERRORS = (MailboxLockTimeout, MailboxScanUnavailable, OSError)


@dataclass(frozen=True, slots=True)
class _CoreCommandRetry:
    expected: CommandPayload
    failure_count: int
    retry_at: float
    retire_only: bool
    reason: str


class RuntimeExecutor:
    """Serialize gateway commands, control commands, and update cycles."""

    def __init__(self, service: Any, control_commands: ControlCommandQueuePort) -> None:
        self.service = service
        self.control_commands = control_commands
        self._core_command_retries: dict[str, _CoreCommandRetry] = {}
        self._core_mailbox_poll_failures = 0

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
        inbox = getattr(
            self.service,
            "_core_command_mailbox",
            None,
        )
        if not isinstance(inbox, CommandMailboxReader):
            return False
        pending = self._load_core_pending(inbox)
        if pending is None:
            return False
        return self._drain_loaded_core_commands(inbox, pending)

    def _load_core_pending(
        self,
        inbox: CommandMailboxReader,
    ) -> list[tuple[str, CommandMapping]] | None:
        try:
            pending: list[tuple[str, CommandMapping]] = inbox.load_pending()
        except CORE_MAILBOX_POLL_ERRORS as error:
            self._core_mailbox_poll_failures += 1
            _log_repeated_failure(
                self._core_mailbox_poll_failures,
                "Core command mailbox poll deferred failures=%s error=%s",
                error,
            )
            return None
        self._core_mailbox_poll_failures = 0
        return pending

    def _drain_loaded_core_commands(
        self,
        inbox: CommandMailboxReader,
        pending: list[tuple[str, CommandMapping]],
    ) -> bool:
        if not pending:
            self._core_command_retries.clear()
            return False
        self._forget_absent_core_retries(pending)
        outcomes = [
            self._process_core_command(inbox, path, payload)
            for path, payload in inbox.coalesce(pending)
        ]
        return any(outcomes)

    def _process_core_command(
        self,
        inbox: CommandMailboxReader,
        path: str,
        payload: CommandMapping,
    ) -> bool:
        retry = self._matching_retry(path, payload)
        if _retry_is_waiting(retry, time.monotonic()):
            return False
        if _retry_needs_only_retirement(retry):
            return self._retire_core_command(inbox, path, payload)
        return self._dispatch_and_retire_core_command(inbox, path, payload)

    def _dispatch_and_retire_core_command(
        self,
        inbox: CommandMailboxReader,
        path: str,
        payload: CommandMapping,
    ) -> bool:
        try:
            self.handle_core_command(payload, command_file=path)
        except ASYNC_CONTROL_COMMAND_ERRORS as error:
            retry = self._defer_core_command(
                path,
                payload,
                reason="dispatch",
                retire_only=False,
            )
            _log_repeated_failure(
                retry.failure_count,
                "Core command dispatch deferred failures=%s file=%s error=%s",
                error,
                path,
            )
            return True
        return self._retire_core_command(inbox, path, payload)

    def _retire_core_command(
        self,
        inbox: CommandMailboxReader,
        path: str,
        payload: CommandMapping,
    ) -> bool:
        try:
            inbox.remove_if_current(path, payload)
        except CORE_MAILBOX_POLL_ERRORS as error:
            retry = self._defer_core_command(
                path,
                payload,
                reason="retire",
                retire_only=True,
            )
            _log_repeated_failure(
                retry.failure_count,
                "Core command retire deferred failures=%s file=%s error=%s",
                error,
                path,
            )
            return True
        self._core_command_retries.pop(path, None)
        return True

    def _matching_retry(
        self,
        path: str,
        payload: CommandMapping,
    ) -> _CoreCommandRetry | None:
        retry = self._core_command_retries.get(path)
        if retry is None:
            return None
        if retry.expected == payload:
            return retry
        self._core_command_retries.pop(path)
        return None

    def _defer_core_command(
        self,
        path: str,
        payload: CommandMapping,
        *,
        reason: str,
        retire_only: bool,
    ) -> _CoreCommandRetry:
        previous = self._matching_retry(path, payload)
        failure_count = 1
        if previous is not None and previous.reason == reason:
            failure_count = previous.failure_count + 1
        retry = _CoreCommandRetry(
            expected=dict(payload),
            failure_count=failure_count,
            retry_at=time.monotonic() + core_command_retry_delay(failure_count),
            retire_only=retire_only,
            reason=reason,
        )
        self._core_command_retries[path] = retry
        return retry

    def _forget_absent_core_retries(self, pending: list[tuple[str, CommandMapping]]) -> None:
        present = {path for path, _payload in pending}
        for path in tuple(self._core_command_retries):
            if path not in present:
                self._core_command_retries.pop(path)

    def handle_core_command(self, payload: CommandMapping, *, command_file: str = "") -> bool:
        """Validate and dispatch one command from the core IPC mailbox."""
        command = parse_core_control_command(payload)
        if command is None:
            logging.warning("Dropping invalid core control command file=%s", command_file or "unknown")
            return False
        _log_core_control_command(command, command_file=command_file, now=time.time())
        self.dispatch_core_control_command(command)
        return True

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


def _retry_is_waiting(retry: _CoreCommandRetry | None, now: float) -> bool:
    return retry is not None and now < retry.retry_at


def _retry_needs_only_retirement(retry: _CoreCommandRetry | None) -> bool:
    return retry is not None and retry.retire_only


def _log_repeated_failure(
    failure_count: int,
    message: str,
    error: Exception,
    *context: object,
) -> None:
    if failure_count & (failure_count - 1) == 0:
        logging.warning(message, failure_count, *context, error)
