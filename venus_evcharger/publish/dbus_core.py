# SPDX-License-Identifier: GPL-3.0-or-later
"""Core DBus publish and transactional write helpers."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.core.dbus_backpressure import service_dbus_backpressure_policy
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH, evcs_fields_to_paths
from venus_evcharger.publish.dbus_shared import (
    DbusPublishContext,
    PublishServicePort,
    PublishServiceValueSnapshot,
    PublishStateEntry,
    PublishValue,
    is_object_mapping,
)

PUBLISH_DBUS_SERVICE_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


def _publish_state_entry(value: object) -> PublishStateEntry | None:
    if not is_object_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def _semantic_field_path(field: object) -> str | None:
    path = EVCS_FIELD_TO_PATH.get(str(field))
    return None if path is None else str(path)


class DbusPublishCore:
    """Own transactional, throttled writes to the gateway publish surface."""

    def __init__(self, context: DbusPublishContext) -> None:
        self.service: PublishServicePort = context.service

    def ensure_state(self) -> None:
        """Initialize DBus publish throttling helpers for tests or partial instances."""
        if not hasattr(self.service, "_dbus_publish_state"):
            self.service._dbus_publish_state = {}
        if not hasattr(self.service, "_dbus_live_publish_interval_seconds"):
            self.service._dbus_live_publish_interval_seconds = 1.0
        if not hasattr(self.service, "_dbus_slow_publish_interval_seconds"):
            self.service._dbus_slow_publish_interval_seconds = 5.0

    def _effective_publish_interval(
        self,
        interval_seconds: float | None,
        *,
        group_name: str,
        force: bool,
    ) -> float | None:
        """Return the interval after advisory gateway-health throttling."""
        if force or interval_seconds is None:
            return interval_seconds
        interval = service_dbus_backpressure_policy(self.service).publish_interval_seconds(
            float(interval_seconds),
            group=group_name,
        )
        return float(interval)

    def _should_enqueue_publish(self) -> bool:
        """Return whether DBus writes must be handed to the GLib thread."""
        return not self.service.runtime.dbus_publish_direct_allowed()

    def _enqueue_publish_values(self, staged_values: Sequence[tuple[str, PublishValue]], current: float) -> bool:
        return bool(self.service.runtime.enqueue_dbus_publish_values(list(staged_values), current))

    def _enqueue_publish_fields(self, staged_fields: Sequence[tuple[str, PublishValue]], current: float) -> bool:
        return bool(self.service.runtime.enqueue_dbus_publish_fields(list(staged_fields), current))

    def _assert_dbus_access_allowed(self, operation: str) -> None:
        self.service.runtime.assert_dbus_mainloop_thread(operation)

    def publish_path(
        self,
        path: str,
        value: PublishValue,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish a DBus path immediately, on change, or with a minimum interval."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        effective_interval = self._effective_publish_interval(
            interval_seconds,
            group_name="single-path",
            force=force,
        )
        should_write, _entry = self._publish_decision(path, value, current, effective_interval, force)
        if not should_write:
            return False

        if self._should_enqueue_publish():
            return self._enqueue_publish_values([(path, value)], current)

        self._assert_dbus_access_allowed(f"publish {path}")
        self.service._dbusservice[path] = value
        self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
        return True

    def publish_field(
        self,
        field: str,
        value: PublishValue,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish one semantic EVCS field immediately, on change, or by interval."""
        return self.publish_fields(
            "single-field",
            {str(field): value},
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def _publish_decision(
        self,
        path: str,
        value: PublishValue,
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> tuple[bool, PublishStateEntry | None]:
        """Return whether one path should be written plus its current publish-state entry."""
        entry = _publish_state_entry(self.service._dbus_publish_state.get(path))
        if force or entry is None:
            return True, entry
        last_value, last_updated_at = self._publish_state_fields(entry)
        if interval_seconds is None:
            return value != last_value, entry
        return self._publish_interval_elapsed(last_updated_at, current, interval_seconds), entry

    @staticmethod
    def _publish_state_fields(entry: PublishStateEntry) -> tuple[PublishValue, PublishValue]:
        """Return the stored publish-state value and timestamp."""
        return entry.get("value"), entry.get("updated_at")

    @staticmethod
    def _publish_interval_elapsed(last_updated_at: PublishValue, current: float, interval_seconds: float) -> bool:
        """Return whether the publish interval is due for one path."""
        normalized_updated_at = finite_float_or_none(last_updated_at)
        return normalized_updated_at is None or (current - normalized_updated_at) >= float(interval_seconds)

    def _publish_group_failure(self, group_name: str, failed_paths: Sequence[str]) -> None:
        """Record one DBus publish-group failure without raising into the caller."""
        self.service.runtime.mark_failure("dbus")
        self.service.runtime.warning_throttled(
            f"dbus-publish-{group_name}-failed",
            1.0,
            "DBus publish group %s failed for paths %s",
            group_name,
            ",".join(failed_paths),
        )

    def _restore_group_publish_state(self, staged_entries: Mapping[str, PublishStateEntry | None]) -> None:
        """Best-effort restore of local DBus publish bookkeeping after a failed group publish."""
        for path, entry in staged_entries.items():
            if entry is None:
                self.service._dbus_publish_state.pop(path, None)
            else:
                self.service._dbus_publish_state[path] = dict(entry)

    def _service_value_snapshot(self, path: str) -> PublishServiceValueSnapshot:
        """Return whether one DBus path existed before publishing plus its previous value."""
        self._assert_dbus_access_allowed(f"snapshot {path}")
        try:
            return True, self.service._dbusservice[path]
        except PUBLISH_DBUS_SERVICE_ERRORS:
            return False, None

    def _stage_publish_values(
        self,
        values: Mapping[str, PublishValue],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> tuple[list[tuple[str, PublishValue]], dict[str, PublishStateEntry | None], dict[str, PublishServiceValueSnapshot]]:
        """Collect the DBus values that should be written in one transactional batch."""
        staged_values: list[tuple[str, PublishValue]] = []
        staged_entries: dict[str, PublishStateEntry | None] = {}
        original_service_values: dict[str, PublishServiceValueSnapshot] = {}
        for path, value in values.items():
            should_write, entry = self._publish_decision(path, value, current, interval_seconds, force)
            if not should_write:
                continue
            staged_values.append((path, value))
            staged_entries[path] = None if entry is None else dict(entry)
            original_service_values[path] = self._service_value_snapshot(path)
        return staged_values, staged_entries, original_service_values

    def _apply_staged_publish_values(
        self,
        staged_values: Sequence[tuple[str, PublishValue]],
        current: float,
    ) -> tuple[bool, list[str], str | None]:
        """Apply one staged publish batch and report any failed path."""
        changed = False
        published_paths: list[str] = []
        for path, value in staged_values:
            self._assert_dbus_access_allowed(f"publish {path}")
            try:
                self.service._dbusservice[path] = value
            except PUBLISH_DBUS_SERVICE_ERRORS:
                return changed, published_paths, path
            self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
            published_paths.append(path)
            changed = True
        return changed, published_paths, None

    def _restore_service_values(
        self,
        published_paths: Sequence[str],
        original_service_values: Mapping[str, PublishServiceValueSnapshot],
    ) -> None:
        """Best-effort restore of DBus path values after a failed transactional publish."""
        for path in published_paths:
            had_original, original_value = original_service_values.get(path, (False, None))
            if not had_original:
                self._assert_dbus_access_allowed(f"delete {path}")
                try:
                    del self.service._dbusservice[path]
                except PUBLISH_DBUS_SERVICE_ERRORS:
                    pass
                continue
            self._assert_dbus_access_allowed(f"restore {path}")
            try:
                self.service._dbusservice[path] = original_value
            except PUBLISH_DBUS_SERVICE_ERRORS:
                pass

    def _publish_values_transactional(
        self,
        group_name: str,
        values: Mapping[str, PublishValue],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish one DBus path group with shared best-effort rollback and failure reporting.

        A number of DBus values form one logical snapshot, for example a bundle
        of diagnostics or all live AC measurements. If one write in that bundle
        fails, we restore the bookkeeping for paths that were already staged so
        the next publish cycle can try again from a clean baseline.

        This is not a hard transactional database model. It is a practical
        "keep related values together as much as possible" strategy for the
        Venus DBus surface.
        """
        self.ensure_state()
        current = time.time() if now is None else float(now)
        effective_interval = self._effective_publish_interval(
            interval_seconds,
            group_name=group_name,
            force=force,
        )
        if self._should_enqueue_publish():
            return self._enqueue_transactional_publish(values, current, effective_interval, force)

        staged_values, staged_entries, original_service_values = self._stage_publish_values(
            values,
            current,
            effective_interval,
            force,
        )

        if not staged_values:
            return False

        changed, published_paths, failed_path = self._apply_staged_publish_values(staged_values, current)
        if failed_path is None:
            return changed

        self._restore_service_values(published_paths, original_service_values)
        self._restore_group_publish_state(staged_entries)
        self._publish_group_failure(group_name, [failed_path])
        return False

    def publish_fields(
        self,
        group_name: str,
        fields: Mapping[str, PublishValue],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish one semantic EVCS field group through the gateway contract."""
        self.ensure_state()
        paths = evcs_fields_to_paths(fields)
        current = time.time() if now is None else float(now)
        effective_interval = self._effective_publish_interval(
            interval_seconds,
            group_name=group_name,
            force=force,
        )
        if self._should_enqueue_publish():
            staged_fields = self._staged_fields_for_enqueue(fields, paths, current, effective_interval, force)
            return bool(staged_fields) and self._enqueue_publish_fields(staged_fields, current)
        return self._publish_values_transactional(
            group_name,
            paths,
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def _enqueue_transactional_publish(
        self,
        values: Mapping[str, PublishValue],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> bool:
        """Stage and enqueue transactional publish values for the mainloop."""
        staged_values = self._staged_values_for_enqueue(values, current, interval_seconds, force)
        if not staged_values:
            return False
        return self._enqueue_publish_values(staged_values, current)

    def _staged_values_for_enqueue(
        self,
        values: Mapping[str, PublishValue],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> list[tuple[str, PublishValue]]:
        """Return only values that should be written by the publish queue."""
        staged_values: list[tuple[str, PublishValue]] = []
        for path, value in values.items():
            should_write, _entry = self._publish_decision(path, value, current, interval_seconds, force)
            if should_write:
                staged_values.append((path, value))
        return staged_values

    def _staged_fields_for_enqueue(
        self,
        fields: Mapping[str, PublishValue],
        paths: Mapping[str, PublishValue],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> list[tuple[str, PublishValue]]:
        """Return semantic fields whose mapped DBus path should be written."""
        staged_fields: list[tuple[str, PublishValue]] = []
        for field, value in fields.items():
            path = _semantic_field_path(field)
            if path is None:
                continue
            should_write, _entry = self._publish_decision(path, paths[path], current, interval_seconds, force)
            if should_write:
                staged_fields.append((str(field), value))
        return staged_fields

    @staticmethod
    def _field_items_to_path_items(fields: Sequence[tuple[str, PublishValue]]) -> list[tuple[str, PublishValue]]:
        """Map semantic field tuples to DBus path tuples for a path-based queue."""
        path_items: list[tuple[str, PublishValue]] = []
        for field, value in fields:
            path = _semantic_field_path(field)
            if path is not None:
                path_items.append((path, value))
        return path_items

    def _publish_values(
        self,
        values: Mapping[str, PublishValue],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish a group of DBus values with shared throttling rules."""
        return self._publish_values_transactional(
            "generic",
            values,
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def bump_update_index(self, now: float | None = None) -> None:
        """Increment UpdateIndex when a set of published values changed."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        if self._enqueue_update_index_bump(current):
            return
        update_index_path = EVCS_FIELD_TO_PATH["update_index"]
        self._assert_dbus_access_allowed(f"bump {update_index_path}")
        next_index = self._next_update_index(self.service._dbusservice[update_index_path])
        self.service._dbusservice[update_index_path] = next_index
        self.service._dbus_publish_state[update_index_path] = {"value": next_index, "updated_at": current}

    def _enqueue_update_index_bump(self, current: float) -> bool:
        """Queue one index bump when direct publishing is unavailable."""
        if not self._should_enqueue_publish():
            return False
        self.service.runtime.enqueue_dbus_update_index_bump(current)
        return True

    @staticmethod
    def _next_update_index(raw_index: object) -> int:
        """Validate, increment, and wrap the DBus update index."""
        if not isinstance(raw_index, (str, bytes, bytearray, int, float)):
            raise TypeError("UpdateIndex must be numeric")
        index = int(raw_index) + 1
        return 0 if index > 255 else index
