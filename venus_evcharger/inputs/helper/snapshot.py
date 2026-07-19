# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread-safe RAM snapshot ownership for the auto-input helper."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping

from venus_evcharger.core.shared import AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION, compact_json, write_text_atomically
from venus_evcharger.energy.grid_fusion import GridMeasurementFusion
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import Snapshot, SnapshotWriterPort, SourceReaderPort
from venus_evcharger.inputs.helper.grid_fusion_snapshot import apply_grid_fusion
from venus_evcharger.inputs.helper.payload_types import is_object_mapping


class AtomicSnapshotWriter:
    """Atomically persist changed snapshots in the configured RAM path."""

    def __init__(self, settings: AutoInputHelperSettings) -> None:
        self.settings = settings
        self._last_payload: str | None = None

    def write(self, payload: Mapping[str, object]) -> None:
        normalized = dict(payload)
        normalized.setdefault("snapshot_version", AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION)
        normalized["writer_pid"] = os.getpid()
        normalized["helper_generation"] = self.settings.helper_generation
        normalized["runtime_instance_id"] = self.settings.runtime_instance_id
        serialized = compact_json(normalized)
        if serialized == self._last_payload:
            return
        write_text_atomically(self.settings.snapshot_path, serialized)
        self._last_payload = serialized


