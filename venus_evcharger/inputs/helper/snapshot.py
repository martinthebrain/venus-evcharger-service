# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread-safe RAM snapshot ownership for the auto-input helper."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class _SourceTarget:
    """Snapshot destination for one semantic source."""

    name: str
    value_key: str
    captured_key: str


@dataclass(frozen=True, slots=True)
class _SourcePollSpec:
    """One scheduled source read and its snapshot destination."""

    target: _SourceTarget
    interval: float
    getter: Callable[[], object]


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
        current, timestamp_after_work = _collection_clock(now)
        with self._lock:
            snapshot = dict(self._state)
            due_sources = self._due_sources(current)
        if due_sources:
            self.sources.prepare_cycle()
        for spec in due_sources:
            value = spec.getter()
            source_current = timestamp_after_work()
            observed_at = _source_observed_at(self.sources, spec.target.name, source_current)
            with self._lock:
                self._apply_source(snapshot, spec.target, value, observed_at)
                self._next_poll_at[spec.target.name] = source_current + spec.interval
        with self._lock:
            final_current = timestamp_after_work()
            self._finalize(snapshot, final_current)
            self._state = dict(snapshot)
        return snapshot

    def poll(self) -> bool:
        """Refresh due RAM-cache sources and publish one coherent snapshot."""
        if self.stop_requested():
            return False
        self.writer.write(self.collect())
        return True

    def refresh_source(self, source_name: str, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        self.sources.prepare_cycle()
        self._refresh_prepared_source(source_name, current)

    def _refresh_prepared_source(self, source_name: str, current: float) -> None:
        source = self._source_read(source_name)
        if source is None:
            return
        value, target = source
        observed_at = self.sources.observed_at(source_name) or current
        with self._lock:
            snapshot = dict(self._state)
            self._apply_source(snapshot, target, value, observed_at)
            if source_name in {"battery", "grid"}:
                apply_grid_fusion(self._grid_fusion, snapshot, current)
            self._stamp(snapshot, current)
            self._state = snapshot
            self.writer.write(snapshot)

    def refresh_all(self, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        self.sources.prepare_cycle()
        samples = tuple(
            sample
            for source_name in ("pv", "battery", "grid")
            if (sample := self._prepared_source_sample(source_name, current))
            is not None
        )
        with self._lock:
            snapshot = dict(self._state)
            for value, target, observed_at in samples:
                self._apply_source(snapshot, target, value, observed_at)
            self._finalize(snapshot, current)
            self._state = snapshot
            self.writer.write(snapshot)

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
    ) -> tuple[_SourcePollSpec, ...]:
        specs = (
            _SourcePollSpec(
                _SourceTarget("pv", "pv_power", "pv_captured_at"),
                self.settings.auto_pv_poll_interval_seconds,
                self.sources.pv_power,
            ),
            _SourcePollSpec(
                _SourceTarget("battery", "battery_soc", "battery_captured_at"),
                self.settings.auto_battery_poll_interval_seconds,
                self.sources.battery_snapshot,
            ),
            _SourcePollSpec(
                _SourceTarget("grid", "grid_gateway_power", "grid_gateway_captured_at"),
                self.settings.auto_grid_poll_interval_seconds,
                self.sources.grid_power,
            ),
        )
        return tuple(spec for spec in specs if current >= self._next_poll_at[spec.target.name])

    def _source_read(self, source_name: str) -> tuple[object, _SourceTarget] | None:
        if source_name == "pv":
            return self.sources.pv_power(), _SourceTarget("pv", "pv_power", "pv_captured_at")
        if source_name == "battery":
            return self.sources.battery_snapshot(), _SourceTarget("battery", "battery_soc", "battery_captured_at")
        if source_name == "grid":
            return self.sources.grid_power(), _SourceTarget("grid", "grid_gateway_power", "grid_gateway_captured_at")
        return None

    def _prepared_source_sample(
        self,
        source_name: str,
        current: float,
    ) -> tuple[object, _SourceTarget, float] | None:
        source = self._source_read(source_name)
        if source is None:
            return None
        value, target = source
        observed_at = self.sources.observed_at(source_name) or current
        return value, target, observed_at

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
        target: _SourceTarget,
        value: object,
        current: float,
    ) -> None:
        if target.name == "battery" and is_object_mapping(value):
            SnapshotStore._apply_battery(snapshot, value, target.captured_key, current)
            return
        snapshot[target.value_key] = value
        snapshot[target.captured_key] = None if value is None else current
        _set_source_status(snapshot, target.name, value is not None)

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


def _collection_clock(now: float | None) -> tuple[float, Callable[[], float]]:
    if now is None:
        return time.time(), time.time
    current = float(now)
    return current, lambda: current


def _source_observed_at(sources: SourceReaderPort, source_name: str, fallback: float) -> float:
    return sources.observed_at(source_name) or fallback


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
