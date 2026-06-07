# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
# pyright: reportAttributeAccessIssue=false
from __future__ import annotations

import math
from typing import Any, Callable

from venus_evcharger.core.contracts import paired_optional_values, valid_battery_soc


class _AutoInputSupervisorSnapshotValidationMixin:
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
    )

    @staticmethod
    def _coerce_snapshot_timestamp(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized):
            return None
        return normalized

    @classmethod
    def _coerce_snapshot_number(cls, value: Any) -> float | None:
        return cls._coerce_snapshot_timestamp(value)

    @classmethod
    def _validate_snapshot_version(cls, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            version = int(value)
        except (TypeError, ValueError):
            return None
        return version

    @staticmethod
    def _coerce_snapshot_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _invalid_snapshot(
        self,
        warning_key: str,
        path: str,
        message: str,
        *args: object,
    ) -> dict[str, Any] | None:
        svc = self.service
        svc._warning_throttled(
            warning_key,
            max(1.0, svc.auto_input_helper_restart_seconds),
            message,
            path,
            *args,
        )
        return None

    def _normalize_snapshot_fields(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
        keys: tuple[str, ...],
        coercer: Callable[[Any], float | None],
        field_type: str,
    ) -> bool:
        for key in keys:
            normalized_value = coercer(snapshot.get(key))
            if snapshot.get(key) is not None and normalized_value is None:
                self._invalid_snapshot(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has invalid %s field %s=%r",
                    field_type,
                    key,
                    snapshot.get(key),
                )
                return False
            normalized[key] = normalized_value
        return True

    def _validate_snapshot_temporal_order(self, path: str, normalized: dict[str, Any]) -> dict[str, Any] | None:
        if normalized["captured_at"] is None or normalized["heartbeat_at"] is None:
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires numeric captured_at and heartbeat_at fields",
            )
        if normalized["heartbeat_at"] < normalized["captured_at"]:
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s has heartbeat_at older than captured_at",
            )
        return normalized

    def _validate_source_timestamps(self, path: str, normalized: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("pv_captured_at", "battery_captured_at", "grid_captured_at"):
            timestamp = normalized.get(key)
            if timestamp is None:
                continue
            if timestamp > normalized["captured_at"] or timestamp > normalized["heartbeat_at"]:
                return self._invalid_snapshot(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has %s newer than captured_at/heartbeat_at",
                    key,
                )
        return normalized

    def _validate_source_value_timestamp_pairs(self, path: str, normalized: dict[str, Any]) -> dict[str, Any] | None:
        for source_key in self.SNAPSHOT_SOURCE_KEYS:
            timestamp_key = f"{source_key}_captured_at"
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            timestamp = normalized.get(timestamp_key)
            value = normalized.get(value_key)
            if paired_optional_values(value, timestamp):
                continue
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s must provide %s and %s together",
                value_key,
                timestamp_key,
            )
        return normalized

    def _validate_snapshot_battery_soc(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        battery_soc = normalized.get("battery_soc")
        combined_soc = normalized.get("battery_combined_soc")
        if not valid_battery_soc(battery_soc):
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s has out-of-range battery_soc=%r",
                snapshot.get("battery_soc"),
            )
        if combined_soc is None or valid_battery_soc(combined_soc):
            return normalized
        return self._invalid_snapshot(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has out-of-range battery_combined_soc=%r",
            snapshot.get("battery_combined_soc"),
        )

    def _validate_snapshot_shape(self, path: str, snapshot: Any) -> int | None:
        if not isinstance(snapshot, dict):
            self._invalid_snapshot(
                "auto-input-helper-invalid",
                path,
                "Auto input helper snapshot %s is not a JSON object",
            )
            return None
        missing_keys = sorted(self.SNAPSHOT_REQUIRED_KEYS.difference(snapshot))
        if missing_keys:
            self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s is missing required keys: %s",
                ", ".join(missing_keys),
            )
            return None
        version = self._validate_snapshot_version(snapshot.get("snapshot_version"))
        if version != self.SNAPSHOT_SCHEMA_VERSION:
            self._invalid_snapshot(
                "auto-input-helper-version-invalid",
                path,
                "Auto input helper snapshot %s has unsupported snapshot_version=%s",
                snapshot.get("snapshot_version"),
            )
            return None
        return version

    def _normalize_snapshot_payload(
        self,
        path: str,
        snapshot: dict[str, Any],
        version: int,
    ) -> dict[str, Any] | None:
        normalized = dict(snapshot)
        normalized["snapshot_version"] = version
        if self._validate_snapshot_identity(path, snapshot, normalized) is None:
            return None
        if not self._normalize_snapshot_scalar_fields(path, snapshot, normalized):
            return None
        return normalized if self._normalize_snapshot_remaining_fields(path, snapshot, normalized) else None

    def _normalize_snapshot_scalar_fields(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> bool:
        """Normalize timestamp and numeric snapshot fields."""
        for keys, coercer, label in self._snapshot_normalization_specs():
            if not self._normalize_snapshot_fields(path, snapshot, normalized, keys, coercer, label):
                return False
        return True

    def _normalize_snapshot_remaining_fields(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> bool:
        """Normalize count and structured snapshot fields."""
        return self._normalize_snapshot_count_fields(
            path,
            snapshot,
            normalized,
        ) and self._normalize_snapshot_structured_fields(path, snapshot, normalized)

    def _snapshot_normalization_specs(self) -> tuple[tuple[tuple[str, ...], Any, str], ...]:
        return (
            (
                ("captured_at", "heartbeat_at", "pv_captured_at", "battery_captured_at", "grid_captured_at"),
                self._coerce_snapshot_timestamp,
                "timestamp",
            ),
            (
                ("pv_power", "battery_soc", "grid_power"),
                self._coerce_snapshot_number,
                "numeric",
            ),
            (
                self.OPTIONAL_NUMERIC_FIELDS,
                self._coerce_snapshot_number,
                "numeric",
            ),
        )

    def _validate_snapshot_identity(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self._normalize_snapshot_writer(path, snapshot, normalized):
            return None
        if not self._normalize_snapshot_generation(path, snapshot, normalized):
            return None
        return self._normalize_snapshot_runtime_instance(path, snapshot, normalized)

    def _normalize_snapshot_writer(self, path: str, snapshot: dict[str, Any], normalized: dict[str, Any]) -> bool:
        writer_pid = self._coerce_snapshot_int(snapshot.get("writer_pid"))
        if writer_pid is None or writer_pid <= 0:
            self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires positive integer writer_pid field",
            )
            return False
        normalized["writer_pid"] = writer_pid
        return True

    def _normalize_snapshot_generation(self, path: str, snapshot: dict[str, Any], normalized: dict[str, Any]) -> bool:
        helper_generation = self._coerce_snapshot_int(snapshot.get("helper_generation"))
        if helper_generation is None or helper_generation < 0:
            self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires non-negative integer helper_generation field",
            )
            return False
        normalized["helper_generation"] = helper_generation
        return True

    def _normalize_snapshot_runtime_instance(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        runtime_instance_id = snapshot.get("runtime_instance_id")
        if not isinstance(runtime_instance_id, str) or not runtime_instance_id.strip():
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires non-empty runtime_instance_id field",
            )
        normalized["runtime_instance_id"] = runtime_instance_id.strip()
        return normalized

    def _normalize_snapshot_count_fields(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> bool:
        for key in self.OPTIONAL_COUNT_FIELDS:
            raw_value = snapshot.get(key)
            if raw_value is None:
                normalized[key] = 0
                continue
            if isinstance(raw_value, bool):
                self._invalid_snapshot(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has invalid count field %s=%r",
                    key,
                    raw_value,
                )
                return False
            try:
                normalized[key] = max(0, int(raw_value))
            except (TypeError, ValueError):
                self._invalid_snapshot(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has invalid count field %s=%r",
                    key,
                    raw_value,
                )
                return False
        return True

    def _normalize_snapshot_structured_fields(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> bool:
        battery_sources = self._normalized_snapshot_battery_sources(path, snapshot)
        if battery_sources is None:
            return False
        learning_profiles = self._normalized_snapshot_learning_profiles(path, snapshot)
        if learning_profiles is None:
            return False
        normalized["battery_sources"] = battery_sources
        normalized["battery_learning_profiles"] = learning_profiles
        return True

    def _normalized_snapshot_battery_sources(
        self,
        path: str,
        snapshot: dict[str, Any],
    ) -> list[Any] | None:
        sources = snapshot.get("battery_sources")
        if sources is None:
            return []
        if isinstance(sources, list):
            return [dict(item) if isinstance(item, dict) else item for item in sources]
        self._invalid_structured_snapshot_field(path, "battery_sources")
        return None

    def _normalized_snapshot_learning_profiles(
        self,
        path: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        learning_profiles = snapshot.get("battery_learning_profiles")
        if learning_profiles is None:
            return {}
        if isinstance(learning_profiles, dict):
            return {
                str(key): dict(value) if isinstance(value, dict) else value
                for key, value in learning_profiles.items()
            }
        self._invalid_structured_snapshot_field(path, "battery_learning_profiles")
        return None

    def _invalid_structured_snapshot_field(self, path: str, field_name: str) -> None:
        self._invalid_snapshot(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has invalid %s payload",
            field_name,
        )

    def _validate_snapshot_semantics(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized_temporal = self._validate_snapshot_temporal_order(path, normalized)
        if normalized_temporal is None:
            return None
        normalized_pairs = self._validate_source_value_timestamp_pairs(path, normalized_temporal)
        if normalized_pairs is None:
            return None
        normalized_timestamps = self._validate_source_timestamps(path, normalized_pairs)
        if normalized_timestamps is None:
            return None
        return self._validate_snapshot_battery_soc(path, snapshot, normalized_timestamps)

    def _validate_snapshot_dict(self, path: str, snapshot: Any) -> dict[str, Any] | None:
        version = self._validate_snapshot_shape(path, snapshot)
        if version is None:
            return None
        normalized = self._normalize_snapshot_payload(path, snapshot, version)
        if normalized is None:
            return None
        return self._validate_snapshot_semantics(path, snapshot, normalized)
