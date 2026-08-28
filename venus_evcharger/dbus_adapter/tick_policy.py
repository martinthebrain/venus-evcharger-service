# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure adaptive-tick policy for the DBus gateway."""

from __future__ import annotations

from dataclasses import dataclass

SLO_BUDGET_FRACTION = 0.8


@dataclass(frozen=True, slots=True)
class TickPolicy:
    """Define gateway cadence bounds and critical-work deadlines."""

    min_tick_seconds: float
    max_tick_seconds: float
    core_read_slo_seconds: float
    queue_slo_seconds: float


@dataclass(frozen=True, slots=True)
class TickDemand:
    """Summarize critical work remaining in the current health snapshot."""

    critical_read_operations: int = 0
    critical_queue_operations: int = 0
    core_read_age_seconds: float = 0.0
    queue_age_seconds: float = 0.0
    operation_p95_ms: float = 0.0

    @property
    def operation_count(self) -> int:
        """Return the bounded amount of critical work to schedule."""
        return max(
            0,
            int(self.critical_read_operations),
        ) + max(0, int(self.critical_queue_operations))


def adaptive_tick_seconds(
    policy: TickPolicy,
    demand: TickDemand,
    *,
    circuit_state: str,
    resource_state: str,
) -> float:
    """Choose the slowest cadence that still preserves critical SLO budget."""
    minimum = max(0.001, float(policy.min_tick_seconds))
    maximum = max(minimum, float(policy.max_tick_seconds))
    if circuit_state == "protective":
        return maximum

    baseline = _state_baseline(
        minimum,
        maximum,
        circuit_state=circuit_state,
        resource_state=resource_state,
    )
    if demand.operation_count == 0:
        return baseline

    service_floor = _service_time_floor(
        minimum,
        maximum,
        demand,
        resource_state=resource_state,
    )
    baseline = max(baseline, service_floor)

    deadline = _critical_deadline(policy, demand)
    operation_seconds = max(0.0, float(demand.operation_p95_ms)) / 1000.0
    available_seconds = max(
        0.0,
        deadline * SLO_BUDGET_FRACTION
        - operation_seconds * demand.operation_count,
    )
    deadline_tick = available_seconds / demand.operation_count
    return min(baseline, _clamp(deadline_tick, service_floor, maximum))


def _service_time_floor(
    minimum: float,
    maximum: float,
    demand: TickDemand,
    *,
    resource_state: str,
) -> float:
    """Avoid polling faster than DBus can complete read-only work under pressure."""
    if resource_state == "ok" or demand.critical_queue_operations > 0:
        return minimum
    operation_seconds = max(0.0, float(demand.operation_p95_ms)) / 1000.0
    return _clamp(operation_seconds, minimum, maximum)


def _state_baseline(
    minimum: float,
    maximum: float,
    *,
    circuit_state: str,
    resource_state: str,
) -> float:
    if resource_state == "constrained":
        return maximum
    if circuit_state == "degraded":
        return _clamp(max(minimum * 2.5, 0.5), minimum, maximum)
    if resource_state == "busy":
        return _clamp(max(minimum * 1.5, 0.3), minimum, maximum)
    return minimum


def _critical_deadline(policy: TickPolicy, demand: TickDemand) -> float:
    deadlines: list[float] = []
    if demand.critical_read_operations > 0:
        deadlines.append(
            max(
                0.0,
                float(policy.core_read_slo_seconds)
                - max(0.0, float(demand.core_read_age_seconds)),
            )
        )
    if demand.critical_queue_operations > 0:
        deadlines.append(
            max(
                0.0,
                float(policy.queue_slo_seconds)
                - max(0.0, float(demand.queue_age_seconds)),
            )
        )
    return min(deadlines)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, float(value)))


__all__ = ["TickDemand", "TickPolicy", "adaptive_tick_seconds"]
