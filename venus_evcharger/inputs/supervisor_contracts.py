# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts for auto-input snapshot supervision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class HelperProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def terminate(self) -> None: ...


class SnapshotRefreshPort(Protocol):
    def refresh_snapshot(self, monotonic_at: float | None = None) -> None: ...


class SupervisorRuntimePort(Protocol):
    """Runtime operations used by helper supervision."""

    def ensure_worker_state(self) -> None: ...
    def update_worker_snapshot(self, **fields: object) -> None: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        exc_info: BaseException | None = None,
    ) -> None: ...


class SupervisorAutoPort(Protocol):
    """Auto-mode decision used while applying helper snapshots."""

    def mode_uses_auto_logic(self, mode: object) -> bool: ...


class AutoInputSupervisorService(Protocol):
    @property
    def runtime(self) -> SupervisorRuntimePort: ...
    @property
    def auto(self) -> SupervisorAutoPort: ...
    auto_input_helper_restart_seconds: float
    auto_input_helper_stale_seconds: float
    auto_input_snapshot_path: str
    gateway_health_path: str
    virtual_mode: int
    _auto_input_helper_generation: int
    _auto_input_helper_last_start_at: float
    _auto_input_helper_process: HelperProcess | None
    _auto_input_helper_restart_requested_at: float | None
    _auto_input_runtime_instance_id: str
    _auto_input_snapshot_generation: int | None
    _auto_input_snapshot_last_captured_at: float | None
    _auto_input_snapshot_last_sequence: int | None
    _auto_input_snapshot_last_seen: float | None
    _auto_input_snapshot_mtime_ns: int | None
    _auto_input_snapshot_runtime_instance_id: str | None
    _auto_input_snapshot_seen_for_current_helper: bool
    _auto_input_snapshot_version: int | None
    _auto_input_snapshot_writer_pid: int | None
    def time_now(self) -> float: ...
    def monotonic_now(self) -> float: ...


@dataclass(frozen=True)
class SnapshotSchema:
    version: int
    source_keys: tuple[str, ...]
    required_keys: frozenset[str]
    future_timestamp_tolerance_seconds: float


SnapshotPayload = dict[str, object]
SnapshotMapping = Mapping[str, object]
