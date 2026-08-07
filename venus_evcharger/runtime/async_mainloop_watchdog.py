# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion publishing and mainloop watchdog helpers."""

from __future__ import annotations

import faulthandler
import logging
import os
import threading
import time
from typing import Any

from venus_evcharger.core.shared import compact_json, write_text_atomically

MAINLOOP_WATCHDOG_TRACEBACK_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
PROCESS_HEARTBEAT_WRITE_ERRORS = (OSError, RuntimeError, TypeError, UnicodeEncodeError, ValueError)


class MainloopWatchdog:
    """Own GLib-mainloop liveness checks."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def heartbeat_tick(self) -> bool:
        """Update monotonic liveness state and the throttled RAM-file heartbeat."""
        monotonic_at = time.monotonic()
        epoch_at = time.time()
        self.service._mainloop_heartbeat_monotonic = monotonic_at
        self.service._mainloop_heartbeat_at = epoch_at
        self._write_process_heartbeat_if_due(self.service, monotonic_at, epoch_at)
        return True

    @staticmethod
    def _heartbeat_due(svc: Any, monotonic_at: float) -> bool:
        last_write = getattr(svc, "_process_heartbeat_last_write_monotonic", None)
        if isinstance(last_write, bool) or not isinstance(last_write, (int, float)):
            return True
        interval = max(1.0, float(svc._process_heartbeat_interval_seconds))
        elapsed = monotonic_at - float(last_write)
        return elapsed < 0.0 or elapsed >= interval

    @staticmethod
    def _write_process_heartbeat_if_due(svc: Any, monotonic_at: float, epoch_at: float) -> None:
        if not MainloopWatchdog._heartbeat_due(svc, monotonic_at):
            return
        path = str(svc._process_heartbeat_path)
        if not path.startswith("/run/"):
            logging.error("Refusing process heartbeat path outside /run: %s", path)
            return
        svc._process_heartbeat_last_write_monotonic = monotonic_at
        payload = compact_json(
            {
                "mainloop_heartbeat_at": epoch_at,
                "pid": os.getpid(),
                "process_heartbeat_at": epoch_at,
                "process_started_at": float(svc._process_started_at),
            }
        )
        try:
            write_text_atomically(path, payload)
        except PROCESS_HEARTBEAT_WRITE_ERRORS as error:
            logging.warning("Unable to write process heartbeat to %s: %s", path, error)

    def start(self) -> None:
        """Start a companion thread that proves and recovers GLib mainloop hangs."""
        svc = self.service
        if svc._mainloop_watchdog_thread is not None:
            return
        thread = threading.Thread(target=self._watchdog_loop, name="evcharger-mainloop-watchdog", daemon=True)
        svc._mainloop_watchdog_thread = thread
        thread.start()

    def _watchdog_loop(self) -> None:
        svc = self.service
        while not svc._mainloop_watchdog_stop_event.wait(svc._mainloop_watchdog_interval_seconds):
            self.check(svc)

    def check(self, svc: Any) -> None:
        stale_seconds = svc._mainloop_watchdog_stale_seconds
        if stale_seconds <= 0:
            return
        heartbeat_monotonic = getattr(svc, "_mainloop_heartbeat_monotonic", None)
        if isinstance(heartbeat_monotonic, bool) or not isinstance(heartbeat_monotonic, (int, float)):
            return
        heartbeat_age = max(0.0, time.monotonic() - float(heartbeat_monotonic))
        if heartbeat_age <= stale_seconds:
            return
        self.dump_traceback(svc)
        logging.critical("Mainloop heartbeat stale for %.1fs; exiting for supervisor restart", heartbeat_age)
        self.exit_for_restart()

    @staticmethod
    def dump_traceback(svc: Any) -> None:
        path = str(svc._mainloop_watchdog_log_path)
        try:
            os.makedirs(os.path.dirname(path) or "/run", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"mainloop watchdog dump at {time.time():.3f}\n")
                faulthandler.dump_traceback(file=handle, all_threads=True)
        except MAINLOOP_WATCHDOG_TRACEBACK_ERRORS as error:
            logging.debug("Unable to write mainloop watchdog traceback: %s", error)

    @staticmethod
    def exit_for_restart() -> None:
        os._exit(75)


__all__ = ["MainloopWatchdog"]
