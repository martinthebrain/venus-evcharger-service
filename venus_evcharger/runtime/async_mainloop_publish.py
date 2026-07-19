# SPDX-License-Identifier: GPL-3.0-or-later
"""Coalesced DBus publish queue helpers."""

from __future__ import annotations

import logging
import time
from typing import Any

from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH, evcs_fields_to_paths
from venus_evcharger.runtime.contracts import AsyncRuntimeStatePort, MainloopWatchdogPort
from venus_evcharger.runtime.async_mainloop_types import PublishQueue, QueuedPublishValue, require_publish_queue

DBUS_PUBLISH_QUEUE_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class DbusPublishQueue:
    """Coalesce and flush semantic or path-based DBus publishes."""

    def __init__(
        self,
        service: Any,
        thread_guard: AsyncRuntimeStatePort,
        watchdog: MainloopWatchdogPort,
    ) -> None:
        self.service = service
        self.thread_guard = thread_guard
        self.watchdog = watchdog

    def enqueue_values(self, values: list[tuple[str, Any]], current: float) -> bool:
        """Coalesce DBus path writes for the GLib thread."""
        svc = self.service
        if not values:
            return False
        queued_at = time.time()
        with svc._dbus_publish_queue_lock:
            pending = require_publish_queue(svc._dbus_publish_pending, "_dbus_publish_pending")
            self._coalesce_dbus_publish_values(pending, values, float(current), queued_at)
            self._trim_dbus_publish_queue(svc, pending)
            self._remember_oldest_dbus_publish(svc, pending)
        return True

    def enqueue_fields(self, fields: list[tuple[str, Any]], current: float) -> bool:
        """Coalesce semantic EVCS field writes for the GLib thread."""
        svc = self.service
        if not fields:
            return False
        queued_at = time.time()
        with svc._dbus_publish_queue_lock:
            pending = require_publish_queue(svc._dbus_publish_field_pending, "_dbus_publish_field_pending")
            self._coalesce_dbus_publish_values(pending, fields, float(current), queued_at)
            self._trim_dbus_publish_queue(svc, pending)
            self._remember_oldest_dbus_publish(svc, pending)
        return True

    @staticmethod
    def _coalesce_dbus_publish_values(
        pending: PublishQueue,
        values: list[tuple[str, Any]],
        current: float,
        queued_at: float,
    ) -> None:
        """Coalesce queued DBus writes so the newest value wins."""
        for path, value in values:
            if path in pending:
                del pending[path]
            pending[path] = (value, current, queued_at)

    @staticmethod
    def _trim_dbus_publish_queue(svc: Any, pending: PublishQueue) -> None:
        """Trim oldest queued DBus writes when the queue is full."""
        while len(pending) > int(svc._dbus_publish_max_paths):
            oldest_path = next(iter(pending))
            del pending[oldest_path]
            svc._dbus_publish_dropped_count += 1

    @staticmethod
    def _remember_oldest_dbus_publish(svc: Any, pending: PublishQueue) -> None:
        """Remember the oldest queued publish time for queue-lag diagnostics."""
        if pending:
            svc._dbus_publish_oldest_queued_at = min(item[2] for item in pending.values())

    def enqueue_update_index_bump(self, current: float) -> None:
        """Queue an UpdateIndex bump for the GLib thread."""
        svc = self.service
        with svc._dbus_publish_queue_lock:
            svc._dbus_publish_bump_pending += 1
            if svc._dbus_publish_oldest_queued_at is None:
                svc._dbus_publish_oldest_queued_at = time.time()

    def enqueue_companion_publish(self, now: float | None = None) -> bool:
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

    def flush(self) -> bool:
        """Apply queued DBus writes quickly from the GLib thread."""
        svc = self.service
        if not hasattr(svc, "_dbusservice"):
            return True
        self.thread_guard.assert_mainloop_thread("main DBus publish flush")
        started = time.monotonic()
        now = time.time()
        values, fields, bump_count, oldest_queued_at = self._drain_dbus_publish_queue(svc)
        self._remember_dbus_publish_queue_lag(svc, now, oldest_queued_at)
        failed_paths = self._apply_dbus_publish_values(svc, values)
        failed_paths.extend(self._apply_dbus_publish_fields(svc, fields))
        self._report_dbus_publish_failures(svc, failed_paths)
        self._flush_update_index_bumps(svc, now, bump_count)
        self._record_publish_flush_duration(svc, started)
        self.watchdog.flush_companion_publish()
        return True

    @staticmethod
    def _drain_dbus_publish_queue(
        svc: Any,
    ) -> tuple[list[tuple[str, QueuedPublishValue]], list[tuple[str, QueuedPublishValue]], int, float | None]:
        """Drain queued DBus writes and return their diagnostics."""
        with svc._dbus_publish_queue_lock:
            pending = require_publish_queue(svc._dbus_publish_pending, "_dbus_publish_pending")
            field_pending = require_publish_queue(svc._dbus_publish_field_pending, "_dbus_publish_field_pending")
            values = list(pending.items())
            fields = list(field_pending.items())
            pending.clear()
            field_pending.clear()
            bump_count = int(svc._dbus_publish_bump_pending)
            svc._dbus_publish_bump_pending = 0
            oldest_queued_at = svc._dbus_publish_oldest_queued_at
            svc._dbus_publish_oldest_queued_at = None
        return values, fields, bump_count, oldest_queued_at

    @staticmethod
    def _remember_dbus_publish_queue_lag(svc: Any, now: float, oldest_queued_at: float | None) -> None:
        """Record DBus publish queue lag from the oldest drained item."""
        if oldest_queued_at is not None:
            svc._last_dbus_publish_queue_lag_seconds = max(0.0, now - float(oldest_queued_at))

    @staticmethod
    def _apply_dbus_publish_values(svc: Any, values: list[tuple[str, QueuedPublishValue]]) -> list[str]:
        """Apply drained DBus publish values and return failed paths."""
        batch_publish = getattr(svc._dbusservice, "publish_paths", None)
        if callable(batch_publish):
            return DbusPublishQueue._apply_gateway_publish_values(svc, values, batch_publish)
        failed_paths: list[str] = []
        for path, (value, current, _queued_at) in values:
            try:
                svc._dbusservice[path] = value
                svc._dbus_publish_state[path] = {"value": value, "updated_at": current}
            except DBUS_PUBLISH_QUEUE_ERRORS:
                failed_paths.append(path)
        return failed_paths

    @staticmethod
    def _apply_gateway_publish_values(
        svc: Any,
        values: list[tuple[str, QueuedPublishValue]],
        batch_publish: Any,
    ) -> list[str]:
        if not values:
            return []
        try:
            batch_publish(DbusPublishQueue._gateway_publish_payload(values))
        except DBUS_PUBLISH_QUEUE_ERRORS:
            return [path for path, _item in values]
        DbusPublishQueue._remember_gateway_publish_success(svc, values)
        return []

    @staticmethod
    def _apply_dbus_publish_fields(svc: Any, fields: list[tuple[str, QueuedPublishValue]]) -> list[str]:
        """Apply drained semantic field publishes and return failed DBus paths."""
        if not fields:
            return []
        publish_fields = getattr(svc._dbusservice, "publish_fields", None)
        if callable(publish_fields):
            return DbusPublishQueue._apply_gateway_publish_fields(svc, fields, publish_fields)
        return DbusPublishQueue._apply_dbus_publish_field_fallback(svc, fields)

    @staticmethod
    def _apply_gateway_publish_fields(
        svc: Any,
        fields: list[tuple[str, QueuedPublishValue]],
        publish_fields: Any,
    ) -> list[str]:
        try:
            publish_fields(DbusPublishQueue._gateway_publish_payload(fields))
        except DBUS_PUBLISH_QUEUE_ERRORS:
            return DbusPublishQueue._field_paths(fields)
        DbusPublishQueue._remember_gateway_field_publish_success(svc, fields)
        return []

    @staticmethod
    def _apply_dbus_publish_field_fallback(svc: Any, fields: list[tuple[str, QueuedPublishValue]]) -> list[str]:
        values = DbusPublishQueue._path_values_from_fields(fields)
        return DbusPublishQueue._apply_dbus_publish_values(svc, values)

    @staticmethod
    def _gateway_publish_payload(values: list[tuple[str, QueuedPublishValue]]) -> dict[str, Any]:
        return {path: value for path, (value, _current, _queued_at) in values}

    @staticmethod
    def _path_values_from_fields(fields: list[tuple[str, QueuedPublishValue]]) -> list[tuple[str, QueuedPublishValue]]:
        payload = DbusPublishQueue._gateway_publish_payload(fields)
        paths = evcs_fields_to_paths(payload)
        values: list[tuple[str, QueuedPublishValue]] = []
        for field, item in fields:
            if field not in EVCS_FIELD_TO_PATH:
                continue
            path = EVCS_FIELD_TO_PATH[field]
            if paths.get(path) == item[0]:
                values.append((path, item))
        return values

    @staticmethod
    def _field_paths(fields: list[tuple[str, QueuedPublishValue]]) -> list[str]:
        return [path for path, _item in DbusPublishQueue._path_values_from_fields(fields)]

    @staticmethod
    def _remember_gateway_publish_success(svc: Any, values: list[tuple[str, QueuedPublishValue]]) -> None:
        for path, (value, current, _queued_at) in values:
            svc._dbus_publish_state[path] = {"value": value, "updated_at": current}

    @staticmethod
    def _remember_gateway_field_publish_success(svc: Any, fields: list[tuple[str, QueuedPublishValue]]) -> None:
        DbusPublishQueue._remember_gateway_publish_success(
            svc,
            DbusPublishQueue._path_values_from_fields(fields),
        )

    @staticmethod
    def _report_dbus_publish_failures(svc: Any, failed_paths: list[str]) -> None:
        """Record and log DBus publish failures."""
        if not failed_paths:
            return
        svc.runtime.mark_failure("dbus")
        logging.warning("DBus publish queue failed for paths %s", ",".join(failed_paths))

    def _flush_update_index_bumps(self: Any, svc: Any, now: float, bump_count: int) -> None:
        """Flush queued UpdateIndex bumps."""
        continue_bumps = True
        for _index in range(max(0, bump_count)):
            if continue_bumps:
                continue_bumps = self._bump_update_index_best_effort(svc, now)

    def _bump_update_index_best_effort(self: Any, svc: Any, now: float) -> bool:
        """Bump UpdateIndex and report whether more bumps should be attempted."""
        try:
            self._bump_update_index_direct(svc, now)
            return True
        except DBUS_PUBLISH_QUEUE_ERRORS:
            logging.warning("DBus publish queue failed to bump /UpdateIndex")
            return False

    def _record_publish_flush_duration(self: Any, svc: Any, started: float) -> None:
        """Record and budget-check publish flush duration."""
        duration = time.monotonic() - started
        svc._last_publish_flush_duration_seconds = duration
        if duration > _float_attr(svc._dbus_publish_budget_seconds, 0.1):
            logging.warning("DBus publish flush exceeded budget: %.3fs", duration)


def _float_attr(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(default)


__all__ = ["DbusPublishQueue"]
