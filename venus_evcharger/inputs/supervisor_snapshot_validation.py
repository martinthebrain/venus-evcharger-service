# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema and semantic validation for auto-input helper snapshots."""

from __future__ import annotations

from collections.abc import Callable
from venus_evcharger.core.contracts import paired_optional_values, valid_battery_soc
from venus_evcharger.inputs.supervisor_contracts import (
    AutoInputSupervisorService,
    SnapshotPayload,
    SnapshotSchema,
)
from venus_evcharger.inputs.supervisor_snapshot_values import (
    copied_object_mapping,
    is_object_list,
    snapshot_int,
    snapshot_number,
    snapshot_payload,
    snapshot_timestamp,
)

OPTIONAL_NUMERIC_FIELDS = (
    "battery_combined_soc",
    "battery_combined_usable_capacity_wh",
    "battery_combined_charge_power_w",
    "battery_combined_discharge_power_w",
    "battery_combined_net_power_w",
    "battery_combined_ac_power_w",
    "battery_headroom_charge_w",
    "battery_headroom_discharge_w",
    "expected_near_term_export_w",
    "expected_near_term_import_w",
    "battery_discharge_balance_error_w",
    "battery_discharge_balance_max_abs_error_w",
    "battery_discharge_balance_total_discharge_w",
    "grid_gateway_power",
    "grid_gateway_captured_at",
    "grid_primary_power",
    "grid_primary_captured_at",
    "grid_fusion_confidence",
    "grid_fusion_primary_age_seconds",
    "grid_fusion_backup_age_seconds",
    "grid_fusion_difference_watts",
    "grid_fusion_tolerance_watts",
)
OPTIONAL_COUNT_FIELDS = (
    "battery_source_count",
    "battery_online_source_count",
    "battery_valid_soc_source_count",
    "battery_discharge_balance_eligible_source_count",
    "battery_discharge_balance_active_source_count",
    "battery_discharge_balance_control_candidate_count",
    "battery_discharge_balance_control_ready_count",
    "battery_discharge_balance_supported_control_source_count",
    "battery_discharge_balance_experimental_control_source_count",
    "grid_fusion_primary_invalid_samples",
    "grid_fusion_primary_recovery_samples",
    "grid_fusion_mismatch_samples",
)