class SnapshotStore:
    """Schedule source reads and own all snapshot freshness metadata."""

    def __init__(
        self,
        settings: AutoInputHelperSettings,
        sources: SourceReaderPort,
        writer: SnapshotWriterPort,
        stop_requested: Callable[[], bool],
    ) -> None:
        self.settings = settings
        self.sources = sources
        self.writer = writer
        self.stop_requested = stop_requested
        self._state = empty_snapshot()
        self._next_poll_at = {"pv": 0.0, "battery": 0.0, "grid": 0.0}
        self._lock = threading.RLock()
        self._grid_fusion = GridMeasurementFusion(settings.grid_fusion_config)

    def collect(self, now: float | None = None) -> Snapshot:
        current = time.time() if now is None else float(now)
        timestamp_after_work: Callable[[], float] = time.time if now is None else lambda: current
        with self._lock:
            snapshot = dict(self._state)
            due_sources = self._due_sources(current)
        for source_name, interval, getter, value_key, captured_key in due_sources:
            value = getter()
            source_current = timestamp_after_work()
            with self._lock:
                self._apply_source(snapshot, source_name, value_key, captured_key, value, source_current)
                self._next_poll_at[source_name] = source_current + interval
        with self._lock:
            final_current = timestamp_after_work()
            self._finalize(snapshot, final_current)
            self._state = dict(snapshot)
        return snapshot

    def refresh_source(self, source_name: str, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        source = self._source_read(source_name)
        if source is None:
            return
        value, value_key, captured_key = source
        with self._lock:
            snapshot = dict(self._state)
            self._apply_source(snapshot, source_name, value_key, captured_key, value, current)
            if source_name in {"battery", "grid"}:
                apply_grid_fusion(self._grid_fusion, snapshot, current)
            self._stamp(snapshot, current)
            self._state = snapshot
            self.writer.write(snapshot)

    def refresh_all(self, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        for source_name in ("pv", "battery", "grid"):
            self.refresh_source(source_name, current)

    def validation_poll(self) -> bool:
        self.refresh_all()
        return not self.stop_requested()

    def heartbeat(self) -> bool:
        current = time.time()
        with self._lock:
            snapshot = dict(self._state)
            snapshot["heartbeat_at"] = current
            snapshot.setdefault("helper_state", "running")
            snapshot.setdefault("helper_status", snapshot["helper_state"])
            self._stamp_identity(snapshot)
            self._state = snapshot
            self.writer.write(snapshot)
        return not self.stop_requested()

    def write_lifecycle(self, state: str, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        with self._lock:
            snapshot = dict(self._state)
            snapshot["captured_at"] = current
            snapshot["heartbeat_at"] = current
            snapshot["helper_state"] = state
            snapshot["helper_status"] = state
            self._stamp_identity(snapshot)
            self._state = snapshot
            self.writer.write(snapshot)

    def current(self) -> Snapshot:
        with self._lock:
            return dict(self._state)

    def _due_sources(
        self,
        current: float,
    ) -> tuple[tuple[str, float, Callable[[], object], str, str], ...]:
        specs: tuple[tuple[str, float, Callable[[], object], str, str], ...] = (
            ("pv", self.settings.auto_pv_poll_interval_seconds, self.sources.pv_power, "pv_power", "pv_captured_at"),
            (
                "battery",
                self.settings.auto_battery_poll_interval_seconds,
                self.sources.battery_snapshot,
                "battery_soc",
                "battery_captured_at",
            ),
            (
                "grid",
                self.settings.auto_grid_poll_interval_seconds,
                self.sources.grid_power,
                "grid_gateway_power",
                "grid_gateway_captured_at",
            ),
        )
        return tuple(spec for spec in specs if current >= self._next_poll_at[spec[0]])

    def _source_read(self, source_name: str) -> tuple[object, str, str] | None:
        if source_name == "pv":
            return self.sources.pv_power(), "pv_power", "pv_captured_at"
        if source_name == "battery":
            return self.sources.battery_snapshot(), "battery_soc", "battery_captured_at"
        if source_name == "grid":
            return self.sources.grid_power(), "grid_gateway_power", "grid_gateway_captured_at"
        return None

    def _finalize(self, snapshot: Snapshot, current: float) -> None:
        apply_grid_fusion(self._grid_fusion, snapshot, current)
        self._stamp(snapshot, current)

    def _stamp(self, snapshot: Snapshot, current: float) -> None:
        snapshot["captured_at"] = current
        snapshot["heartbeat_at"] = current
        self._stamp_identity(snapshot)

    def _stamp_identity(self, snapshot: Snapshot) -> None:
        snapshot["snapshot_version"] = AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
        snapshot["writer_pid"] = os.getpid()
        snapshot["helper_generation"] = self.settings.helper_generation
        snapshot["runtime_instance_id"] = self.settings.runtime_instance_id

    @staticmethod
    def _apply_source(
        snapshot: Snapshot,
        source_name: str,
        value_key: str,
        captured_key: str,
        value: object,
        current: float,
    ) -> None:
        if source_name == "battery" and is_object_mapping(value):
            SnapshotStore._apply_battery(snapshot, value, captured_key, current)
            return
        snapshot[value_key] = value
        snapshot[captured_key] = None if value is None else current
        _set_source_status(snapshot, source_name, value is not None)

    @staticmethod
    def _apply_battery(
        snapshot: Snapshot,
        value: Mapping[object, object],
        captured_key: str,
        current: float,
    ) -> None:
        battery_soc = value.get("battery_soc")
        snapshot["battery_soc"] = battery_soc
        snapshot[captured_key] = None if battery_soc is None else current
        for field_name in BATTERY_SNAPSHOT_FIELDS[1:]:
            snapshot[field_name] = value.get(field_name)
        _set_source_status(snapshot, "battery", battery_soc is not None)


BATTERY_SNAPSHOT_FIELDS = (
    "battery_soc",
    "battery_combined_soc",
    "battery_combined_usable_capacity_wh",
    "battery_combined_charge_power_w",
    "battery_combined_discharge_power_w",
    "battery_combined_net_power_w",
    "battery_combined_ac_power_w",
    "battery_combined_pv_input_power_w",
    "battery_combined_grid_interaction_w",
    "battery_headroom_charge_w",
    "battery_headroom_discharge_w",
    "expected_near_term_export_w",
    "expected_near_term_import_w",
    "battery_discharge_balance_mode",
    "battery_discharge_balance_target_distribution_mode",
    "battery_discharge_balance_error_w",
    "battery_discharge_balance_max_abs_error_w",
    "battery_discharge_balance_total_discharge_w",
    "battery_discharge_balance_eligible_source_count",
    "battery_discharge_balance_active_source_count",
    "battery_discharge_balance_control_candidate_count",
    "battery_discharge_balance_control_ready_count",
    "battery_discharge_balance_supported_control_source_count",
    "battery_discharge_balance_experimental_control_source_count",
    "battery_average_confidence",
    "battery_source_count",
    "battery_online_source_count",
    "battery_valid_soc_source_count",
    "battery_battery_source_count",
    "battery_hybrid_inverter_source_count",
    "battery_inverter_source_count",
    "battery_sources",
    "battery_learning_profiles",
)


def _set_source_status(snapshot: Snapshot, source_name: str, available: bool) -> None:
    snapshot[f"{source_name}_status"] = "ok" if available else "missing"
    snapshot["helper_state"] = "running"
    snapshot["helper_status"] = "running"


def empty_snapshot(captured_at: float | None = None) -> Snapshot:
    return {
        "snapshot_version": AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
        "captured_at": captured_at,
        "heartbeat_at": captured_at,
        "writer_pid": os.getpid(),
        "helper_state": "starting",
        "helper_status": "starting",
        "pv_status": "missing",
        "pv_captured_at": None,
        "pv_power": None,
        "battery_status": "missing",
        "battery_captured_at": None,
        "battery_soc": None,
        "battery_combined_soc": None,
        "battery_combined_usable_capacity_wh": None,
        "battery_combined_charge_power_w": None,
        "battery_combined_discharge_power_w": None,
        "battery_combined_net_power_w": None,
        "battery_combined_ac_power_w": None,
        "battery_source_count": 0,
        "battery_online_source_count": 0,
        "battery_valid_soc_source_count": 0,
        "battery_sources": [],
        "battery_learning_profiles": {},
        "grid_status": "missing",
        "grid_captured_at": None,
        "grid_power": None,
        "grid_gateway_captured_at": None,
        "grid_gateway_power": None,
        "grid_primary_captured_at": None,
        "grid_primary_power": None,
        "grid_fusion_enabled": False,
        "grid_fusion_primary_source_id": "",
        "grid_fusion_backup_source_id": "victron",
        "grid_selected_source_id": "",
        "grid_fusion_state": "unavailable",
        "grid_fusion_confidence": 0.0,
        "grid_fusion_primary_valid": False,
        "grid_fusion_backup_valid": False,
        "grid_fusion_primary_age_seconds": None,
        "grid_fusion_backup_age_seconds": None,
        "grid_fusion_difference_watts": None,
        "grid_fusion_tolerance_watts": None,
        "grid_fusion_primary_invalid_samples": 0,
        "grid_fusion_primary_recovery_samples": 0,
        "grid_fusion_mismatch_samples": 0,
    }
