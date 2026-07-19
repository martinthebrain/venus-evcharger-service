# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit composition root for relay and phase-switch components."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.update.relay_charger_current import ChargerTargetController
from venus_evcharger.update.relay_charger_current_targets import ChargerCurrentTargetPolicy
from venus_evcharger.update.relay_charger_health import ChargerHealthMonitor
from venus_evcharger.update.relay_charger_readback import ChargerBackendAccess
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker
from venus_evcharger.update.relay_phase_decision import AutoPhaseTargetSelector
from venus_evcharger.update.relay_phase_publish import RelayTelemetry
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_phase_switch_policy import AutoPhaseSwitchController
from venus_evcharger.update.relay_phase_switch_runtime import PhaseSwitchCoordinator
from venus_evcharger.update.relay_phase_switch_runtime_recovery import PhaseSwitchRecovery
from venus_evcharger.update.relay_status_publish import RelayStatusPublisher, VirtualStatePublisher


PHASE_SWITCH_WAITING_STATE = "waiting-relay-off"
PHASE_SWITCH_STABILIZING_STATE = "stabilizing"
CHARGER_FAULT_HINT_TOKENS = frozenset(
    {"fault", "error", "failed", "failure", "alarm", "offline", "unavailable", "lockout", "tripped"}
)
CHARGER_STATUS_CHARGING_HINT_TOKENS = frozenset({"charging"})
CHARGER_STATUS_READY_HINT_TOKENS = frozenset({"ready", "connected", "available", "idle"})
CHARGER_STATUS_WAITING_HINT_TOKENS = frozenset({"paused", "waiting", "suspended", "sleeping"})
CHARGER_STATUS_FINISHED_HINT_TOKENS = frozenset({"complete", "completed", "finished", "done"})


@dataclass(frozen=True, slots=True)
class RelayFoundation:
    """Relay components that do not depend on virtual-state publishing."""

    backends: ChargerBackendAccess
    transport: ChargerTransportTracker
    current_policy: ChargerCurrentTargetPolicy
    targets: ChargerTargetController
    health: ChargerHealthMonitor
    telemetry: RelayTelemetry
    mismatch: PhaseSwitchMismatchMonitor
    selector: AutoPhaseTargetSelector
    recovery: PhaseSwitchRecovery
    phase_switch: PhaseSwitchCoordinator
    auto_phase: AutoPhaseSwitchController


@dataclass(frozen=True, slots=True)
class RelayComponents:
    """Complete relay object graph used by one update-cycle controller."""

    foundation: RelayFoundation
    status: RelayStatusPublisher


def build_relay_foundation(
    phase_values: Callable[[float, float, object, object], object],
) -> RelayFoundation:
    """Build the acyclic relay foundation exactly once."""
    backends = ChargerBackendAccess(CHARGER_FAULT_HINT_TOKENS)
    transport = ChargerTransportTracker()
    current_policy = ChargerCurrentTargetPolicy(backends)
    targets = ChargerTargetController(backends, current_policy, transport)
    health = ChargerHealthMonitor(
        backends,
        transport,
        charging_tokens=CHARGER_STATUS_CHARGING_HINT_TOKENS,
        ready_tokens=CHARGER_STATUS_READY_HINT_TOKENS,
        waiting_tokens=CHARGER_STATUS_WAITING_HINT_TOKENS,
        finished_tokens=CHARGER_STATUS_FINISHED_HINT_TOKENS,
    )
    telemetry = RelayTelemetry(phase_values)
    mismatch = PhaseSwitchMismatchMonitor()
    selector = AutoPhaseTargetSelector(mismatch, telemetry.phase_voltage)
    recovery = PhaseSwitchRecovery(mismatch, targets, health, telemetry)
    phase_switch = PhaseSwitchCoordinator(
        recovery,
        mismatch,
        waiting_state=PHASE_SWITCH_WAITING_STATE,
        stabilizing_state=PHASE_SWITCH_STABILIZING_STATE,
    )
    auto_phase = AutoPhaseSwitchController(
        selector,
        mismatch,
        phase_switch,
        telemetry,
        waiting_state=PHASE_SWITCH_WAITING_STATE,
    )
    return RelayFoundation(
        backends=backends,
        transport=transport,
        current_policy=current_policy,
        targets=targets,
        health=health,
        telemetry=telemetry,
        mismatch=mismatch,
        selector=selector,
        recovery=recovery,
        phase_switch=phase_switch,
        auto_phase=auto_phase,
    )


def complete_relay_components(
    foundation: RelayFoundation,
    virtual_state: VirtualStatePublisher,
) -> RelayComponents:
    """Attach status publishing after the state component exists."""
    status = RelayStatusPublisher(
        foundation.telemetry,
        foundation.targets,
        foundation.health,
        foundation.transport,
        virtual_state,
    )
    return RelayComponents(foundation=foundation, status=status)


__all__ = [
    "RelayComponents",
    "RelayFoundation",
    "build_relay_foundation",
    "complete_relay_components",
]
