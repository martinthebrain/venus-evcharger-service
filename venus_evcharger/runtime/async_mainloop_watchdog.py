# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion publishing and mainloop watchdog helpers."""

from __future__ import annotations

import faulthandler
import logging
import os
import threading
import time
from typing import Any

from venus_evcharger.runtime.async_mainloop_control import _RuntimeAsyncMainloopControl

MAINLOOP_WATCHDOG_TRACEBACK_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _RuntimeAsyncMainloopWatchdog(_RuntimeAsyncMainloopControl):
    def flush_companion_dbus_publish_queue(self: Any) -> bool:
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

    def mainloop_heartbeat_tick(self: Any) -> bool:
        """Update an in-RAM heartbeat from the GLib thread."""
        self.service._mainloop_heartbeat_at = time.time()
        return True

    def start_mainloop_watchdog(self: Any) -> None:
        """Start a companion thread that proves and recovers GLib mainloop hangs."""
        svc = self.service
        if svc._mainloop_watchdog_thread is not None:
            return
        thread = threading.Thread(target=self._mainloop_watchdog_loop, name="evcharger-mainloop-watchdog", daemon=True)
        svc._mainloop_watchdog_thread = thread
        thread.start()

    def _mainloop_watchdog_loop(self: Any) -> None:
        svc = self.service
        while not svc._mainloop_watchdog_stop_event.wait(svc._mainloop_watchdog_interval_seconds):
            self._check_mainloop_watchdog(svc)

    def _check_mainloop_watchdog(self: Any, svc: Any) -> None:
        stale_seconds = svc._mainloop_watchdog_stale_seconds
        if stale_seconds <= 0:
            return
        heartbeat_age = time.time() - svc._mainloop_heartbeat_at
        if heartbeat_age <= stale_seconds:
            return
        self._dump_mainloop_watchdog_traceback(svc)
        logging.critical("Mainloop heartbeat stale for %.1fs; exiting for supervisor restart", heartbeat_age)
        self._exit_for_mainloop_watchdog()

    @staticmethod
    def _dump_mainloop_watchdog_traceback(svc: Any) -> None:
        path = str(svc._mainloop_watchdog_log_path)
        try:
            os.makedirs(os.path.dirname(path) or "/run", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"mainloop watchdog dump at {time.time():.3f}\n")
                faulthandler.dump_traceback(file=handle, all_threads=True)
        except MAINLOOP_WATCHDOG_TRACEBACK_ERRORS as error:
            logging.debug("Unable to write mainloop watchdog traceback: %s", error)

    @staticmethod
    def _exit_for_mainloop_watchdog() -> None:
        os._exit(75)
