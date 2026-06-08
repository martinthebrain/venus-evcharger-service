# SPDX-License-Identifier: GPL-3.0-or-later
"""Lifecycle supervision for the advisory DBus introspection worker."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any


class DbusIntrospectionSupervisor:
    """Start and restart the optional DBus introspection worker process."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def stop_worker(self, force: bool = False) -> None:
        svc = self.service
        svc._ensure_worker_state()
        process = getattr(svc, "_dbus_introspection_worker_process", None)
        if process is None:
            return
        if process.poll() is not None:
            svc._dbus_introspection_worker_process = None
            return
        try:
            process.kill() if force else process.terminate()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to stop DBus introspection worker pid=%s: %s", getattr(process, "pid", "na"), error)

    def ensure_worker_process(self, now: float | None = None) -> None:
        svc = self.service
        svc._ensure_worker_state()
        if not bool(getattr(svc, "dbus_introspection_enabled", True)):
            self.stop_worker()
            return
        current = svc._time_now() if now is None else float(now)
        process = getattr(svc, "_dbus_introspection_worker_process", None)
        if process is not None and process.poll() is None:
            return
        if (
            float(getattr(svc, "_dbus_introspection_worker_last_start_at", 0.0) or 0.0) > 0.0
            and current - float(svc._dbus_introspection_worker_last_start_at) < float(getattr(svc, "dbus_introspection_restart_seconds", 30.0))
        ):
            return
        command = self._worker_command()
        try:
            process = subprocess.Popen(command)  # pylint: disable=consider-using-with
        except Exception as error:  # pylint: disable=broad-except
            svc._warning_throttled(
                "dbus-introspection-worker-start-failed",
                max(5.0, float(getattr(svc, "dbus_introspection_restart_seconds", 30.0) or 30.0)),
                "Unable to start DBus introspection worker: %s",
                error,
            )
            return
        svc._dbus_introspection_worker_process = process
        svc._dbus_introspection_worker_last_start_at = current
        logging.info(
            "Started DBus introspection worker pid=%s snapshot=%s",
            getattr(process, "pid", "na"),
            getattr(svc, "dbus_introspection_snapshot_path", ""),
        )

    def _worker_command(self) -> list[str]:
        svc = self.service
        return [
            sys.executable,
            "-u",
            svc._dbus_introspection_worker_path(),
            svc._config_path(),
            str(getattr(svc, "dbus_introspection_snapshot_path", "") or ""),
            str(getattr(svc, "dbus_introspection_request_path", "") or ""),
            str(os.getpid()),
        ]
