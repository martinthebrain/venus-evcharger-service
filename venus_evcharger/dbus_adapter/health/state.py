#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable operational and performance verdicts for gateway health."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GatewayHealthState = Literal["ok", "degraded", "protective"]

_STATE_RANK: dict[GatewayHealthState, int] = {
    "ok": 0,
    "degraded": 1,
    "protective": 2,
}
_PROTECTIVE_PERFORMANCE_SIGNALS = frozenset(
    {
        ("backpressure", "protective"),
        ("resource", "constrained"),
    }
)
_DEGRADED_PERFORMANCE_SIGNALS = frozenset(
    {
        ("slo", "violated"),
        ("resource", "busy"),
        ("backpressure", "congested"),
        ("backpressure", "slow"),
    }
)


@dataclass(frozen=True, slots=True)
class AggregatedHealthState:
    """One stable aggregate verdict and its transition metadata."""

    state: GatewayHealthState
    changed_at: float
    recovery_pending: bool


class GatewayHealthStateLatch:
    """Escalate immediately and delay recovery until health is stable."""

    def __init__(self, *, recovery_hold_seconds: float = 10.0) -> None:
        self._recovery_hold_seconds = max(0.0, recovery_hold_seconds)
        self._state: GatewayHealthState = "ok"
        self._changed_at = 0.0
        self._recovery: tuple[GatewayHealthState, float] | None = None

    @property
    def recovery_pending(self) -> bool:
        """Return whether a lower-severity state is awaiting confirmation."""
        return self._recovery is not None

    def observe(
        self,
        operational_state: GatewayHealthState,
        performance_state: GatewayHealthState,
        *,
        monotonic_at: float,
        captured_at: float,
    ) -> AggregatedHealthState:
        """Observe component verdicts and return the latched aggregate."""
        desired = higher_health_state(operational_state, performance_state)
        if _STATE_RANK[desired] >= _STATE_RANK[self._state]:
            self._apply_immediate(desired, captured_at)
        else:
            self._recover_if_stable(
                desired,
                monotonic_at=max(0.0, monotonic_at),
                captured_at=captured_at,
            )
        return AggregatedHealthState(
            self._state,
            self._changed_at,
            self.recovery_pending,
        )

    def _apply_immediate(
        self,
        desired: GatewayHealthState,
        captured_at: float,
    ) -> None:
        if desired != self._state:
            self._state = desired
            self._changed_at = max(0.0, captured_at)
        self._reset_recovery()

    def _recover_if_stable(
        self,
        desired: GatewayHealthState,
        *,
        monotonic_at: float,
        captured_at: float,
    ) -> None:
        recovery = self._recovery
        if recovery is None or desired != recovery[0]:
            recovery = (desired, monotonic_at)
            self._recovery = recovery
        if (
            monotonic_at - recovery[1]
            < self._recovery_hold_seconds
        ):
            return
        self._state = desired
        self._changed_at = max(0.0, captured_at)
        self._reset_recovery()

    def _reset_recovery(self) -> None:
        self._recovery = None


def operational_health_state(value: str) -> GatewayHealthState:
    """Normalize the circuit-breaker state for public health output."""
    if value == "protective":
        return "protective"
    if value == "degraded":
        return "degraded"
    return "ok"


def performance_health_state(
    *,
    slo_state: str,
    resource_state: str,
    backpressure_state: str,
) -> GatewayHealthState:
    """Derive a bounded performance verdict from existing observations."""
    signals = {
        ("slo", slo_state),
        ("resource", resource_state),
        ("backpressure", backpressure_state),
    }
    if signals & _PROTECTIVE_PERFORMANCE_SIGNALS:
        return "protective"
    if signals & _DEGRADED_PERFORMANCE_SIGNALS:
        return "degraded"
    return "ok"


def higher_health_state(
    left: GatewayHealthState,
    right: GatewayHealthState,
) -> GatewayHealthState:
    """Return the more severe of two normalized health states."""
    return max((left, right), key=_STATE_RANK.__getitem__)


__all__ = [
    "AggregatedHealthState",
    "GatewayHealthState",
    "GatewayHealthStateLatch",
    "higher_health_state",
    "operational_health_state",
    "performance_health_state",
]
