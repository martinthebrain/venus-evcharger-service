# SPDX-License-Identifier: GPL-3.0-or-later
"""Coalesced DBus publish queue helpers."""

from __future__ import annotations

from collections import OrderedDict
import logging
import time
from typing import Any, cast

from venus_evcharger.runtime.async_mainloop_types import QueuedPublishValue


class _RuntimeSupportAsyncMainloopPublishMixin:
    def enqueue_dbus_publish_values(self: Any, values: list[tuple[str, Any]], current: float) -> bool:
        """Coalesce DBus path writes for the GLib thread."""
        svc = self.service
        if not values:
            return False
        queued_at = time.time()
        with svc._dbus_publish_queue_lock:
            pending = cast("OrderedDict[str, QueuedPublishValue]", svc._dbus_publish_pending)
            self._coalesce_dbus_publish_values(pending, values, float(current), queued_at)
            self._trim_dbus_publish_queue(svc, pending)
            self._remember_oldest_dbus_publish(svc, pending)
        return True

    @staticmethod
    def _coalesce_dbus_publish_values(
        pending: "OrderedDict[str, QueuedPublishValue]",
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
    def _trim_dbus_publish_queue(svc: Any, pending: "OrderedDict[str, QueuedPublishValue]") -> None:
        """Trim oldest queued DBus writes when the queue is full."""
        while len(pending) > int(getattr(svc, "_dbus_publish_max_paths", 256)):
            pending.popitem(last=False)
            svc._dbus_publish_dropped_count += 1

    @staticmethod
    def _remember_oldest_dbus_publish(svc: Any, pending: "OrderedDict[str, QueuedPublishValue]") -> None:
        """Remember the oldest queued publish time for queue-lag diagnostics."""
        if pending:
            svc._dbus_publish_oldest_queued_at = min(item[2] for item in pending.values())

    def enqueue_dbus_update_index_bump(self: Any, current: float) -> None:
        """Queue an UpdateIndex bump for the GLib thread."""
        svc = self.service
        with svc._dbus_publish_queue_lock:
            svc._dbus_publish_bump_pending += 1
            if getattr(svc, "_dbus_publish_oldest_queued_at", None) is None:
                svc._dbus_publish_oldest_queued_at = time.time()

    def enqueue_companion_dbus_publish(self: Any, now: float | None = None) -> bool:
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

    def flush_dbus_publish_queue(self: Any) -> bool:
        """Apply queued DBus writes quickly from the GLib thread."""
        svc = self.service
        if not hasattr(svc, "_dbusservice"):
            return True
        self.assert_dbus_mainloop_thread("main DBus publish flush")
        started = time.monotonic()
        now = time.time()
        values, bump_count, oldest_queued_at = self._drain_dbus_publish_queue(svc)
        self._remember_dbus_publish_queue_lag(svc, now, oldest_queued_at)
        failed_paths = self._apply_dbus_publish_values(svc, values)
        self._report_dbus_publish_failures(svc, failed_paths)
        self._flush_update_index_bumps(svc, now, bump_count)
        self._record_publish_flush_duration(svc, started)
        self.flush_companion_dbus_publish_queue()
        return True

    @staticmethod
    def _drain_dbus_publish_queue(svc: Any) -> tuple[list[tuple[str, QueuedPublishValue]], int, float | None]:
        """Drain queued DBus writes and return their diagnostics."""
        with svc._dbus_publish_queue_lock:
            pending = cast("OrderedDict[str, QueuedPublishValue]", svc._dbus_publish_pending)
            values = list(pending.items())
            pending.clear()
            bump_count = int(getattr(svc, "_dbus_publish_bump_pending", 0))
            svc._dbus_publish_bump_pending = 0
            oldest_queued_at = getattr(svc, "_dbus_publish_oldest_queued_at", None)
            svc._dbus_publish_oldest_queued_at = None
        return values, bump_count, oldest_queued_at

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
            return _RuntimeSupportAsyncMainloopPublishMixin._apply_gateway_publish_values(svc, values, batch_publish)
        failed_paths: list[str] = []
        for path, (value, current, _queued_at) in values:
            try:
                svc._dbusservice[path] = value
                svc._dbus_publish_state[path] = {"value": value, "updated_at": current}
            except Exception:  # pylint: disable=broad-except
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
            batch_publish(_RuntimeSupportAsyncMainloopPublishMixin._gateway_publish_payload(values))
        except Exception:  # pylint: disable=broad-except
            return [path for path, _item in values]
        _RuntimeSupportAsyncMainloopPublishMixin._remember_gateway_publish_success(svc, values)
        return []

    @staticmethod
    def _gateway_publish_payload(values: list[tuple[str, QueuedPublishValue]]) -> dict[str, Any]:
        return {path: value for path, (value, _current, _queued_at) in values}

    @staticmethod
    def _remember_gateway_publish_success(svc: Any, values: list[tuple[str, QueuedPublishValue]]) -> None:
        for path, (value, current, _queued_at) in values:
            svc._dbus_publish_state[path] = {"value": value, "updated_at": current}

    @staticmethod
    def _report_dbus_publish_failures(svc: Any, failed_paths: list[str]) -> None:
        """Record and log DBus publish failures."""
        if not failed_paths:
            return
        mark_failure = getattr(svc, "_mark_failure", None)
        if callable(mark_failure):
            mark_failure("dbus")
        logging.warning("DBus publish queue failed for paths %s", ",".join(failed_paths))

    def _flush_update_index_bumps(self: Any, svc: Any, now: float, bump_count: int) -> None:
        """Flush queued UpdateIndex bumps."""
        for _index in range(max(0, bump_count)):
            if not self._bump_update_index_best_effort(svc, now):
                break

    def _bump_update_index_best_effort(self: Any, svc: Any, now: float) -> bool:
        """Bump UpdateIndex and report whether more bumps should be attempted."""
        try:
            self._bump_update_index_direct(svc, now)
            return True
        except Exception:  # pylint: disable=broad-except
            logging.warning("DBus publish queue failed to bump /UpdateIndex")
            return False

    def _record_publish_flush_duration(self: Any, svc: Any, started: float) -> None:
        """Record and budget-check publish flush duration."""
        duration = time.monotonic() - started
        svc._last_publish_flush_duration_seconds = duration
        if duration > self._float_attr(getattr(svc, "_dbus_publish_budget_seconds", 0.1), 0.1):
            logging.warning("DBus publish flush exceeded budget: %.3fs", duration)
