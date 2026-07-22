# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
from pathlib import Path

from venus_evcharger.core.contracts import timestamp_not_future
from venus_evcharger.inputs.supervisor_contracts import (
    AutoInputSupervisorService,
    SnapshotPayload,
    SnapshotSchema,
)
from venus_evcharger.inputs.supervisor_snapshot_validation import AutoInputSnapshotValidator
from venus_evcharger.inputs.supervisor_snapshot_values import snapshot_int, snapshot_timestamp
from venus_evcharger.ipc.gateway_pressure import service_gateway_pressure_policy

SnapshotFileSignature = tuple[int, int, int, int]


class AutoInputSnapshotRuntime:
    """Load, freshness-check, and apply validated helper snapshots."""

    def __init__(
        self,
        service: AutoInputSupervisorService,
        schema: SnapshotSchema,
        validator: AutoInputSnapshotValidator,
    ) -> None:
        self._service = service
        self._schema = schema
        self._validator = validator
        self._last_snapshot_signature: SnapshotFileSignature | None = None

    @staticmethod
    def _snapshot_file_metadata(path: str) -> tuple[int, SnapshotFileSignature] | None:
        try:
            stat_result = os.stat(path)
        except OSError:
            return None
        mtime_ns = int(stat_result.st_mtime_ns)
        signature = (
            mtime_ns,
            int(getattr(stat_result, "st_ino", 0)),
            int(getattr(stat_result, "st_size", 0)),
            int(getattr(stat_result, "st_ctime_ns", mtime_ns)),
        )
        return mtime_ns, signature

    def _load_snapshot_dict(self, path: str) -> SnapshotPayload | None:
        svc = self._service
        try:
            loaded: object = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as error:
            svc.runtime.warning_throttled(
                "auto-input-helper-read-failed",
                max(1.0, svc.auto_input_helper_restart_seconds),
                "Unable to read auto input helper snapshot %s: %s",
                path,
                error,
                exc_info=error,
            )
            return None
        return self._validator.validate(path, loaded)

    def _snapshot_freshness(self, snapshot: SnapshotPayload, current: float) -> tuple[float | None, float | None, bool]:
        svc = self._service
        captured_at = snapshot_timestamp(snapshot.get("captured_at"))
        heartbeat_at = snapshot_timestamp(snapshot.get("heartbeat_at"))
        freshness_timestamp = heartbeat_at if heartbeat_at is not None else captured_at
        snapshot_age = None if freshness_timestamp is None else max(0.0, current - freshness_timestamp)
        stale_after = service_gateway_pressure_policy(svc).liveness_timeout_seconds(
            svc.auto_input_helper_stale_seconds,
        )
        stale = snapshot_age is not None and snapshot_age > stale_after
        return captured_at, freshness_timestamp, stale

    def _empty_snapshot_fields(self) -> SnapshotPayload:
        fields: SnapshotPayload = {}
        for source_key in self._schema.source_keys:
            fields[f"{source_key}_captured_at"] = None
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = None
        return fields

    def _snapshot_value_fields(self, snapshot: SnapshotPayload) -> SnapshotPayload:
        fields: SnapshotPayload = {}
        for source_key in self._schema.source_keys:
            fields[f"{source_key}_captured_at"] = snapshot.get(f"{source_key}_captured_at")
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = snapshot.get(value_key)
        return fields

    def _normalize_source_timestamps(self, fields: SnapshotPayload) -> SnapshotPayload:
        for source_key in self._schema.source_keys:
            timestamp_key = f"{source_key}_captured_at"
            fields[timestamp_key] = snapshot_timestamp(fields[timestamp_key])
        return fields

    def _build_snapshot_fields(
        self,
        snapshot: SnapshotPayload,
        current: float,
        captured_at: float | None,
        stale: bool,
    ) -> SnapshotPayload:
        svc = self._service
        fields: SnapshotPayload = {
            "captured_at": captured_at if captured_at is not None else current,
            "auto_mode_active": svc.auto.mode_uses_auto_logic(svc.virtual_mode),
        }
        source_fields = self._empty_snapshot_fields() if stale else self._snapshot_value_fields(snapshot)
        fields.update(self._normalize_source_timestamps(source_fields))
        return fields

    def _apply_snapshot(
        self,
        mtime_ns: int | None,
        freshness_timestamp: float | None,
        current: float,
        fields: SnapshotPayload,
        seen_for_current_helper: bool,
        *,
        file_signature: SnapshotFileSignature | None = None,
    ) -> None:
        svc = self._service
        svc._auto_input_snapshot_mtime_ns = mtime_ns
        if file_signature is not None:
            self._last_snapshot_signature = file_signature
        self._apply_snapshot_last_seen(freshness_timestamp, seen_for_current_helper)
        svc._auto_input_snapshot_seen_for_current_helper = bool(seen_for_current_helper)
        svc._auto_input_snapshot_last_captured_at = snapshot_timestamp(fields.get("captured_at"))
        svc._auto_input_snapshot_version = snapshot_int(fields.get("snapshot_version"))
        svc._auto_input_snapshot_writer_pid = snapshot_int(fields.get("writer_pid"))
        svc._auto_input_snapshot_generation = snapshot_int(fields.get("helper_generation"))
        runtime_instance_id = fields.get("runtime_instance_id")
        svc._auto_input_snapshot_runtime_instance_id = (
            str(runtime_instance_id) if runtime_instance_id is not None else None
        )
        svc.runtime.update_worker_snapshot(**fields)

    def _apply_snapshot_last_seen(
        self,
        freshness_timestamp: float | None,
        seen_for_current_helper: bool,
    ) -> None:
        svc = self._service
        if seen_for_current_helper:
            svc._auto_input_snapshot_last_seen = freshness_timestamp
            return
        if not svc._auto_input_snapshot_seen_for_current_helper:
            svc._auto_input_snapshot_last_seen = None

    def _snapshot_seen_for_current_helper(
        self,
        snapshot: SnapshotPayload,
        freshness_timestamp: float | None,
        stale: bool,
    ) -> bool:
        if stale or freshness_timestamp is None:
            return False
        return self._snapshot_matches_current_helper(snapshot, freshness_timestamp)

    def _snapshot_matches_current_helper(self, snapshot: SnapshotPayload, freshness_timestamp: float) -> bool:
        svc = self._service
        return (
            self._snapshot_after_current_helper_start(svc, freshness_timestamp)
            and self._snapshot_runtime_instance_matches_current_service(svc, snapshot)
            and self._snapshot_generation_matches_current_helper(svc, snapshot)
            and self._snapshot_pid_matches_current_helper(svc, snapshot)
        )

    @staticmethod
    def _snapshot_after_current_helper_start(svc: AutoInputSupervisorService, freshness_timestamp: float) -> bool:
        """Return whether snapshot freshness is newer than the helper start."""
        helper_start = float(svc._auto_input_helper_last_start_at)
        return helper_start <= 0.0 or freshness_timestamp >= helper_start

    def _snapshot_generation_matches_current_helper(
        self,
        svc: AutoInputSupervisorService,
        snapshot: SnapshotPayload,
    ) -> bool:
        """Return whether snapshot generation matches the current helper."""
        expected = snapshot_int(svc._auto_input_helper_generation)
        if expected is None or expected <= 0:
            return True
        return bool(snapshot_int(snapshot.get("helper_generation")) == expected)

    @staticmethod
    def _snapshot_runtime_instance_matches_current_service(
        svc: AutoInputSupervisorService,
        snapshot: SnapshotPayload,
    ) -> bool:
        expected = svc._auto_input_runtime_instance_id.strip()
        raw_actual = snapshot.get("runtime_instance_id")
        actual = "" if raw_actual is None else str(raw_actual).strip()
        return bool(expected and actual == expected)

    def _snapshot_pid_matches_current_helper(self, svc: AutoInputSupervisorService, snapshot: SnapshotPayload) -> bool:
        """Return whether snapshot writer pid matches the current helper process."""
        process = svc._auto_input_helper_process
        expected = snapshot_int(None if process is None else process.pid)
        snapshot_pid = snapshot_int(snapshot.get("writer_pid"))
        return expected is None or snapshot_pid is None or snapshot_pid == expected

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
        svc = self._service
        last_captured_at = svc._auto_input_snapshot_last_captured_at
        if captured_at is None or last_captured_at is None or float(captured_at) >= float(last_captured_at):
            return True
        svc.runtime.warning_throttled(
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
        svc = self._service
        if timestamp_not_future(freshness_timestamp, current, self._schema.future_timestamp_tolerance_seconds):
            return True
        svc.runtime.warning_throttled(
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

    def _snapshot_file_changed(self, path: str, metadata: tuple[int, SnapshotFileSignature]) -> bool:
        mtime_ns, file_signature = metadata
        if self._snapshot_path_changed(path, mtime_ns, self._service._auto_input_snapshot_mtime_ns):
            return True
        return self._last_snapshot_signature is not None and self._last_snapshot_signature != file_signature

    def _refresh_snapshot_payload(
        self,
        path: str,
        current: float,
    ) -> tuple[int, float | None, bool, SnapshotPayload, SnapshotFileSignature] | None:
        svc = self._service
        metadata = self._snapshot_file_metadata(path)
        if metadata is None or not self._snapshot_file_changed(path, metadata):
            return None
        mtime_ns, file_signature = metadata
        snapshot = self._load_snapshot_dict(path)
        if snapshot is None:
            return None
        captured_at, freshness_timestamp, stale = self._snapshot_freshness(snapshot, current)
        if not self._snapshot_timestamps_valid(path, captured_at, freshness_timestamp, current):
            return None
        fields = self._build_snapshot_fields(snapshot, current, captured_at, stale)
        self._copy_snapshot_identity_fields(fields, snapshot)
        self._copy_snapshot_diagnostic_fields(fields, snapshot)
        seen_for_current_helper = self._snapshot_seen_for_current_helper(snapshot, freshness_timestamp, stale)
        return mtime_ns, freshness_timestamp, seen_for_current_helper, fields, file_signature

    def _copy_snapshot_identity_fields(self, fields: SnapshotPayload, snapshot: SnapshotPayload) -> None:
        fields["snapshot_version"] = snapshot["snapshot_version"]
        fields["writer_pid"] = snapshot_int(snapshot.get("writer_pid"))
        fields["helper_generation"] = snapshot_int(snapshot.get("helper_generation"))
        raw_runtime_instance_id = snapshot.get("runtime_instance_id")
        fields["runtime_instance_id"] = "" if raw_runtime_instance_id is None else str(raw_runtime_instance_id)

    def _copy_snapshot_diagnostic_fields(self, fields: SnapshotPayload, snapshot: SnapshotPayload) -> None:
        for source_key in self._schema.source_keys:
            fields[f"{source_key}_status"] = snapshot.get(f"{source_key}_status")
        fields["helper_state"] = snapshot.get("helper_state")
        fields["helper_status"] = snapshot.get("helper_status")

    def refresh_snapshot(self, now: float | None = None) -> None:
        svc = self._service
        svc.runtime.ensure_worker_state()
        current = svc.time_now() if now is None else float(now)
        path = svc.auto_input_snapshot_path.strip()
        payload = self._refresh_snapshot_payload(path, current)
        if payload is None:
            return
        mtime_ns, freshness_timestamp, seen_for_current_helper, fields, file_signature = payload
        self._apply_snapshot(
            mtime_ns,
            freshness_timestamp,
            current,
            fields,
            seen_for_current_helper,
            file_signature=file_signature,
        )
