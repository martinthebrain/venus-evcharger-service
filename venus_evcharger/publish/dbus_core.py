# SPDX-License-Identifier: GPL-3.0-or-later
"""Core DBus publish and transactional write helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from venus_evcharger.core.dbus_backpressure import service_dbus_backpressure_policy
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH, evcs_fields_to_paths
from venus_evcharger.publish.dbus_shared import PublishServiceValueSnapshot, PublishStateEntry, PhaseData

PUBLISH_DBUS_SERVICE_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


def _publish_state_entry(value: object) -> PublishStateEntry | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


class _DbusPublishCore:
    PHASE_NAMES: tuple[str, str, str]
    service: Any

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
        return service_dbus_backpressure_policy(self.service).publish_interval_seconds(
            float(interval_seconds),
            group=group_name,
        )

    def _should_enqueue_publish(self) -> bool:
        """Return whether DBus writes must be handed to the GLib thread."""
        direct_allowed = getattr(self.service, "_dbus_publish_direct_allowed", None)
        enqueue = getattr(self.service, "_enqueue_dbus_publish_values", None)
        enqueue_fields = getattr(self.service, "_enqueue_dbus_publish_fields", None)
        return callable(direct_allowed) and (callable(enqueue) or callable(enqueue_fields)) and not bool(direct_allowed())

    def _enqueue_publish_values(self, staged_values: Sequence[tuple[str, Any]], current: float) -> bool:
        enqueue = getattr(self.service, "_enqueue_dbus_publish_values", None)
        if not callable(enqueue):
            return False
        return bool(enqueue(list(staged_values), current))

    def _enqueue_publish_fields(self, staged_fields: Sequence[tuple[str, Any]], current: float) -> bool:
        enqueue = getattr(self.service, "_enqueue_dbus_publish_fields", None)
        if callable(enqueue):
            return bool(enqueue(list(staged_fields), current))
        return self._enqueue_publish_values(
            self._field_items_to_path_items(staged_fields),
            current,
        )

    def _assert_dbus_access_allowed(self, operation: str) -> None:
        assert_allowed = getattr(self.service, "_assert_dbus_mainloop_thread", None)
        if callable(assert_allowed):
            assert_allowed(operation)

    def publish_path(
        self,
        path: str,
        value: Any,
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
        value: Any,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish one semantic EVCS field immediately, on change, or by interval."""
        return self._publish_fields_transactional(
            "single-field",
            {str(field): value},
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def _publish_decision(
        self,
        path: str,
        value: Any,
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
    def _publish_state_fields(entry: PublishStateEntry) -> tuple[Any, Any]:
        """Return the stored publish-state value and timestamp."""
        return entry.get("value"), entry.get("updated_at")

    @staticmethod
    def _publish_interval_elapsed(last_updated_at: Any, current: float, interval_seconds: float) -> bool:
        """Return whether the publish interval is due for one path."""
        if last_updated_at is None:
            return True
        return (current - float(last_updated_at)) >= float(interval_seconds)

    def _publish_group_failure(self, group_name: str, failed_paths: Sequence[str], current: float) -> None:
        """Record one DBus publish-group failure without raising into the caller."""
        mark_failure = getattr(self.service, "_mark_failure", None)
        if callable(mark_failure):
            mark_failure("dbus")
        warning_throttled = getattr(self.service, "_warning_throttled", None)
        if callable(warning_throttled):
            warning_throttled(
                f"dbus-publish-{group_name}-failed",
                1.0,
                "DBus publish group %s failed for paths %s",
                group_name,
                ",".join(failed_paths),
            )
        else:
            # Fallback for narrow unit-test doubles that only expose the publisher.
            logging.warning(
                "DBus publish group %s failed for paths %s at %.3f",
                group_name,
                ",".join(failed_paths),
                current,
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
        values: Mapping[str, Any],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> tuple[list[tuple[str, Any]], dict[str, PublishStateEntry | None], dict[str, PublishServiceValueSnapshot]]:
        """Collect the DBus values that should be written in one transactional batch."""
        staged_values: list[tuple[str, Any]] = []
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
        staged_values: Sequence[tuple[str, Any]],
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
        values: Mapping[str, Any],
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
        self._publish_group_failure(group_name, [failed_path], current)
        return False

    def _publish_fields_transactional(
        self,
        group_name: str,
        fields: Mapping[str, Any],
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
        values: Mapping[str, Any],
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
        values: Mapping[str, Any],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> list[tuple[str, Any]]:
        """Return only values that should be written by the publish queue."""
        staged_values: list[tuple[str, Any]] = []
        for path, value in values.items():
            should_write, _entry = self._publish_decision(path, value, current, interval_seconds, force)
            if should_write:
                staged_values.append((path, value))
        return staged_values

    def _staged_fields_for_enqueue(
        self,
        fields: Mapping[str, Any],
        paths: Mapping[str, Any],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> list[tuple[str, Any]]:
        """Return semantic fields whose mapped DBus path should be written."""
        staged_fields: list[tuple[str, Any]] = []
        for field, value in fields.items():
            path = EVCS_FIELD_TO_PATH.get(str(field), "")
            if not path:
                continue
            should_write, _entry = self._publish_decision(path, paths[path], current, interval_seconds, force)
            if should_write:
                staged_fields.append((str(field), value))
        return staged_fields

    @staticmethod
    def _field_items_to_path_items(fields: Sequence[tuple[str, Any]]) -> list[tuple[str, Any]]:
        """Map semantic field tuples to DBus path tuples for legacy direct queues."""
        return [
            (path, value)
            for field, value in fields
            for path in [EVCS_FIELD_TO_PATH.get(str(field), "")]
            if path
        ]

    def _publish_values(
        self,
        values: Mapping[str, Any],
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
        if self._should_enqueue_publish():
            enqueue_bump = getattr(self.service, "_enqueue_dbus_update_index_bump", None)
            if callable(enqueue_bump):
                enqueue_bump(current)
                return
        update_index_path = EVCS_FIELD_TO_PATH["update_index"]
        self._assert_dbus_access_allowed(f"bump {update_index_path}")
        index = int(self.service._dbusservice[update_index_path]) + 1
        next_index = 0 if index > 255 else index
        self.service._dbusservice[update_index_path] = next_index
        self.service._dbus_publish_state[update_index_path] = {"value": next_index, "updated_at": current}

    def _live_measurement_fields(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
    ) -> dict[str, float]:
        """Return fast-moving AC measurement values keyed by semantic EVCS field."""
        values: dict[str, float] = {
            "ac_power_w": power,
            "ac_voltage_v": voltage,
            "ac_current_a": total_current,
            "charge_current_a": total_current,
        }
        for phase_name in self.PHASE_NAMES:
            normalized = phase_name.lower()
            values[f"{normalized}_power_w"] = phase_data[phase_name]["power"]
            values[f"{normalized}_current_a"] = phase_data[phase_name]["current"]
            values[f"{normalized}_voltage_v"] = phase_data[phase_name]["voltage"]
        return values

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
        now: float | None,
    ) -> bool:
        """Publish fast-changing AC measurements once per second."""
        self.ensure_state()
        return self._publish_fields_transactional(
            "live-measurements",
            self._live_measurement_fields(power, voltage, total_current, phase_data),
            now,
            interval_seconds=self.service._dbus_live_publish_interval_seconds,
        )

    def _energy_time_fields(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
    ) -> dict[str, float | int]:
        """Return slower-moving energy and time values keyed by semantic EVCS field."""
        return {
            "energy_forward_kwh": energy_forward,
            "l1_energy_forward_kwh": phase_energies["L1"],
            "l2_energy_forward_kwh": phase_energies["L2"],
            "l3_energy_forward_kwh": phase_energies["L3"],
            "charging_time_s": charging_time,
            "session_energy_kwh": session_energy,
            "session_time_s": charging_time,
        }

    def publish_energy_time_measurements(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        """Publish energy and time related values at most every five seconds."""
        self.ensure_state()
        return self._publish_fields_transactional(
            "energy-time",
            self._energy_time_fields(energy_forward, phase_energies, charging_time, session_energy),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
