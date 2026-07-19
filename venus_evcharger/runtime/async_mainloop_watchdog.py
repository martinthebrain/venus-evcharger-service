# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion publishing and mainloop watchdog helpers."""

from __future__ import annotations

import faulthandler
import logging
import os
import threading
import time
from typing import Any

from venus_evcharger.runtime.contracts import AsyncRuntimeStatePort

MAINLOOP_WATCHDOG_TRACEBACK_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class MainloopWatchdog:
    """Own companion publishing and GLib-mainloop liveness checks."""

    def __init__(self, service: Any, thread_guard: AsyncRuntimeStatePort) -> None:
        self.service = service
        self.thread_guard = thread_guard

    def flush_companion_publish(self) -> bool:
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
        self.thread_guard.assert_mainloop_thread("companion DBus publish flush")
        return bool(bridge.publish(publish_now))

    def heartbeat_tick(self) -> bool:
        """Update an in-RAM heartbeat from the GLib thread."""
        self.service._mainloop_heartbeat_at = time.time()
        return True

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
        heartbeat_age = time.time() - svc._mainloop_heartbeat_at
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
