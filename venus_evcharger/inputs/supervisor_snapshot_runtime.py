# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
from pathlib import Path

from venus_evcharger.inputs.supervisor_contracts import (
    AutoInputSupervisorService,
    SnapshotPayload,
    SnapshotSchema,
)
from venus_evcharger.inputs.supervisor_snapshot_validation import AutoInputSnapshotValidator
from venus_evcharger.inputs.supervisor_snapshot_values import snapshot_int, snapshot_timestamp
from venus_evcharger.ipc.gateway_pressure import service_gateway_pressure_policy

SnapshotFileSignature = tuple[int, int, int, int]
ChangedSnapshot = tuple[int, SnapshotFileSignature, SnapshotPayload]
SnapshotFreshness = tuple[float | None, float | None, bool]


def _sequence_advances(
    sequence: int | None,
    previous_sequence: int | None,
    generation: int | None,
    previous_generation: int | None,
    runtime_instance: str,
    previous_runtime_instance: str | None,
) -> bool:
    """Return whether a sequence belongs to a new producer or advances in place."""
    if sequence is None:
        return False
    if previous_sequence is None:
        return True
    if runtime_instance != previous_runtime_instance:
        return True
    if generation != previous_generation:
        return True
    return sequence > previous_sequence


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

    def _snapshot_freshness(
        self,
        snapshot: SnapshotPayload,
        current_monotonic: float,
    ) -> tuple[float | None, float | None, bool]:
        svc = self._service
        captured_at = snapshot_timestamp(snapshot.get("captured_at"))
        freshness_monotonic = snapshot_timestamp(
            snapshot.get("heartbeat_monotonic")
        )
        snapshot_age = (
            None
            if freshness_monotonic is None
            else current_monotonic - freshness_monotonic
        )
        stale_after = service_gateway_pressure_policy(svc).liveness_timeout_seconds(
            svc.auto_input_helper_stale_seconds,
        )
        stale = snapshot_age is not None and snapshot_age > stale_after
        return captured_at, freshness_monotonic, stale

    def _empty_snapshot_fields(self) -> SnapshotPayload:
        fields: SnapshotPayload = {}
        for source_key in self._schema.source_keys:
            fields[f"{source_key}_captured_at"] = None
            fields[f"{source_key}_observed_monotonic"] = None
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = None
        return fields

    def _snapshot_value_fields(self, snapshot: SnapshotPayload) -> SnapshotPayload:
        fields: SnapshotPayload = {}
        for source_key in self._schema.source_keys:
            fields[f"{source_key}_captured_at"] = snapshot.get(f"{source_key}_captured_at")
            fields[f"{source_key}_observed_monotonic"] = snapshot.get(
                f"{source_key}_observed_monotonic"
            )
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = snapshot.get(value_key)
        return fields

    def _normalize_source_timestamps(self, fields: SnapshotPayload) -> SnapshotPayload:
        for source_key in self._schema.source_keys:
            for suffix in ("captured_at", "observed_monotonic"):
                timestamp_key = f"{source_key}_{suffix}"
                fields[timestamp_key] = snapshot_timestamp(fields[timestamp_key])
        return fields

    def _build_snapshot_fields(
        self,
        snapshot: SnapshotPayload,
        current_epoch: float,
        captured_at: float | None,
        stale: bool,
    ) -> SnapshotPayload:
        svc = self._service
        fields: SnapshotPayload = {
            "captured_at": captured_at if captured_at is not None else current_epoch,
            "captured_monotonic": snapshot_timestamp(snapshot.get("captured_monotonic")),
            "heartbeat_monotonic": snapshot_timestamp(snapshot.get("heartbeat_monotonic")),
            "auto_mode_active": svc.auto.mode_uses_auto_logic(svc.virtual_mode),
        }
        source_fields = self._empty_snapshot_fields() if stale else self._snapshot_value_fields(snapshot)
        fields.update(self._normalize_source_timestamps(source_fields))
        return fields

    def _apply_snapshot(
        self,
        mtime_ns: int | None,
        freshness_monotonic: float | None,
        fields: SnapshotPayload,
        seen_for_current_helper: bool,
        *,
        file_signature: SnapshotFileSignature | None = None,
    ) -> None:
        svc = self._service
        svc._auto_input_snapshot_mtime_ns = mtime_ns
        if file_signature is not None:
            self._last_snapshot_signature = file_signature
        self._apply_snapshot_last_seen(freshness_monotonic, seen_for_current_helper)
        svc._auto_input_snapshot_seen_for_current_helper = bool(seen_for_current_helper)
        svc._auto_input_snapshot_last_captured_at = snapshot_timestamp(fields.get("captured_at"))
        svc._auto_input_snapshot_last_sequence = snapshot_int(fields.get("snapshot_sequence"))
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
        freshness_monotonic: float | None,
        seen_for_current_helper: bool,
    ) -> None:
        svc = self._service
        if seen_for_current_helper:
            svc._auto_input_snapshot_last_seen = freshness_monotonic
            return
        if not svc._auto_input_snapshot_seen_for_current_helper:
            svc._auto_input_snapshot_last_seen = None

    def _snapshot_seen_for_current_helper(
        self,
        snapshot: SnapshotPayload,
        freshness_monotonic: float | None,
        stale: bool,
    ) -> bool:
        if stale or freshness_monotonic is None:
            return False
        return self._snapshot_matches_current_helper(snapshot, freshness_monotonic)

    def _snapshot_matches_current_helper(self, snapshot: SnapshotPayload, freshness_monotonic: float) -> bool:
        svc = self._service
        return (
            self._snapshot_after_current_helper_start(svc, freshness_monotonic)
            and self._snapshot_runtime_instance_matches_current_service(svc, snapshot)
            and self._snapshot_generation_matches_current_helper(svc, snapshot)
            and self._snapshot_pid_matches_current_helper(svc, snapshot)
        )

    @staticmethod
    def _snapshot_after_current_helper_start(svc: AutoInputSupervisorService, freshness_monotonic: float) -> bool:
        """Return whether snapshot freshness is newer than the helper start."""
        helper_start = float(svc._auto_input_helper_last_start_at)
        return helper_start <= 0.0 or freshness_monotonic >= helper_start

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
        freshness_monotonic: float | None,
        current_monotonic: float,
    ) -> bool:
        if freshness_monotonic is None:
            return True
        svc = self._service
        if freshness_monotonic <= current_monotonic + self._schema.future_timestamp_tolerance_seconds:
            return True
        svc.runtime.warning_throttled(
            "auto-input-helper-future-timestamp",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s moved monotonic freshness into the future: %.3f > %.3f",
            path,
            float(freshness_monotonic),
            float(current_monotonic),
        )
        return False

    def _snapshot_sequence_valid(self, path: str, snapshot: SnapshotPayload) -> bool:
        svc = self._service
        sequence = snapshot_int(snapshot.get("snapshot_sequence"))
        generation = snapshot_int(snapshot.get("helper_generation"))
        runtime_instance = str(snapshot.get("runtime_instance_id") or "").strip()
        previous_sequence = svc._auto_input_snapshot_last_sequence
        previous_generation = svc._auto_input_snapshot_generation
        previous_runtime = svc._auto_input_snapshot_runtime_instance_id
        if _sequence_advances(
            sequence,
            previous_sequence,
            generation,
            previous_generation,
            runtime_instance,
            previous_runtime,
        ):
            return True
        svc.runtime.warning_throttled(
            "auto-input-helper-sequence-regressed",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s has non-increasing sequence %s after %s",
            path,
            sequence,
            previous_sequence,
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

    def _load_changed_snapshot(self, path: str) -> ChangedSnapshot | None:
        """Return one validated snapshot only when its file identity changed."""
        metadata = self._snapshot_file_metadata(path)
        if metadata is None:
            return None
        if not self._snapshot_file_changed(path, metadata):
            return None
        snapshot = self._load_snapshot_dict(path)
        if snapshot is None:
            return None
        mtime_ns, file_signature = metadata
        return mtime_ns, file_signature, snapshot

    def _validated_snapshot_freshness(
        self,
        path: str,
        snapshot: SnapshotPayload,
        observed_monotonic: float,
    ) -> SnapshotFreshness | None:
        """Validate freshness and producer ordering as one acceptance decision."""
        freshness = self._snapshot_freshness(snapshot, observed_monotonic)
        _captured_at, freshness_monotonic, _stale = freshness
        if not self._snapshot_timestamps_valid(
            path,
            freshness_monotonic,
            observed_monotonic,
        ):
            return None
        if not self._snapshot_sequence_valid(path, snapshot):
            return None
        return freshness

    def _refresh_snapshot_payload(
        self,
        path: str,
        current_monotonic: float | None,
    ) -> tuple[int, float | None, bool, SnapshotPayload, SnapshotFileSignature] | None:
        svc = self._service
        changed = self._load_changed_snapshot(path)
        if changed is None:
            return None
        mtime_ns, file_signature, snapshot = changed
        observed_monotonic = (
            svc.monotonic_now()
            if current_monotonic is None
            else current_monotonic
        )
        freshness = self._validated_snapshot_freshness(
            path,
            snapshot,
            observed_monotonic,
        )
        if freshness is None:
            return None
        captured_at, freshness_monotonic, stale = freshness
        fields = self._build_snapshot_fields(snapshot, svc.time_now(), captured_at, stale)
        self._copy_snapshot_identity_fields(fields, snapshot)
        self._copy_snapshot_diagnostic_fields(fields, snapshot)
        seen_for_current_helper = self._snapshot_seen_for_current_helper(
            snapshot,
            freshness_monotonic,
            stale,
        )
        return mtime_ns, freshness_monotonic, seen_for_current_helper, fields, file_signature

    def _copy_snapshot_identity_fields(self, fields: SnapshotPayload, snapshot: SnapshotPayload) -> None:
        fields["snapshot_version"] = snapshot["snapshot_version"]
        fields["snapshot_sequence"] = snapshot_int(snapshot.get("snapshot_sequence"))
        fields["writer_pid"] = snapshot_int(snapshot.get("writer_pid"))
        fields["helper_generation"] = snapshot_int(snapshot.get("helper_generation"))
        raw_runtime_instance_id = snapshot.get("runtime_instance_id")
        fields["runtime_instance_id"] = "" if raw_runtime_instance_id is None else str(raw_runtime_instance_id)

    def _copy_snapshot_diagnostic_fields(self, fields: SnapshotPayload, snapshot: SnapshotPayload) -> None:
        for source_key in self._schema.source_keys:
            fields[f"{source_key}_status"] = snapshot.get(f"{source_key}_status")
        fields["helper_state"] = snapshot.get("helper_state")
        fields["helper_status"] = snapshot.get("helper_status")

    def refresh_snapshot(self, monotonic_at: float | None = None) -> None:
        svc = self._service
        svc.runtime.ensure_worker_state()
        current_monotonic = (
            None if monotonic_at is None else float(monotonic_at)
        )
        path = svc.auto_input_snapshot_path.strip()
        payload = self._refresh_snapshot_payload(path, current_monotonic)
        if payload is None:
            return
        mtime_ns, freshness_monotonic, seen_for_current_helper, fields, file_signature = payload
        self._apply_snapshot(
            mtime_ns,
            freshness_monotonic,
            fields,
            seen_for_current_helper,
            file_signature=file_signature,
        )
