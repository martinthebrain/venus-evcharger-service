# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract harnesses for service-facing DBus publisher tests."""

from __future__ import annotations

import logging
from types import SimpleNamespace


class PublishRuntimeHarness:
    """Expose the publisher's mandatory runtime port around test callbacks."""

    def __init__(self, service: PublishServiceHarness) -> None:
        self.service = service

    def assert_dbus_mainloop_thread(self, operation: str = "dbus access") -> None:
        callback = getattr(self.service, "_assert_dbus_mainloop_thread", None)
        if callable(callback):
            callback(operation)

    def dbus_publish_direct_allowed(self) -> bool:
        callback = getattr(self.service, "_dbus_publish_direct_allowed", None)
        return True if not callable(callback) else bool(callback())

    def enqueue_dbus_publish_fields(self, fields: list[tuple[str, object]], current: float) -> bool:
        callback = getattr(self.service, "_enqueue_dbus_publish_fields", None)
        return False if not callable(callback) else bool(callback(fields, current))

    def enqueue_dbus_publish_values(self, values: list[tuple[str, object]], current: float) -> bool:
        callback = getattr(self.service, "_enqueue_dbus_publish_values", None)
        return False if not callable(callback) else bool(callback(values, current))

    def enqueue_dbus_update_index_bump(self, current: float) -> None:
        callback = getattr(self.service, "_enqueue_dbus_update_index_bump", None)
        if callable(callback):
            callback(current)

    def enqueue_companion_dbus_publish(self, current: float) -> bool:
        callback = getattr(self.service, "_enqueue_companion_dbus_publish", None)
        return False if not callable(callback) else bool(callback(current))

    def worker_snapshot(self) -> dict[str, object]:
        callback = getattr(self.service, "_get_worker_snapshot", None)
        if not callable(callback):
            return {}
        snapshot = callback()
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def mark_failure(self, source_key: str) -> None:
        callback = getattr(self.service, "_mark_failure", None)
        if callable(callback):
            callback(source_key)

    def warning_throttled(self, key: str, interval: float, message: str, *args: object) -> None:
        callback = getattr(self.service, "_warning_throttled", None)
        if callable(callback):
            callback(key, interval, message, *args)
            return
        logging.warning(message, *args)

    def source_retry_remaining(self, source_key: str, now: float | None = None) -> int:
        callback = getattr(self.service, "_runtime_source_retry_remaining", None)
        if callable(callback):
            return int(callback(source_key, now))
        retry_after_by_source = getattr(self.service, "_source_retry_after", None)
        if not isinstance(retry_after_by_source, dict):
            return 0
        retry_after = retry_after_by_source.get(source_key)
        if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool):
            return 0
        current = 0.0 if now is None else float(now)
        return max(0, int(float(retry_after) - current))

    def update_is_stale(self, now: float | None = None) -> bool:
        callback = getattr(self.service, "_runtime_update_is_stale", None)
        return False if not callable(callback) else bool(callback(now))


class PublishServiceHarness(SimpleNamespace):
    """Dynamic service state with one explicit publisher runtime port."""

    def __init__(self, **values: object) -> None:
        super().__init__(**values)
        if not hasattr(self, "runtime"):
            self.runtime = PublishRuntimeHarness(self)


__all__ = ["PublishRuntimeHarness", "PublishServiceHarness"]
