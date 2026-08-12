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
from venus_evcharger.inputs.helper.snapshot_builder import (
    BATTERY_TARGET,
    GRID_GATEWAY_TARGET,
    PV_TARGET,
    SnapshotBuilder,
    SnapshotTarget,
    SourceSample,
)
from venus_evcharger.inputs.helper.snapshot_defaults import empty_snapshot


class AtomicSnapshotWriter:
    """Atomically persist changed snapshots in the configured RAM path."""

    def __init__(self, settings: AutoInputHelperSettings) -> None:
        self.settings = settings
        self._last_payload: str | None = None
        self._sequence = 0

    def write(self, payload: Mapping[str, object]) -> None:
        normalized = dict(payload)
        normalized.setdefault("snapshot_version", AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION)
        normalized["writer_pid"] = os.getpid()
        normalized["helper_generation"] = self.settings.helper_generation
        normalized["runtime_instance_id"] = self.settings.runtime_instance_id
        content_payload = compact_json(normalized)
        if content_payload == self._last_payload:
            return
        self._sequence += 1
        normalized["snapshot_sequence"] = self._sequence
        serialized = compact_json(normalized)
        write_text_atomically(self.settings.snapshot_path, serialized)
        self._last_payload = content_payload


@dataclass(frozen=True, slots=True)
class _SourcePollSpec:
    """One scheduled source read and its snapshot destination."""

    target: SnapshotTarget
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
        (
            current,
            timestamp_after_work,
            current_monotonic,
            monotonic_after_work,
        ) = _collection_clock(now)
        with self._lock:
            snapshot = dict(self._state)
            due_sources = self._due_sources(current_monotonic)
        if due_sources:
            self.sources.prepare_cycle()
        for spec in due_sources:
            value = spec.getter()
            source_current = timestamp_after_work()
            source_monotonic = monotonic_after_work()
            observed_at = _source_observed_at(self.sources, spec.target.name, source_current)
            observed_monotonic = _source_observed_monotonic(
                self.sources,
                spec.target.name,
                source_monotonic,
            )
            with self._lock:
                SnapshotBuilder(snapshot).apply_source(
                    spec.target,
                    value,
                    observed_at,
                    observed_monotonic,
                )
                self._next_poll_at[spec.target.name] = (
                    source_monotonic + spec.interval
                )
        with self._lock:
            final_current = timestamp_after_work()
            final_monotonic = monotonic_after_work()
            self._finalize(snapshot, final_current, final_monotonic)
            self._state = dict(snapshot)
        return snapshot

    def poll(self) -> bool:
        """Refresh due RAM-cache sources and publish one coherent snapshot."""
        if self.stop_requested():
            return False
        self.writer.write(self.collect())
        return True

    def refresh_source(self, source_name: str, now: float | None = None) -> None:
        current, current_monotonic = _instant_clock(now)
        self.sources.prepare_cycle()
        self._refresh_prepared_source(source_name, current, current_monotonic)

    def _refresh_prepared_source(
        self,
        source_name: str,
        current: float,
        current_monotonic: float,
    ) -> None:
        source = self._source_read(source_name)
        if source is None:
            return
        value, target = source
        observed_at = self.sources.observed_at(source_name) or current
        observed_monotonic = (
            self.sources.observed_monotonic(source_name) or current_monotonic
        )
        with self._lock:
            snapshot = dict(self._state)
            SnapshotBuilder(snapshot).apply_source(
                target,
                value,
                observed_at,
                observed_monotonic,
            )
            if source_name in {"battery", "grid"}:
                apply_grid_fusion(self._grid_fusion, snapshot, current_monotonic)
            self._stamp(snapshot, current, current_monotonic)
            self._state = snapshot
            self.writer.write(snapshot)

    def refresh_all(self, now: float | None = None) -> None:
        current, current_monotonic = _instant_clock(now)
        self.sources.prepare_cycle()
        samples = tuple(
            sample
            for source_name in ("pv", "battery", "grid")
            if (
                sample := self._prepared_source_sample(
                    source_name,
                    current,
                    current_monotonic,
                )
            )
            is not None
        )
        with self._lock:
            snapshot = dict(self._state)
            builder = SnapshotBuilder(snapshot)
            for sample in samples:
                builder.apply_source(
                    sample.target,
                    sample.value,
                    sample.captured_at,
                    sample.observed_monotonic,
                )
            self._finalize(snapshot, current, current_monotonic)
            self._state = snapshot
            self.writer.write(snapshot)

    def validation_poll(self) -> bool:
        self.refresh_all()
        return not self.stop_requested()

    def heartbeat(self) -> bool:
        current = time.time()
        current_monotonic = time.monotonic()
        with self._lock:
            snapshot = dict(self._state)
            snapshot["heartbeat_at"] = current
            snapshot["heartbeat_monotonic"] = current_monotonic
            snapshot.setdefault("helper_state", "running")
            snapshot.setdefault("helper_status", snapshot["helper_state"])
            self._stamp_identity(snapshot)
            self._state = snapshot
            self.writer.write(snapshot)
        return not self.stop_requested()

    def write_lifecycle(self, state: str, now: float | None = None) -> None:
        current, current_monotonic = _instant_clock(now)
        with self._lock:
            snapshot = dict(self._state)
            snapshot["captured_at"] = current
            snapshot["captured_monotonic"] = current_monotonic
            snapshot["heartbeat_at"] = current
            snapshot["heartbeat_monotonic"] = current_monotonic
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
                PV_TARGET,
                self.settings.auto_pv_poll_interval_seconds,
                self.sources.pv_power,
            ),
            _SourcePollSpec(
                BATTERY_TARGET,
                self.settings.auto_battery_poll_interval_seconds,
                self.sources.battery_snapshot,
            ),
            _SourcePollSpec(
                GRID_GATEWAY_TARGET,
                self.settings.auto_grid_poll_interval_seconds,
                self.sources.grid_power,
            ),
        )
        return tuple(spec for spec in specs if current >= self._next_poll_at[spec.target.name])

    def _source_read(self, source_name: str) -> tuple[object, SnapshotTarget] | None:
        if source_name == "pv":
            return self.sources.pv_power(), PV_TARGET
        if source_name == "battery":
            return self.sources.battery_snapshot(), BATTERY_TARGET
        if source_name == "grid":
            return self.sources.grid_power(), GRID_GATEWAY_TARGET
        return None

    def _prepared_source_sample(
        self,
        source_name: str,
        current: float,
        current_monotonic: float,
    ) -> SourceSample | None:
        source = self._source_read(source_name)
        if source is None:
            return None
        value, target = source
        observed_at = self.sources.observed_at(source_name) or current
        observed_monotonic = (
            self.sources.observed_monotonic(source_name) or current_monotonic
        )
        return SourceSample(value, target, observed_at, observed_monotonic)

    def _finalize(
        self,
        snapshot: Snapshot,
        current: float,
        current_monotonic: float,
    ) -> None:
        apply_grid_fusion(self._grid_fusion, snapshot, current_monotonic)
        self._stamp(snapshot, current, current_monotonic)

    def _stamp(
        self,
        snapshot: Snapshot,
        current: float,
        current_monotonic: float,
    ) -> None:
        snapshot["captured_at"] = current
        snapshot["captured_monotonic"] = current_monotonic
        snapshot["heartbeat_at"] = current
        snapshot["heartbeat_monotonic"] = current_monotonic
        self._stamp_identity(snapshot)

    def _stamp_identity(self, snapshot: Snapshot) -> None:
        snapshot["snapshot_version"] = AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
        snapshot["writer_pid"] = os.getpid()
        snapshot["helper_generation"] = self.settings.helper_generation
        snapshot["runtime_instance_id"] = self.settings.runtime_instance_id

def _collection_clock(
    now: float | None,
) -> tuple[float, Callable[[], float], float, Callable[[], float]]:
    if now is None:
        return time.time(), time.time, time.monotonic(), time.monotonic
    current = float(now)
    return current, lambda: current, current, lambda: current


def _instant_clock(now: float | None) -> tuple[float, float]:
    if now is None:
        return time.time(), time.monotonic()
    current = float(now)
    return current, current


def _source_observed_at(sources: SourceReaderPort, source_name: str, fallback: float) -> float:
    return sources.observed_at(source_name) or fallback


def _source_observed_monotonic(
    sources: SourceReaderPort,
    source_name: str,
    fallback: float,
) -> float:
    return sources.observed_monotonic(source_name) or fallback

