# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized backend-facing result and selection types for the wallbox service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Literal, cast

PhaseSelection = Literal["P1", "P1_P2", "P1_P2_P3"]
SwitchingMode = Literal["direct", "contactor"]
BackendMode = Literal["combined", "split"]


def phase_selection_count(value: object) -> int:
    """Return how many energized phases one normalized phase selection represents."""
    normalized = normalize_phase_selection(value, "P1")
    if normalized == "P1_P2_P3":
        return 3
    if normalized == "P1_P2":
        return 2
    return 1


def normalize_phase_selection(value: object, default: PhaseSelection = "P1") -> PhaseSelection:
    """Return one normalized phase-selection value."""
    selection = str(value).strip().upper() if value is not None else ""
    if selection in {"P1", "L1", "L2", "L3", "1P"}:
        return "P1"
    if selection in {"P1_P2", "P1+P2", "2P"}:
        return "P1_P2"
    if selection in {"P1_P2_P3", "P1+P2+P3", "3P"}:
        return "P1_P2_P3"
    return default


def normalize_switching_mode(value: object, default: SwitchingMode = "direct") -> SwitchingMode:
    """Return one normalized switch-backend switching mode."""
    mode = str(value).strip().lower() if value is not None else ""
    if mode == "contactor":
        return "contactor"
    if mode == "direct":
        return "direct"
    return default


def normalize_phase_selection_tuple(
    values: object,
    default: tuple[PhaseSelection, ...] = ("P1",),
) -> tuple[PhaseSelection, ...]:
    """Return one normalized supported-phase tuple from arbitrary runtime data."""
    normalized = _normalized_phase_selection_values(values)
    return normalized or default


def _normalized_phase_selection_values(values: object) -> tuple[PhaseSelection, ...]:
    """Return normalized phase selections from iterable or comma-separated runtime data."""
    if isinstance(values, (tuple, list)):
        return cast(tuple[PhaseSelection, ...], tuple(normalize_phase_selection(value, "P1") for value in values))
    text = _phase_selection_text(values)
    if not text:
        return ()
    return cast(tuple[PhaseSelection, ...], tuple(normalize_phase_selection(part, "P1") for part in text.split(",")))


def _phase_selection_text(values: object) -> str:
    """Return a trimmed phase-selection text payload."""
    return str(values).strip() if values is not None else ""


def phase_switch_lockout_active(
    lockout_selection: object,
    lockout_until: object,
    *,
    now: float | None = None,
) -> bool:
    """Return whether one phase-switch lockout is currently active."""
    if not _phase_selection_text(lockout_selection):
        return False
    if not isinstance(lockout_until, (int, float, str)):
        return False
    try:
        normalized_until = float(lockout_until)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else float(now)
    return current < normalized_until


def effective_supported_phase_selections(
    values: object,
    *,
    lockout_selection: object = None,
    lockout_until: object = None,
    now: float | None = None,
) -> tuple[PhaseSelection, ...]:
    """Return the effective supported phase layouts after lockout degradation."""
    normalized = normalize_phase_selection_tuple(values, ("P1",))
    if not phase_switch_lockout_active(lockout_selection, lockout_until, now=now):
        return normalized
    allowed_phase_count = max(1, phase_selection_count(lockout_selection) - 1)
    filtered = cast(
        tuple[PhaseSelection, ...],
        tuple(selection for selection in normalized if phase_selection_count(selection) <= allowed_phase_count),
    )
    return filtered or normalized


def switch_feedback_mismatch(enabled: object, feedback_closed: object) -> bool:
    """Return whether command state and explicit switch feedback disagree."""
    if feedback_closed is None:
        return False
    return bool(enabled) != bool(feedback_closed)


@dataclass(frozen=True)
class MeterReading:
    """Normalized meter reading returned by one meter backend."""

    relay_on: bool | None
    power_w: float
    voltage_v: float | None
    current_a: float | None
    energy_kwh: float
    phase_selection: PhaseSelection
    phase_powers_w: tuple[float, float, float] | None = None
    phase_currents_a: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SwitchCapabilities:
    """Declarative switch-backend capabilities consumed by wallbox core logic."""

    switching_mode: SwitchingMode
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None


@dataclass(frozen=True)
class SwitchState:
    """Normalized switch state exposed by one switch backend."""

    enabled: bool
    phase_selection: PhaseSelection
    feedback_closed: bool | None = None
    interlock_ok: bool | None = None


@dataclass(frozen=True)
class ChargerState:
    """Normalized charger state exposed by one charger backend."""

    enabled: bool | None
    current_amps: float | None
    phase_selection: PhaseSelection | None
    actual_current_amps: float | None = None
    power_w: float | None = None
    energy_kwh: float | None = None
    status_text: str | None = None
    fault_text: str | None = None


@dataclass(frozen=True)
class BackendRuntimeSummary:
    """Normalized runtime-facing backend summary for one configured load topology."""

    backend_mode: BackendMode
    meter_type: str | None
    meter_config_path: Path | None
    switch_type: str | None
    switch_config_path: Path | None
    charger_type: str | None
    charger_config_path: Path | None
    topology_configured: bool
    primary_rpc_configured: bool
