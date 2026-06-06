# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
# pyright: reportAttributeAccessIssue=false
from __future__ import annotations

import os
from typing import Any, cast

from venus_evcharger.core.contracts import timestamp_not_future


class _AutoInputSupervisorSnapshotRuntimeMixin:
    def _snapshot_mtime_ns(self, path: str) -> int | None:
        svc = self.service
        try:
            stat_path = getattr(svc, "_stat_path", os.stat)
            stat_result = stat_path(path)
        except (AttributeError, OSError):
            return None
        return getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))

    def _load_snapshot_dict(self, path: str) -> dict[str, Any] | None:
        svc = self.service
        try:
            snapshot = svc._load_json_file(path)
        except Exception as error:  # pylint: disable=broad-except
            svc._warning_throttled(
                "auto-input-helper-read-failed",
                max(1.0, svc.auto_input_helper_restart_seconds),
                "Unable to read auto input helper snapshot %s: %s",
                path,
                error,
                exc_info=error,
            )
            return None
        return cast(dict[str, Any] | None, self._validate_snapshot_dict(path, snapshot))

    def _snapshot_freshness(self, snapshot: dict[str, Any], current: float) -> tuple[float | None, float | None, bool]:
        svc = self.service
        captured_at = self._coerce_snapshot_timestamp(snapshot.get("captured_at"))
        heartbeat_at = self._coerce_snapshot_timestamp(snapshot.get("heartbeat_at"))
        freshness_timestamp = heartbeat_at if heartbeat_at is not None else captured_at
        snapshot_age = None if freshness_timestamp is None else max(0.0, current - freshness_timestamp)
        stale = snapshot_age is not None and snapshot_age > svc.auto_input_helper_stale_seconds
        return captured_at, freshness_timestamp, stale

    @classmethod
    def _empty_snapshot_fields(cls) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            fields[f"{source_key}_captured_at"] = None
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = None
        return fields

    @classmethod
    def _snapshot_value_fields(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            fields[f"{source_key}_captured_at"] = snapshot.get(f"{source_key}_captured_at")
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = snapshot.get(value_key)
        return fields

    @classmethod
    def _normalize_source_timestamps(cls, fields: dict[str, Any]) -> dict[str, Any]:
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            timestamp_key = f"{source_key}_captured_at"
            fields[timestamp_key] = cls._coerce_snapshot_timestamp(fields[timestamp_key])
        return fields

    def _build_snapshot_fields(
        self,
        snapshot: dict[str, Any],
        current: float,
        captured_at: float | None,
        stale: bool,
    ) -> dict[str, Any]:
        svc = self.service
        fields = {
            "captured_at": captured_at if captured_at is not None else current,
            "auto_mode_active": svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
        }
        source_fields = self._empty_snapshot_fields() if stale else self._snapshot_value_fields(snapshot)
        fields.update(self._normalize_source_timestamps(source_fields))
        return fields

    def _apply_snapshot(
        self,
        mtime_ns: int | None,
        freshness_timestamp: float | None,
        current: float,
        fields: dict[str, Any],
        seen_for_current_helper: bool,
    ) -> None:
        svc = self.service
        svc._auto_input_snapshot_mtime_ns = mtime_ns
        if seen_for_current_helper:
            svc._auto_input_snapshot_last_seen = freshness_timestamp if freshness_timestamp is not None else current
        elif not getattr(svc, "_auto_input_snapshot_seen_for_current_helper", False):
            svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_seen_for_current_helper = bool(seen_for_current_helper)
        svc._auto_input_snapshot_last_captured_at = fields.get("captured_at")
        svc._auto_input_snapshot_version = fields.get("snapshot_version")
        svc._auto_input_snapshot_writer_pid = fields.get("writer_pid")
        svc._auto_input_snapshot_generation = fields.get("helper_generation")
        svc._update_worker_snapshot(**fields)

    def _snapshot_seen_for_current_helper(
        self,
        snapshot: dict[str, Any],
        freshness_timestamp: float | None,
        stale: bool,
    ) -> bool:
        if stale or freshness_timestamp is None:
            return False
        return self._snapshot_matches_current_helper(snapshot, freshness_timestamp)

    def _snapshot_matches_current_helper(self, snapshot: dict[str, Any], freshness_timestamp: float) -> bool:
        svc = self.service
        helper_start = float(getattr(svc, "_auto_input_helper_last_start_at", 0.0) or 0.0)
        if helper_start > 0.0 and freshness_timestamp < helper_start:
            return False

        expected_generation = self._coerce_snapshot_int(getattr(svc, "_auto_input_helper_generation", None))
        snapshot_generation = self._coerce_snapshot_int(snapshot.get("helper_generation"))
        if expected_generation is not None and expected_generation > 0:
            if snapshot_generation != expected_generation:
                return False

        process = getattr(svc, "_auto_input_helper_process", None)
        expected_pid = self._coerce_snapshot_int(getattr(process, "pid", None))
        snapshot_pid = self._coerce_snapshot_int(snapshot.get("writer_pid"))
        if expected_pid is not None and snapshot_pid is not None and snapshot_pid != expected_pid:
            return False
        return True

    def _snapshot_timestamps_valid(
        self,
        path: str,
        captured_at: float | None,
        freshness_timestamp: float | None,
        current: float,
    ) -> bool:
        return self._snapshot_captured_at_monotonic(path, captured_at) and self._snapshot_freshness_not_future(
            path,
            freshness_timestamp,
            current,
        )

    def _snapshot_captured_at_monotonic(self, path: str, captured_at: float | None) -> bool:
        svc = self.service
        last_captured_at = getattr(svc, "_auto_input_snapshot_last_captured_at", None)
        if captured_at is None or last_captured_at is None or float(captured_at) >= float(last_captured_at):
            return True
        svc._warning_throttled(
            "auto-input-helper-captured-at-regressed",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s moved captured_at backwards from %.3f to %.3f",
            path,
            float(last_captured_at),
            float(captured_at),
        )
        return False

    def _snapshot_freshness_not_future(self, path: str, freshness_timestamp: float | None, current: float) -> bool:
        if freshness_timestamp is None:
            return True
        svc = self.service
        if timestamp_not_future(freshness_timestamp, current, self.FUTURE_TIMESTAMP_TOLERANCE_SECONDS):
            return True
        svc._warning_throttled(
            "auto-input-helper-future-timestamp",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s moved freshness timestamp into the future: %.3f > %.3f",
            path,
            float(freshness_timestamp),
            float(current),
        )
        return False

    @staticmethod
    def _snapshot_path_changed(path: str, mtime_ns: int | None, previous_mtime_ns: int | None) -> bool:
        return bool(path) and mtime_ns is not None and previous_mtime_ns != mtime_ns

    def _refresh_snapshot_payload(
        self,
        path: str,
        current: float,
    ) -> tuple[int | None, float | None, bool, dict[str, Any]] | None:
        svc = self.service
        mtime_ns = self._snapshot_mtime_ns(path)
        if not self._snapshot_path_changed(path, mtime_ns, svc._auto_input_snapshot_mtime_ns):
            return None
        snapshot = self._load_snapshot_dict(path)
        if snapshot is None:
            return None
        captured_at, freshness_timestamp, stale = self._snapshot_freshness(snapshot, current)
        if not self._snapshot_timestamps_valid(path, captured_at, freshness_timestamp, current):
            return None
        fields = self._build_snapshot_fields(snapshot, current, captured_at, stale)
        fields["snapshot_version"] = snapshot["snapshot_version"]
        fields["writer_pid"] = self._coerce_snapshot_int(snapshot.get("writer_pid"))
        fields["helper_generation"] = self._coerce_snapshot_int(snapshot.get("helper_generation"))
        seen_for_current_helper = self._snapshot_seen_for_current_helper(snapshot, freshness_timestamp, stale)
        return mtime_ns, freshness_timestamp, seen_for_current_helper, fields

    def refresh_snapshot(self, now: float | None = None) -> None:
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        path = getattr(svc, "auto_input_snapshot_path", "").strip()
        payload = self._refresh_snapshot_payload(path, current)
        if payload is None:
            return
        mtime_ns, freshness_timestamp, seen_for_current_helper, fields = payload
        self._apply_snapshot(mtime_ns, freshness_timestamp, current, fields, seen_for_current_helper)
