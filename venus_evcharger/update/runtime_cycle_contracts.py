# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed ports shared by the composed update-cycle components."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.ports.readback import ReadbackStore
from venus_evcharger.update.readback_resolver import FreshReadbacks
from venus_evcharger.update.input_cache import InputCacheService
from venus_evcharger.update.learning_runtime import _LearningRuntimeService
from venus_evcharger.update.offline_publish import OfflineAutoPort, OfflineService, OfflineStatePort
from venus_evcharger.update.pm_snapshot import PmSnapshotService
from venus_evcharger.update.relay_charger_current import ChargerControlService, ChargerRuntimePort
from venus_evcharger.update.relay_charger_health import ChargerHealthService
from venus_evcharger.update.relay_phase_publish import RelayTelemetryRuntimePort
from venus_evcharger.update.relay_ports import (
    PhaseSwitchAutoPort,
    PhaseSwitchReadbackPort,
    PhaseSwitchRuntimePort,
    PhaseSwitchServicePort,
    PhaseSwitchStatePort,
)
from venus_evcharger.update.relay_status_publish import (
    RelayStatusService,
    StatusReadbackPort,
    StatusRuntimePort,
)
from venus_evcharger.update.runtime_cycle_warnings import RuntimeWarningServicePort
from venus_evcharger.update.state import (
    StateAutoPort,
    StatePublishPort,
    StateReadbackPort,
    StateRuntimePort,
    UpdateStateService,
)


class UpdateCycleAutoPort(StateAutoPort, PhaseSwitchAutoPort, OfflineAutoPort, Protocol):
    """Auto-policy operations consumed during one update pass."""

    def decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> bool: ...


class UpdateCycleRuntimePort(
    StateRuntimePort,
    PhaseSwitchRuntimePort,
    ChargerRuntimePort,
    RelayTelemetryRuntimePort,
    StatusRuntimePort,
    Protocol,
):
    """Runtime side effects required by the composed update cycle."""


class UpdateCycleStatePort(StatePublishPort, PhaseSwitchStatePort, OfflineStatePort, Protocol):
    """State persistence and publication required by the update cycle."""

    def flush_runtime_overrides(self, now: float | None = None) -> None: ...
    def summary(self) -> str: ...
    def publish_companion_bridge(self, now: float | None = None) -> bool: ...
    def last_accepted_field(self, field: str) -> object: ...


class UpdateCycleReadbackPort(
    StateReadbackPort,
    PhaseSwitchReadbackPort,
    StatusReadbackPort,
    Protocol,
):
    """Fresh atomic charger and switch readbacks."""

    def resolve(self, now: float | None = None) -> FreshReadbacks: ...


class UpdateCycleServicePort(
    UpdateStateService,
    InputCacheService,
    PmSnapshotService,
    OfflineService,
    PhaseSwitchServicePort,
    RelayStatusService,
    ChargerControlService,
    ChargerHealthService,
    _LearningRuntimeService,
    RuntimeWarningServicePort,
    Protocol,
):
    """Canonical service boundary used by the update composition root."""

    @property
    def auto(self) -> UpdateCycleAutoPort: ...

    @property
    def runtime(self) -> UpdateCycleRuntimePort: ...

    @property
    def state(self) -> UpdateCycleStatePort: ...

    _readback_store: ReadbackStore

    @property
    def _readback_resolver(self) -> UpdateCycleReadbackPort: ...

    @_readback_resolver.setter
    def _readback_resolver(self, value: UpdateCycleReadbackPort) -> None: ...


__all__ = [
    "UpdateCycleAutoPort",
    "UpdateCycleReadbackPort",
    "UpdateCycleRuntimePort",
    "UpdateCycleServicePort",
    "UpdateCycleStatePort",
]
