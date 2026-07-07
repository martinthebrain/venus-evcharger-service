# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual runtime-state initialization helpers for service bootstrap."""

from __future__ import annotations

from collections import deque
from typing import Any

from venus_evcharger.backend.errors import BACKEND_OPTIONAL_CAPABILITY_ERRORS
from venus_evcharger.backend.models import (
    PhaseSelection,
    normalize_phase_selection_or_none,
    normalize_phase_selection_tuple,
)

_DEFAULT_PHASE_SELECTIONS: tuple[PhaseSelection, ...] = ("P1",)


def switch_backend_supported_phase_selections(svc: Any) -> tuple[PhaseSelection, ...]:
    """Return normalized supported phase selections declared by the current switch backend."""
    backend = getattr(svc, "_switch_backend", None)
    capabilities_method = getattr(backend, "capabilities", None)
    if not callable(capabilities_method):
        return ("P1",)
    try:
        capabilities = capabilities_method()
    except BACKEND_OPTIONAL_CAPABILITY_ERRORS:
        return _DEFAULT_PHASE_SELECTIONS
    supported = getattr(capabilities, "supported_phase_selections", None)
    if supported is None:
        return _DEFAULT_PHASE_SELECTIONS
    return normalize_phase_selection_tuple(supported)


def charger_backend_supported_phase_selections(svc: Any) -> tuple[PhaseSelection, ...]:
    """Return normalized supported phase selections declared by the current charger backend."""
    backend = getattr(svc, "_charger_backend", None)
    settings = getattr(backend, "settings", None)
    supported = getattr(settings, "supported_phase_selections", None)
    if supported is None:
        return _DEFAULT_PHASE_SELECTIONS
    return normalize_phase_selection_tuple(supported)


def supported_phase_selections_for_service(svc: Any) -> tuple[PhaseSelection, ...]:
    """Return the effective phase-selection domain for the current backend topology."""
    if getattr(svc, "_switch_backend", None) is None and getattr(svc, "_charger_backend", None) is not None:
        return charger_backend_supported_phase_selections(svc)
    return switch_backend_supported_phase_selections(svc)


def configured_phase_selection(defaults: Any, supported_phase_selections: tuple[PhaseSelection, ...]) -> PhaseSelection:
    """Return the configured phase selection constrained to the backend-supported domain."""
    raw_phase_selection = defaults.get("PhaseSelection")
    if raw_phase_selection is None:
        return supported_phase_selections[0]
    normalized = normalize_phase_selection_or_none(raw_phase_selection)
    if normalized in supported_phase_selections:
        return normalized
    return supported_phase_selections[0]


def initialize_virtual_state(svc: Any, normalize_mode: Any) -> None:
    """Initialize the writable EV charger state exposed on DBus."""
    defaults = svc.config["DEFAULT"]
    supported_phase_selections = supported_phase_selections_for_service(svc)
    svc.manual_override_until = 0.0
    svc.virtual_mode = normalize_mode(defaults.get("Mode", "0"))
    svc.virtual_autostart = int(defaults.get("AutoStart", "1"))
    svc.virtual_startstop = int(defaults.get("StartStop", "1"))
    svc.virtual_enable = int(defaults.get("Enable", defaults.get("StartStop", "1")))
    svc.virtual_set_current = float(defaults.get("SetCurrent", svc.max_current))
    reset_transient_session_state(svc)
    reset_learning_state(svc)
    svc.relay_last_changed_at = None
    svc.relay_last_off_at = None
    svc.supported_phase_selections = supported_phase_selections
    phase_selection = configured_phase_selection(defaults, supported_phase_selections)
    svc.requested_phase_selection = phase_selection
    svc.active_phase_selection = phase_selection
    svc._grid_recovery_required = False
    svc._grid_recovery_since = None
    svc._auto_mode_cutover_pending = False
    svc._ignore_min_offtime_once = False


def reset_transient_session_state(svc: Any) -> None:
    """Reset transient session and Auto-cycle state."""
    svc.charging_started_at = None
    svc.energy_at_start = 0.0
    svc.last_status = 0
    svc.auto_start_condition_since = None
    svc.auto_stop_condition_since = None
    svc.auto_stop_condition_reason = None
    svc.auto_samples = deque()
    svc._auto_high_soc_profile_active = None
    svc._stop_smoothed_surplus_power = None
    svc._stop_smoothed_grid_power = None


def reset_learning_state(svc: Any) -> None:
    """Reset charge-power learning state for a fresh process lifetime."""
    svc.learned_charge_power_watts = None
    svc.learned_charge_power_updated_at = None
    svc.learned_charge_power_state = "unknown"
    svc.learned_charge_power_learning_since = None
    svc.learned_charge_power_sample_count = 0
    svc.learned_charge_power_phase = None
    svc.learned_charge_power_voltage = None
    svc.learned_charge_power_signature_mismatch_sessions = 0
    svc.learned_charge_power_signature_checked_session_started_at = None
