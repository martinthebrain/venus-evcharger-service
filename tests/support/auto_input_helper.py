# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed component doubles for auto-input helper contract tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings, load_auto_input_helper_settings
from venus_evcharger.inputs.helper.contracts import Snapshot
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergyRefreshScope,
    EnergyTopologySnapshot,
    MeasuredValue,
)

ROOT = Path(__file__).resolve().parents[2]


def run_callback(callback: Callable[[], object]) -> object:
    return callback()


def helper_settings(*, parent_pid: int | None = None) -> AutoInputHelperSettings:
    return load_auto_input_helper_settings(
        str(ROOT / "deploy/venus/config.venus_evcharger.ini"),
        "/tmp/auto-input-helper-test.json",
        parent_pid,
        3,
        "test-instance",
    )


class FakeGatewayClient:
    def __init__(self) -> None:
        self.inputs: EnergyInputsSnapshot | None = None
        self.topology: EnergyTopologySnapshot | None = None
        self.refresh_requests: list[tuple[EnergyRefreshRequest, str]] = []
        self.error: Exception | None = None

    def load_energy_inputs(self, *, max_age_seconds: float) -> EnergyInputsSnapshot | None:
        del max_age_seconds
        return self.inputs

    def load_energy_topology(self, *, max_age_seconds: float) -> EnergyTopologySnapshot | None:
        del max_age_seconds
        return self.topology

    def request_energy_refresh(self, request: EnergyRefreshRequest, *, source: str) -> str:
        if self.error is not None:
            raise self.error
        self.refresh_requests.append((request, source))
        return f"command-{len(self.refresh_requests)}"


class FakeEnergyGateway:
    def __init__(self) -> None:
        self.inputs: EnergyInputsSnapshot | None = None
        self.topology: EnergyTopologySnapshot | None = None
        self.measurements: dict[EnergyRefreshScope, MeasuredValue | None] = {}
        self.requests: list[tuple[EnergyRefreshScope, str, bool]] = []
        self.input_refreshes = 0
        self.topology_refreshes = 0
        self.reset_calls = 0

    def refresh_inputs(self) -> EnergyInputsSnapshot | None:
        self.input_refreshes += 1
        return self.inputs

    def refresh_topology(self) -> EnergyTopologySnapshot | None:
        self.topology_refreshes += 1
        return self.topology

    def measurement(self, scope: EnergyRefreshScope) -> MeasuredValue | None:
        return self.measurements.get(scope)

    def request_refresh(
        self,
        scope: EnergyRefreshScope,
        *,
        reason: str,
        priority: bool = False,
    ) -> bool:
        self.requests.append((scope, reason, priority))
        return True

    def reset(self) -> None:
        self.reset_calls += 1


class FakeSources:
    def __init__(self) -> None:
        self.pv: float | None = 100.0
        self.battery: Snapshot = {"battery_soc": 50.0}
        self.grid: float | None = -20.0
        self.prepared = 0
        self.observed: dict[str, float | None] = {}

    def prepare_cycle(self) -> None:
        self.prepared += 1

    def observed_at(self, source_name: str) -> float | None:
        return self.observed.get(source_name)

    def pv_power(self) -> float | None:
        return self.pv

    def battery_snapshot(self) -> Snapshot:
        return dict(self.battery)

    def grid_power(self) -> float | None:
        return self.grid


class MemoryWriter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def write(self, payload: Mapping[str, object]) -> None:
        self.payloads.append(dict(payload))


class FakeSnapshots:
    def __init__(self) -> None:
        self.refreshed: list[tuple[str, float | None]] = []
        self.refresh_all_calls = 0
        self.lifecycle: list[str] = []
        self.heartbeat_calls = 0
        self.refresh_error: Exception | None = None
        self.heartbeat_error: Exception | None = None

    def poll(self) -> bool:
        return True

    def refresh_source(self, source_name: str, now: float | None = None) -> None:
        if self.refresh_error is not None:
            raise self.refresh_error
        self.refreshed.append((source_name, now))

    def refresh_all(self, now: float | None = None) -> None:
        del now
        self.refresh_all_calls += 1

    def validation_poll(self) -> bool:
        return True

    def heartbeat(self) -> bool:
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        self.heartbeat_calls += 1
        return True

    def write_lifecycle(self, state: str, now: float | None = None) -> None:
        del now
        self.lifecycle.append(state)


class FakeLoop:
    def __init__(self) -> None:
        self.run_calls = 0
        self.quit_calls = 0

    def run(self) -> None:
        self.run_calls += 1

    def quit(self) -> None:
        self.quit_calls += 1
