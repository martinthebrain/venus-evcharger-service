# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed scenario harness for auto-input supervisor component tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.inputs.supervisor_contracts import HelperProcess
from tests.support.gateway_pressure import FreshOkGatewayPressurePolicy
from venus_evcharger.ports.gateway_pressure import GatewayPressurePolicy


@dataclass
class SupervisorRuntimeFake:
    ensure_calls: int = 0
    snapshots: list[dict[str, object]] = field(
        default_factory=lambda: list[dict[str, object]]()
    )
    warnings: list[tuple[str, float, str, tuple[object, ...], BaseException | None]] = field(
        default_factory=lambda: list[
            tuple[str, float, str, tuple[object, ...], BaseException | None]
        ]()
    )

    def ensure_worker_state(self) -> None:
        self.ensure_calls += 1

    def update_worker_snapshot(self, **fields: object) -> None:
        self.snapshots.append(dict(fields))

    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        exc_info: BaseException | None = None,
    ) -> None:
        self.warnings.append((warning_key, interval_seconds, message, args, exc_info))


@dataclass
class SupervisorAutoFake:
    automatic_modes: frozenset[int] = frozenset((1, 2))

    def mode_uses_auto_logic(self, mode: object) -> bool:
        return isinstance(mode, int) and mode in self.automatic_modes


@dataclass
class HelperProcessFake:
    pid: int = 4321
    return_code: int | None = None
    terminate_calls: int = 0
    kill_calls: int = 0
    terminate_error: Exception | None = None
    kill_error: Exception | None = None

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_error is not None:
            raise self.terminate_error

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_error is not None:
            raise self.kill_error


@dataclass
class SnapshotRefreshFake:
    calls: list[float | None] = field(default_factory=lambda: list[float | None]())

    def refresh_snapshot(self, now: float | None = None) -> None:
        self.calls.append(now)


@dataclass
class AutoInputSupervisorServiceFake:
    runtime: SupervisorRuntimeFake = field(default_factory=SupervisorRuntimeFake)
    auto: SupervisorAutoFake = field(default_factory=SupervisorAutoFake)
    auto_input_helper_restart_seconds: float = 5.0
    auto_input_helper_stale_seconds: float = 15.0
    auto_input_snapshot_path: str = "/tmp/auto-input.json"
    gateway_health_path: str = ""
    gateway_pressure_policy: GatewayPressurePolicy | None = field(
        default_factory=FreshOkGatewayPressurePolicy
    )
    virtual_mode: int = 1
    now: float = 100.0
    _auto_input_helper_generation: int = 0
    _auto_input_helper_last_start_at: float = 0.0
    _auto_input_helper_process: HelperProcess | None = None
    _auto_input_helper_restart_requested_at: float | None = None
    _auto_input_runtime_instance_id: str = "instance-1"
    _auto_input_snapshot_generation: int | None = None
    _auto_input_snapshot_last_captured_at: float | None = None
    _auto_input_snapshot_last_seen: float | None = None
    _auto_input_snapshot_mtime_ns: int | None = None
    _auto_input_snapshot_runtime_instance_id: str | None = None
    _auto_input_snapshot_seen_for_current_helper: bool = False
    _auto_input_snapshot_version: int | None = None
    _auto_input_snapshot_writer_pid: int | None = None

    def time_now(self) -> float:
        return self.now


def valid_snapshot(**overrides: object) -> dict[str, object]:
    """Build one complete, current helper snapshot."""
    payload: dict[str, object] = {
        "snapshot_version": AutoInputSupervisor.SCHEMA.version,
        "captured_at": 100.0,
        "heartbeat_at": 100.0,
        "pv_captured_at": 100.0,
        "pv_power": 2300.0,
        "battery_captured_at": 100.0,
        "battery_soc": 57.0,
        "grid_captured_at": 100.0,
        "grid_power": -2100.0,
        "writer_pid": 4321,
        "helper_generation": 1,
        "runtime_instance_id": "instance-1",
    }
    payload.update(overrides)
    return payload