class AutoInputSnapshotValidator:
    """Validate and normalize one helper snapshot against its schema."""

    def __init__(self, service: AutoInputSupervisorService, schema: SnapshotSchema) -> None:
        self._service = service
        self._schema = schema

    def _invalid(self, warning_key: str, path: str, message: str, *args: object) -> None:
        svc = self._service
        svc.runtime.warning_throttled(
            warning_key,
            max(1.0, svc.auto_input_helper_restart_seconds),
            message,
            path,
            *args,
        )

    def _shape(self, path: str, raw_snapshot: object) -> tuple[SnapshotPayload, int] | None:
        payload = snapshot_payload(raw_snapshot)
        if payload is None:
            self._invalid("auto-input-helper-invalid", path, "Auto input helper snapshot %s is not a JSON object")
            return None
        missing_keys = sorted(self._schema.required_keys.difference(payload))
        if missing_keys:
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s is missing required keys: %s",
                ", ".join(missing_keys),
            )
            return None
        version = snapshot_int(payload.get("snapshot_version"))
        if version is None or version != self._schema.version:
            self._invalid(
                "auto-input-helper-version-invalid",
                path,
                "Auto input helper snapshot %s has unsupported snapshot_version=%s",
                payload.get("snapshot_version"),
            )
            return None
        return payload, version

    def _normalize_fields(
        self,
        path: str,
        source: SnapshotPayload,
        target: SnapshotPayload,
        keys: tuple[str, ...],
        normalizer: Callable[[object], float | None],
        label: str,
    ) -> bool:
        for key in keys:
            raw_value = source.get(key)
            value = normalizer(raw_value)
            if raw_value is not None and value is None:
                self._invalid(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has invalid %s field %s=%r",
                    label,
                    key,
                    raw_value,
                )
                return False
            target[key] = value
        return True

    def _normalize_scalars(self, path: str, source: SnapshotPayload, target: SnapshotPayload) -> bool:
        timestamp_keys = (
            "captured_at",
            "captured_monotonic",
            "heartbeat_at",
            "heartbeat_monotonic",
            "pv_captured_at",
            "pv_observed_monotonic",
            "battery_captured_at",
            "battery_observed_monotonic",
            "grid_captured_at",
            "grid_observed_monotonic",
        )
        if not self._normalize_fields(path, source, target, timestamp_keys, snapshot_timestamp, "timestamp"):
            return False
        if not self._normalize_fields(
            path,
            source,
            target,
            ("pv_power", "battery_soc", "grid_power"),
            snapshot_number,
            "numeric",
        ):
            return False
        return self._normalize_fields(
            path,
            source,
            target,
            OPTIONAL_NUMERIC_FIELDS,
            snapshot_number,
            "numeric",
        )

    def _normalize_identity(self, path: str, source: SnapshotPayload, target: SnapshotPayload) -> bool:
        writer_pid = self._writer_pid(path, source.get("writer_pid"))
        if writer_pid is None:
            return False
        generation = self._helper_generation(path, source.get("helper_generation"))
        if generation is None:
            return False
        runtime_instance = self._runtime_instance(path, source.get("runtime_instance_id"))
        if runtime_instance is None:
            return False
        sequence = self._snapshot_sequence(path, source.get("snapshot_sequence"))
        if sequence is None:
            return False
        target.update(
            writer_pid=writer_pid,
            helper_generation=generation,
            runtime_instance_id=runtime_instance,
            snapshot_sequence=sequence,
        )
        return True

    def _snapshot_sequence(self, path: str, raw_value: object) -> int | None:
        sequence = snapshot_int(raw_value)
        if sequence is None or sequence <= 0:
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires positive integer snapshot_sequence field",
            )
            return None
        return sequence

    def _writer_pid(self, path: str, raw_value: object) -> int | None:
        writer_pid = snapshot_int(raw_value)
        if writer_pid is None or writer_pid <= 0:
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires positive integer writer_pid field",
            )
            return None
        return writer_pid

    def _helper_generation(self, path: str, raw_value: object) -> int | None:
        generation = snapshot_int(raw_value)
        if generation is None or generation < 0:
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires non-negative integer helper_generation field",
            )
            return None
        return generation

    def _runtime_instance(self, path: str, raw_value: object) -> str | None:
        runtime_instance = raw_value
        if not isinstance(runtime_instance, str) or not runtime_instance.strip():
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires non-empty runtime_instance_id field",
            )
            return None
        return runtime_instance.strip()

    def _normalize_count(self, path: str, key: str, raw_value: object) -> int | None:
        if raw_value is None:
            return 0
        if isinstance(raw_value, bool):
            self._invalid_count(path, key, raw_value)
            return None
        value = snapshot_int(raw_value)
        if value is None:
            self._invalid_count(path, key, raw_value)
            return None
        return max(0, value)

    def _invalid_count(self, path: str, key: str, raw_value: object) -> None:
        self._invalid(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has invalid count field %s=%r",
            key,
            raw_value,
        )

    def _normalize_counts(self, path: str, source: SnapshotPayload, target: SnapshotPayload) -> bool:
        for key in OPTIONAL_COUNT_FIELDS:
            value = self._normalize_count(path, key, source.get(key))
            if value is None:
                return False
            target[key] = value
        return True

    def _normalize_battery_sources(self, path: str, value: object) -> list[object] | None:
        if value is None:
            return []
        if not is_object_list(value):
            self._invalid_structured(path, "battery_sources")
            return None
        return [self._copy_mapping_or_value(item) for item in value]

    def _normalize_learning_profiles(self, path: str, value: object) -> SnapshotPayload | None:
        if value is None:
            return {}
        payload = copied_object_mapping(value)
        if payload is None:
            self._invalid_structured(path, "battery_learning_profiles")
            return None
        return {key: self._copy_mapping_or_value(item) for key, item in payload.items()}

    @staticmethod
    def _copy_mapping_or_value(value: object) -> object:
        copied = copied_object_mapping(value)
        return value if copied is None else copied

    def _invalid_structured(self, path: str, field_name: str) -> None:
        self._invalid(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has invalid %s payload",
            field_name,
        )

    def _normalize_structured(self, path: str, source: SnapshotPayload, target: SnapshotPayload) -> bool:
        battery_sources = self._normalize_battery_sources(path, source.get("battery_sources"))
        if battery_sources is None:
            return False
        learning_profiles = self._normalize_learning_profiles(path, source.get("battery_learning_profiles"))
        if learning_profiles is None:
            return False
        target["battery_sources"] = battery_sources
        target["battery_learning_profiles"] = learning_profiles
        return True

    def _normalize(self, path: str, source: SnapshotPayload, version: int) -> SnapshotPayload | None:
        target = dict(source)
        target["snapshot_version"] = version
        if not self._normalize_identity(path, source, target):
            return None
        if not self._normalize_scalars(path, source, target):
            return None
        if not self._normalize_counts(path, source, target):
            return None
        return target if self._normalize_structured(path, source, target) else None

    def _timestamps_ordered(self, path: str, snapshot: SnapshotPayload) -> bool:
        captured_monotonic = snapshot_timestamp(snapshot.get("captured_monotonic"))
        heartbeat_monotonic = snapshot_timestamp(snapshot.get("heartbeat_monotonic"))
        if (
            captured_monotonic is not None
            and captured_monotonic >= 0.0
            and heartbeat_monotonic is not None
            and heartbeat_monotonic >= captured_monotonic
        ):
            return True
        self._invalid(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s requires ordered non-negative monotonic timestamps",
        )
        return False

    def _source_timestamp_valid(self, source_key: str, snapshot: SnapshotPayload) -> bool:
        timestamp = snapshot_timestamp(
            snapshot.get(f"{source_key}_observed_monotonic")
        )
        if timestamp is None:
            return True
        captured_at = snapshot_timestamp(snapshot.get("captured_monotonic"))
        heartbeat_at = snapshot_timestamp(snapshot.get("heartbeat_monotonic"))
        return (
            captured_at is not None
            and heartbeat_at is not None
            and timestamp >= 0.0
            and timestamp <= min(captured_at, heartbeat_at)
        )

    def _source_timestamps_valid(self, path: str, snapshot: SnapshotPayload) -> bool:
        for source_key in self._schema.source_keys:
            if self._source_timestamp_valid(source_key, snapshot):
                continue
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s has invalid or newer %s_observed_monotonic",
                source_key,
            )
            return False
        return True

    def _source_pairs_valid(self, path: str, snapshot: SnapshotPayload) -> bool:
        for source_key in self._schema.source_keys:
            timestamp_key = f"{source_key}_captured_at"
            monotonic_key = f"{source_key}_observed_monotonic"
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            if paired_optional_values(
                snapshot.get(value_key),
                snapshot.get(timestamp_key),
            ) and paired_optional_values(
                snapshot.get(value_key),
                snapshot.get(monotonic_key),
            ):
                continue
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s must provide %s, %s, and %s together",
                value_key,
                timestamp_key,
                monotonic_key,
            )
            return False
        return True

    def _battery_soc_valid(self, path: str, source: SnapshotPayload, snapshot: SnapshotPayload) -> bool:
        if not valid_battery_soc(snapshot.get("battery_soc")):
            self._invalid(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s has out-of-range battery_soc=%r",
                source.get("battery_soc"),
            )
            return False
        combined_soc = snapshot.get("battery_combined_soc")
        if combined_soc is None or valid_battery_soc(combined_soc):
            return True
        self._invalid(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has out-of-range battery_combined_soc=%r",
            source.get("battery_combined_soc"),
        )
        return False

    def _semantics_valid(self, path: str, source: SnapshotPayload, snapshot: SnapshotPayload) -> bool:
        return (
            self._timestamps_ordered(path, snapshot)
            and self._source_pairs_valid(path, snapshot)
            and self._source_timestamps_valid(path, snapshot)
            and self._battery_soc_valid(path, source, snapshot)
        )

    def validate(self, path: str, raw_snapshot: object) -> SnapshotPayload | None:
        """Return a normalized snapshot, or ``None`` after one exact warning."""
        shaped = self._shape(path, raw_snapshot)
        if shaped is None:
            return None
        source, version = shaped
        normalized = self._normalize(path, source, version)
        if normalized is None:
            return None
        return normalized if self._semantics_valid(path, source, normalized) else None
