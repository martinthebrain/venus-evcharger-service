# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow state and runtime ports for relay and phase-switch components."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.backend.models import PhaseSelection
from venus_evcharger.update.readback_resolver import FreshReadbacks
from venus_evcharger.update.relay_phase_publish import RelayTelemetryRuntimePort, RelayTelemetryService


class PhaseSwitchAutoPort(Protocol):
    def set_health(self, reason: str, *, cached: bool) -> None: ...


class PhaseSwitchReadbackPort(Protocol):
    def resolve(self, now: float | None = None) -> FreshReadbacks: ...


class PhaseSwitchRuntimePort(Protocol):
    """Runtime side effects required by physical phase switching."""

    def apply_phase_selection(self, selection: PhaseSelection) -> PhaseSelection: ...
    def mark_failure(self, source_key: str) -> None: ...
    def mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...
    def pending_relay_command(self) -> tuple[bool | None, float | None]: ...
    def phase_selection_requires_pause(self) -> bool: ...
    def queue_relay_command(self, relay_on: bool, current_time: float) -> object: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...


class PhaseSwitchStatePort(Protocol):
    """Persistent state side effect required after phase changes."""

    def save_runtime_state(self) -> object: ...


class PhaseSwitchCombinedRuntimePort(PhaseSwitchRuntimePort, RelayTelemetryRuntimePort, Protocol):
    """Combined runtime effects used by phase switching and relay confirmation."""


class PhaseSwitchServicePort(RelayTelemetryService, Protocol):
    """Canonical mutable phase-switch state owned by the service."""

    @property
    def runtime(self) -> PhaseSwitchCombinedRuntimePort: ...

    @property
    def state(self) -> PhaseSwitchStatePort: ...

    @property
    def auto(self) -> PhaseSwitchAutoPort: ...

    @property
    def _readback_resolver(self) -> PhaseSwitchReadbackPort: ...

    auto_policy: AutoPolicy
    requested_phase_selection: PhaseSelection
    active_phase_selection: PhaseSelection
    supported_phase_selections: tuple[str, ...]
    auto_shelly_soft_fail_seconds: float
    min_current: float
    voltage_mode: str
    _last_auto_metrics: dict[str, object]
    _auto_phase_target_candidate: PhaseSelection | None
    _auto_phase_target_since: float | None
    _phase_switch_pending_selection: PhaseSelection | None
    _phase_switch_state: str | None
    _phase_switch_requested_at: float | None
    _phase_switch_stable_until: float | None
    _phase_switch_resume_relay: bool
    _phase_switch_mismatch_active: bool
    _phase_switch_mismatch_counts: dict[str, int]
    _phase_switch_last_mismatch_selection: str | None
    _phase_switch_last_mismatch_at: float | None
    _phase_switch_lockout_selection: str | None
    _phase_switch_lockout_reason: str
    _phase_switch_lockout_at: float | None
    _phase_switch_lockout_until: float | None
    _ignore_min_offtime_once: bool
    _charger_target_current_amps: float | None
    _charger_target_current_applied_at: float | None


__all__ = [
    "PhaseSwitchAutoPort",
    "PhaseSwitchReadbackPort",
    "PhaseSwitchRuntimePort",
    "PhaseSwitchServicePort",
    "PhaseSwitchStatePort",
]
