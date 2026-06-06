# SPDX-License-Identifier: GPL-3.0-or-later
"""Async runtime helpers that keep the GLib/DBus main loop responsive."""

from __future__ import annotations

from collections import OrderedDict
import faulthandler
import logging
import os
import threading
import time
from typing import Any, cast

from venus_evcharger.control import ControlCommand
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


QueuedPublishValue = tuple[Any, float, float]
QueuedControlCommand = tuple[int, float, ControlCommand]


class _RuntimeSupportAsyncMainloopMixin(_ComposableControllerMixin):
    """Own background work and coalescing queues around the DBus main loop."""

    @staticmethod
    def _float_attr(value: Any, default: float = 0.0) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(default)

    def initialize_async_runtime_state(self) -> None:
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

    def mark_mainloop_thread(self) -> None:
        """Remember which thread owns VeDbusService writes."""
        svc = self.service
        svc._dbus_mainloop_thread_id = threading.get_ident()
        svc._dbus_async_publish_enabled = True

    def dbus_publish_direct_allowed(self) -> bool:
        """Return whether the caller may touch ``VeDbusService`` directly."""
        svc = self.service
        if not bool(getattr(svc, "_dbus_async_publish_enabled", False)):
            return True
        mainloop_thread_id = getattr(svc, "_dbus_mainloop_thread_id", None)
        return mainloop_thread_id is None or threading.get_ident() == int(mainloop_thread_id)

    def assert_dbus_mainloop_thread(self, operation: str = "dbus access") -> None:
        """Raise when code tries to touch a DBus service outside the GLib thread."""
        if self.dbus_publish_direct_allowed():
            return
        message = f"{operation} attempted outside GLib/DBus mainloop thread"
        logging.error(message)
        raise RuntimeError(message)

    def enqueue_dbus_publish_values(self, values: list[tuple[str, Any]], current: float) -> bool:
        """Coalesce DBus path writes for the GLib thread."""
        svc = self.service
        if not values:
            return False
        queued_at = time.time()
        with svc._dbus_publish_queue_lock:
            pending = cast("OrderedDict[str, QueuedPublishValue]", svc._dbus_publish_pending)
            for path, value in values:
                if path in pending:
                    del pending[path]
                pending[path] = (value, float(current), queued_at)
            while len(pending) > int(getattr(svc, "_dbus_publish_max_paths", 256)):
                pending.popitem(last=False)
                svc._dbus_publish_dropped_count += 1
            if pending:
                svc._dbus_publish_oldest_queued_at = min(item[2] for item in pending.values())
        return True

    def enqueue_dbus_update_index_bump(self, current: float) -> None:
        """Queue an UpdateIndex bump for the GLib thread."""
        svc = self.service
        with svc._dbus_publish_queue_lock:
            svc._dbus_publish_bump_pending += 1
            if getattr(svc, "_dbus_publish_oldest_queued_at", None) is None:
                svc._dbus_publish_oldest_queued_at = time.time()

    def enqueue_companion_dbus_publish(self, now: float | None = None) -> bool:
        """Coalesce optional companion-service publishes for the GLib thread."""
        svc = self.service
        with svc._companion_publish_lock:
            svc._companion_publish_pending = True
            svc._companion_publish_requested_at = time.time()
            svc._companion_publish_now = now
        return True

    @staticmethod
    def _bump_update_index_direct(svc: Any, current: float) -> None:
        index = int(svc._dbusservice["/UpdateIndex"]) + 1
        next_index = 0 if index > 255 else index
        svc._dbusservice["/UpdateIndex"] = next_index
        svc._dbus_publish_state["/UpdateIndex"] = {"value": next_index, "updated_at": current}

    def flush_dbus_publish_queue(self) -> bool:
        """Apply queued DBus writes quickly from the GLib thread."""
        svc = self.service
        if not hasattr(svc, "_dbusservice"):
            return True
        self.assert_dbus_mainloop_thread("main DBus publish flush")
        started = time.monotonic()
        now = time.time()
        with svc._dbus_publish_queue_lock:
            pending = cast("OrderedDict[str, QueuedPublishValue]", svc._dbus_publish_pending)
            values = list(pending.items())
            pending.clear()
            bump_count = int(getattr(svc, "_dbus_publish_bump_pending", 0))
            svc._dbus_publish_bump_pending = 0
            oldest_queued_at = getattr(svc, "_dbus_publish_oldest_queued_at", None)
            svc._dbus_publish_oldest_queued_at = None

        if oldest_queued_at is not None:
            svc._last_dbus_publish_queue_lag_seconds = max(0.0, now - float(oldest_queued_at))

        failed_paths: list[str] = []
        for path, (value, current, _queued_at) in values:
            try:
                svc._dbusservice[path] = value
                svc._dbus_publish_state[path] = {"value": value, "updated_at": current}
            except Exception:  # pylint: disable=broad-except
                failed_paths.append(path)
        if failed_paths:
            mark_failure = getattr(svc, "_mark_failure", None)
            if callable(mark_failure):
                mark_failure("dbus")
            logging.warning("DBus publish queue failed for paths %s", ",".join(failed_paths))

        for _index in range(max(0, bump_count)):
            try:
                self._bump_update_index_direct(svc, now)
            except Exception:  # pylint: disable=broad-except
                logging.warning("DBus publish queue failed to bump /UpdateIndex")
                break

        duration = time.monotonic() - started
        svc._last_publish_flush_duration_seconds = duration
        if duration > self._float_attr(getattr(svc, "_dbus_publish_budget_seconds", 0.1), 0.1):
            logging.warning("DBus publish flush exceeded budget: %.3fs", duration)
        self.flush_companion_dbus_publish_queue()
        return True

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
            svc._runtime_executor_event.wait(0.5)
            svc._runtime_executor_event.clear()
            svc._update_worker_event.clear()
            svc._control_command_event.clear()
            if self._runtime_executor_stop_requested():
                break
            while not self._runtime_executor_stop_requested():
                did_work = self._drain_control_commands_once()
                did_work = self._run_pending_update_cycle_once() or did_work
                if not did_work:
                    break

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

    def enqueue_control_command(self, command: ControlCommand) -> bool:
        """Coalesce DBus control commands for a background worker."""
        svc = self.service
        if not bool(getattr(svc, "_control_command_async_enabled", False)):
            result = svc._handle_control_command(command)
            return bool(result.accepted)
        queued_at = time.time()
        with svc._control_command_lock:
            pending = cast("OrderedDict[str, QueuedControlCommand]", svc._control_command_pending)
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

    def start_control_command_worker(self) -> None:
        """Enable DBus command execution in the serialized runtime executor."""
        svc = self.service
        svc._control_command_async_enabled = True
        self._start_runtime_executor()

    def _control_command_worker_loop(self) -> None:
        """Compatibility entry point for older tests; use the serialized executor."""
        self._runtime_executor_loop()

    def _drain_control_commands_once(self) -> bool:
        svc = self.service
        with svc._control_command_lock:
            pending = cast("OrderedDict[str, QueuedControlCommand]", svc._control_command_pending)
            commands = sorted(pending.values(), key=lambda item: item[0])
            pending.clear()
        if not commands:
            return False
        for _sequence, queued_at, command in commands:
            started = time.monotonic()
            svc._last_write_command_queue_lag_seconds = max(0.0, time.time() - queued_at)
            try:
                svc._handle_control_command(command)
            except Exception:  # pylint: disable=broad-except
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

    def flush_companion_dbus_publish_queue(self) -> bool:
        """Run any coalesced companion-service publish in the GLib thread."""
        svc = self.service
        with svc._companion_publish_lock:
            if not svc._companion_publish_pending:
                return False
            svc._companion_publish_pending = False
            publish_now = svc._companion_publish_now
            svc._companion_publish_now = None
        bridge = getattr(svc, "_companion_dbus_bridge", None)
        if bridge is None:
            return False
        self.assert_dbus_mainloop_thread("companion DBus publish flush")
        return bool(bridge.publish(publish_now))

    def mainloop_heartbeat_tick(self) -> bool:
        """Update an in-RAM heartbeat from the GLib thread."""
        self.service._mainloop_heartbeat_at = time.time()
        return True

    def start_mainloop_watchdog(self) -> None:
        """Start a companion thread that proves and recovers GLib mainloop hangs."""
        svc = self.service
        if getattr(svc, "_mainloop_watchdog_thread", None) is not None:
            return
        thread = threading.Thread(target=self._mainloop_watchdog_loop, name="evcharger-mainloop-watchdog", daemon=True)
        svc._mainloop_watchdog_thread = thread
        thread.start()

    def _mainloop_watchdog_loop(self) -> None:
        svc = self.service
        while not svc._mainloop_watchdog_stop_event.wait(
            self._float_attr(getattr(svc, "_mainloop_watchdog_interval_seconds", 1.0), 1.0)
        ):
            heartbeat_at = self._float_attr(getattr(svc, "_mainloop_heartbeat_at", 0.0), 0.0)
            stale_seconds = self._float_attr(getattr(svc, "_mainloop_watchdog_stale_seconds", 0.0), 0.0)
            if stale_seconds <= 0 or (time.time() - heartbeat_at) <= stale_seconds:
                continue
            self._dump_mainloop_watchdog_traceback(svc)
            logging.critical("Mainloop heartbeat stale for %.1fs; exiting for supervisor restart", time.time() - heartbeat_at)
            self._exit_for_mainloop_watchdog()

    @staticmethod
    def _dump_mainloop_watchdog_traceback(svc: Any) -> None:
        path = str(getattr(svc, "_mainloop_watchdog_log_path", "/run/dbus-venus-evcharger-mainloop-hang.log"))
        try:
            os.makedirs(os.path.dirname(path) or "/run", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"mainloop watchdog dump at {time.time():.3f}\n")
                faulthandler.dump_traceback(file=handle, all_threads=True)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to write mainloop watchdog traceback: %s", error)

    @staticmethod
    def _exit_for_mainloop_watchdog() -> None:
        os._exit(75)


__all__ = ["_RuntimeSupportAsyncMainloopMixin"]
