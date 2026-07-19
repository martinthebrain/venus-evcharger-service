# SPDX-License-Identifier: GPL-3.0-or-later
"""Lifecycle helpers for the Shelly I/O background worker."""

from __future__ import annotations

import threading
from collections.abc import Callable

import requests

from venus_evcharger.backend.errors import BACKEND_OPTIONAL_CAPABILITY_ERRORS
from venus_evcharger.backend.shelly_io_ports import ShellyLifecycleHost
from venus_evcharger.backend.shelly_io_types import is_settable_event, optional_json_object
from venus_evcharger.backend.shelly_io_worker_transport import ShellyWorkerTransport


class ShellyWorkerLifecycle:
    """Start, restart, and inspect the Shelly background worker thread."""

    def __init__(
        self,
        service: ShellyLifecycleHost,
        transport: ShellyWorkerTransport,
        clock: Callable[[], float],
        worker_loop: Callable[[], None],
    ) -> None:
        self.service = service
        self.transport = transport
        self._clock = clock
        self._worker_loop = worker_loop

    @staticmethod
    def _worker_stale_restart_seconds(svc: ShellyLifecycleHost) -> float:
        poll_seconds = max(
            0.2,
            ShellyWorkerLifecycle._runtime_seconds_setting(svc, "_worker_poll_interval_seconds", 1.0),
        )
        request_timeout = ShellyWorkerLifecycle._runtime_seconds_setting(
            svc, "shelly_request_timeout_seconds", 2.0
        )
        relay_sync_timeout = ShellyWorkerLifecycle._runtime_seconds_setting(
            svc, "relay_sync_timeout_seconds", 2.0
        )
        return max(5.0, poll_seconds * 5.0, request_timeout * 3.0, relay_sync_timeout * 2.0)

    @staticmethod
    def _runtime_seconds_setting(svc: ShellyLifecycleHost, name: str, fallback: float) -> float:
        value = getattr(svc, name, None)
        if not value:
            return fallback
        return float(value)

    @staticmethod
    def _worker_snapshot_captured_at(svc: ShellyLifecycleHost) -> float | None:
        snapshot = ShellyWorkerLifecycle._worker_snapshot_payload(svc)
        if snapshot is None:
            return None
        return ShellyWorkerLifecycle._worker_snapshot_number(snapshot, "captured_at")

    @staticmethod
    def _worker_snapshot_payload(svc: ShellyLifecycleHost) -> dict[str, object] | None:
        try:
            snapshot = svc.runtime.worker_snapshot()
        except BACKEND_OPTIONAL_CAPABILITY_ERRORS:
            return None
        return optional_json_object(snapshot)

    @staticmethod
    def _worker_snapshot_number(snapshot: dict[str, object], key: str) -> float | None:
        value = snapshot.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return float(value)

    @classmethod
    def _worker_snapshot_age(cls, svc: ShellyLifecycleHost, now: float) -> float | None:
        captured_at = cls._worker_snapshot_captured_at(svc)
        if captured_at is None:
            return None
        return max(0.0, float(now) - captured_at)

    @classmethod
    def _worker_thread_stale(cls, svc: ShellyLifecycleHost, now: float) -> bool:
        thread = getattr(svc, "_worker_thread", None)
        if thread is None or not thread.is_alive():
            return False
        snapshot_age = cls._worker_snapshot_age(svc, now)
        return snapshot_age is not None and snapshot_age > cls._worker_stale_restart_seconds(svc)

    def _restart_stale_io_worker(self, now: float) -> None:
        svc = self.service
        stale_after = self._worker_stale_restart_seconds(svc)
        snapshot_age = self._worker_snapshot_age(svc, now)
        svc.runtime.warning_throttled(
            "io-worker-stale-restart",
            stale_after,
            "Background I/O worker stale for %.1fs, restarting worker session",
            self._display_snapshot_age(snapshot_age),
        )
        self._set_object_event(getattr(svc, "_worker_stop_event", None))
        self.transport.close_object(getattr(svc, "_worker_session", None))
        svc._worker_stop_event = threading.Event()
        svc._worker_session = requests.Session()
        svc._worker_thread = None

    @staticmethod
    def _display_snapshot_age(snapshot_age: float | None) -> float:
        return -1.0 if snapshot_age is None else float(snapshot_age)

    @staticmethod
    def _set_object_event(candidate: object) -> None:
        if is_settable_event(candidate):
            candidate.set()

    def start_io_worker(self) -> None:
        svc = self.service
        svc.runtime.ensure_worker_state()
        current = self._clock()
        if self._worker_thread_stale(svc, current):
            self._restart_stale_io_worker(current)
        if svc._worker_thread is None or not svc._worker_thread.is_alive():
            svc._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="shelly-wallbox-shelly-io",
                daemon=True,
            )
            svc._worker_thread.start()
        svc.runtime.ensure_auto_input_helper()


__all__ = ["ShellyWorkerLifecycle"]
