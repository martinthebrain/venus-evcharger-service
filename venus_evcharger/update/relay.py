# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed relay/update-cycle helpers for the Venus EV charger service."""

from __future__ import annotations

from venus_evcharger.backend.models import normalize_phase_selection
from venus_evcharger.update.relay_phase_decision import _RelayPhaseDecision


class _UpdateCycleRelay(_RelayPhaseDecision):
    """Composed update-cycle relay helpers.

    The update-cycle logic is intentionally split into focused helper modules:
    phase target selection, phase-switch orchestration, charger health/current
    handling, relay confirmation, and outward status publishing.
    """

    PHASE_SWITCH_WAITING_STATE = "waiting-relay-off"
    PHASE_SWITCH_STABILIZING_STATE = "stabilizing"
    CHARGER_FAULT_HINT_TOKENS = frozenset(
        {"fault", "error", "failed", "failure", "alarm", "offline", "unavailable", "lockout", "tripped"}
    )
    CHARGER_STATUS_CHARGING_HINT_TOKENS = frozenset({"charging"})
    CHARGER_STATUS_READY_HINT_TOKENS = frozenset({"ready", "connected", "available", "idle"})
    CHARGER_STATUS_WAITING_HINT_TOKENS = frozenset({"paused", "waiting", "suspended", "sleeping"})
    CHARGER_STATUS_FINISHED_HINT_TOKENS = frozenset({"complete", "completed", "finished", "done"})


__all__ = ["_UpdateCycleRelay", "normalize_phase_selection"]
