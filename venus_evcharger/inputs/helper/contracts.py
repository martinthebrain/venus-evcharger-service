# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow contracts between auto-input helper components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergyRefreshScope,
    EnergyTopologySnapshot,
    MeasuredValue,
)
from venus_evcharger.ipc.enqueue_result import GatewayEnqueueResult

Snapshot: TypeAlias = dict[str, object]
EnergyMeasurementKey = Literal[
    "pv",
    "grid",
    "battery",
    "battery_power",
    "battery_capacity_wh",
    "battery_capacity_ah",
    "battery_voltage",
]


@runtime_checkable
class MainLoopPort(Protocol):  # pragma: no cover
    def run(self) -> None: ...

    def quit(self) -> None: ...


class EnergyGatewayClientPort(Protocol):  # pragma: no cover
    def load_energy_inputs(self, *, max_age_seconds: float) -> EnergyInputsSnapshot | None: ...

    def load_energy_topology(self, *, max_age_seconds: float) -> EnergyTopologySnapshot | None: ...

    def request_energy_refresh(
        self,
        request: EnergyRefreshRequest,
        *,
        source: str,
    ) -> GatewayEnqueueResult: ...


class EnergySnapshotReaderPort(Protocol):  # pragma: no cover
    def refresh_inputs(self) -> EnergyInputsSnapshot | None: ...

    def refresh_topology(self) -> EnergyTopologySnapshot | None: ...

    def measurement(self, key: EnergyMeasurementKey) -> MeasuredValue | None: ...

    def request_refresh(
        self,
        scope: EnergyRefreshScope,
        *,
        reason: str,
        priority: bool = False,
    ) -> bool: ...

    def reset(self) -> None: ...


class SourceReaderPort(Protocol):  # pragma: no cover
    def prepare_cycle(self) -> None: ...

    def observed_at(self, source_name: str) -> float | None: ...

    def observed_monotonic(self, source_name: str) -> float | None: ...

    def pv_power(self) -> float | None: ...

    def battery_snapshot(self) -> Snapshot: ...

    def grid_power(self) -> float | None: ...


class SnapshotPort(Protocol):  # pragma: no cover
    def poll(self) -> bool: ...

    def refresh_source(self, source_name: str, now: float | None = None) -> None: ...

    def refresh_all(self, now: float | None = None) -> None: ...

    def heartbeat(self) -> bool: ...

    def write_lifecycle(self, state: str, now: float | None = None) -> None: ...


class RefreshCoordinatorPort(Protocol):  # pragma: no cover
    def refresh(self) -> bool: ...

    def timer_tick(self) -> bool: ...

    def reset(self) -> None: ...


class SnapshotWriterPort(Protocol):  # pragma: no cover
    def write(self, payload: Mapping[str, object]) -> None: ...
