# SPDX-License-Identifier: GPL-3.0-or-later
"""Warning specifications emitted by the runtime update coordinator."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.backend.models import ChargerState, SwitchState


class RuntimeWarningServicePort(Protocol):
    _last_charger_transport_source: str | None
    _last_charger_transport_detail: str | None
    _contactor_fault_counts: dict[str, int]
    _contactor_lockout_source: str


WarningSpec = tuple[str, str, tuple[object, ...]]


def blocking_charger_health_warning_spec(
    svc: RuntimeWarningServicePort,
    charger_health: str,
    charger_state: ChargerState | None,
) -> WarningSpec:
    """Return warning metadata for one blocking charger-health reason."""
    if charger_health.startswith("charger-transport-"):
        return (
            "charger-transport-blocking",
            "Native charger transport override %s blocks charging (source=%s detail=%s)",
            (
                charger_health,
                svc._last_charger_transport_source,
                svc._last_charger_transport_detail,
            ),
        )
    return (
        "charger-health-blocking",
        "Native charger health override %s blocks charging (status=%s fault=%s)",
        (
            charger_health,
            None if charger_state is None else charger_state.status_text,
            None if charger_state is None else charger_state.fault_text,
        ),
    )


def switch_feedback_warning_spec(
    switch_health: str,
    desired_relay: bool,
    relay_on: bool,
    power: float,
    current: float,
    svc: RuntimeWarningServicePort,
    charger_state: ChargerState | None,
    switch_state: SwitchState | None,
) -> WarningSpec:
    """Return warning metadata for one blocking switch-feedback health reason."""
    specs: dict[str, WarningSpec] = {
        "contactor-interlock": (
            "switch-interlock-blocking",
            "Switch interlock blocks charging (desired=%s relay=%s interlock_ok=%s)",
            (
                int(bool(desired_relay)),
                int(bool(relay_on)),
                None if switch_state is None else switch_state.interlock_ok,
            ),
        ),
        "contactor-suspected-open": (
            "switch-suspected-open-blocking",
            "Contactor heuristics suspect OPEN state (relay=%s power=%.1f current=%.1f charger_status=%s)",
            (
                int(bool(relay_on)),
                float(power),
                float(current),
                None if charger_state is None else charger_state.status_text,
            ),
        ),
        "contactor-suspected-welded": (
            "switch-suspected-welded-blocking",
            "Contactor heuristics suspect WELDED state (relay=%s power=%.1f current=%.1f)",
            (int(bool(relay_on)), float(power), float(current)),
        ),
        "contactor-lockout-open": (
            "switch-lockout-open-blocking",
            "Latched contactor OPEN lockout blocks charging (count=%s source=%s)",
            (
                svc._contactor_fault_counts.get("contactor-suspected-open", 0),
                svc._contactor_lockout_source,
            ),
        ),
        "contactor-lockout-welded": (
            "switch-lockout-welded-blocking",
            "Latched contactor WELDED lockout blocks charging (count=%s source=%s)",
            (
                svc._contactor_fault_counts.get("contactor-suspected-welded", 0),
                svc._contactor_lockout_source,
            ),
        ),
    }
    return specs.get(
        switch_health,
        (
            "switch-feedback-blocking",
            "Switch feedback mismatch blocks charging (relay=%s feedback_closed=%s)",
            (
                int(bool(relay_on)),
                None if switch_state is None else switch_state.feedback_closed,
            ),
        ),
    )


__all__ = [
    "RuntimeWarningServicePort",
    "WarningSpec",
    "blocking_charger_health_warning_spec",
    "switch_feedback_warning_spec",
]
