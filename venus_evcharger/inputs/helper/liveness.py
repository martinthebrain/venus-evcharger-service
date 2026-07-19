# SPDX-License-Identifier: GPL-3.0-or-later
"""Heartbeat and parent-watchdog ownership for the auto-input helper."""

from __future__ import annotations

import logging
import os
import threading
import time

from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import MainLoopPort, SnapshotPort
from venus_evcharger.inputs.helper.glib_runtime import GLIB_RUNTIME


class WarningThrottle:
    """Throttle repeated helper warnings without coupling components to runtime state."""

    def __init__(self) -> None:
        self._last_logged_at: dict[str, float] = {}

    def __call__(self, key: str, interval_seconds: float, message: str, *args: object) -> None:
        now = time.time()
        last_logged = self._last_logged_at.get(key)
        if last_logged is None or now - last_logged > interval_seconds:
            logging.warning(message, *args)
            self._last_logged_at[key] = now


class HelperLiveness:
    """Own stop state and the two independent liveness threads."""

    def __init__(self, settings: AutoInputHelperSettings) -> None:
        self.settings = settings
        self.snapshots: SnapshotPort | None = None
        self.main_loop: MainLoopPort | None = None
        self._stop_requested = False
        self._thread_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._parent_thread: threading.Thread | None = None

    def bind(self, snapshots: SnapshotPort, main_loop: MainLoopPort) -> None:
        self.snapshots = snapshots
        self.main_loop = main_loop

    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        self._stop_requested = True
        if self.main_loop is not None:
            GLIB_RUNTIME.idle_add(self.main_loop.quit)

    def start(self) -> None:
        if self.snapshots is None or self.main_loop is None:
            raise RuntimeError("Helper liveness must be bound before start")
        self._thread_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="auto-input-heartbeat",
            daemon=True,
        )
        self._parent_thread = threading.Thread(
            target=self._parent_watchdog_loop,
            name="auto-input-parent-watchdog",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._parent_thread.start()

    def stop(self) -> None:
        self._stop_requested = True
        self._thread_stop.set()
        for thread in (self._heartbeat_thread, self._parent_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)

    def parent_watchdog_tick(self) -> bool:
        if self._stop_requested or self._parent_alive():
            return not self._stop_requested
        if self.main_loop is not None:
            self.main_loop.quit()
        return False

    def _heartbeat_loop(self) -> None:
        interval = max(0.5, min(2.0, self.settings.poll_interval_seconds or 1.0))
        while not self._thread_stop.wait(interval):
            if self._stop_requested:
                return
            self._write_heartbeat_once()

    def _write_heartbeat_once(self) -> None:
        try:
            if self.snapshots is None:
                raise RuntimeError("Helper liveness is not bound")
            self.snapshots.heartbeat()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto input helper heartbeat write failed: %s", error)

    def _parent_watchdog_loop(self) -> None:
        while not self._thread_stop.wait(1.0):
            if self._stop_requested:
                return
            if not self._parent_alive():
                self.request_stop()
                return

    def _parent_alive(self) -> bool:
        if self.settings.parent_pid is None:
            return True
        try:
            return os.getppid() == self.settings.parent_pid
        except OSError:
            return False
