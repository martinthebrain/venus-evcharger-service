# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource pressure classification and recovery hysteresis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

CONSTRAINED_LOAD_PER_CPU = 1.5
CONSTRAINED_CPU_PERCENT = 90.0
CONSTRAINED_MEM_AVAILABLE_KB = 32768.0
BUSY_LOAD_PER_CPU = 1.0
BUSY_CPU_PERCENT = 80.0
BUSY_MEM_AVAILABLE_KB = 65536.0
BUSY_EXIT_LOAD_PER_CPU = 0.85
BUSY_EXIT_CPU_PERCENT = 75.0
BUSY_EXIT_MEM_AVAILABLE_KB = 73728.0
CONSTRAINED_EXIT_LOAD_PER_CPU = 1.25
CONSTRAINED_EXIT_CPU_PERCENT = 85.0
CONSTRAINED_EXIT_MEM_AVAILABLE_KB = 40960.0

ResourceState: TypeAlias = Literal["ok", "busy", "constrained"]
_ESCALATIONS: frozenset[tuple[ResourceState, ResourceState]] = frozenset(
    {
        ("ok", "busy"),
        ("ok", "constrained"),
        ("busy", "constrained"),
    }
)
_RECOVERY_IMPROVEMENTS: frozenset[tuple[ResourceState, ResourceState]] = frozenset(
    {("busy", "ok")}
)


@dataclass(frozen=True, slots=True)
class _Recovery:
    target: ResourceState
    started_at: float


def resource_state(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> ResourceState:
    """Classify the available dimensions of one normalized resource sample."""
    if _all_dimensions_unknown(load_per_cpu, cpu_pct, mem_available_kb):
        return "busy"
    if _resource_constrained(load_per_cpu, cpu_pct, mem_available_kb):
        return "constrained"
    if _resource_busy(load_per_cpu, cpu_pct, mem_available_kb):
        return "busy"
    return "ok"


def _all_dimensions_unknown(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> bool:
    return all(value is None for value in (load_per_cpu, cpu_pct, mem_available_kb))


class ResourceStateLatch:
    """Stabilize resource recovery without delaying pressure escalation."""

    def __init__(self, *, recovery_hold_seconds: float) -> None:
        self.recovery_hold_seconds = max(0.0, float(recovery_hold_seconds))
        self.state: ResourceState = "ok"
        self._recovery: _Recovery | None = None

    def observe(
        self,
        *,
        load_per_cpu: float | None,
        cpu_pct: float | None,
        mem_available_kb: float | None,
        now: float,
    ) -> ResourceState:
        candidate = self._candidate_state(load_per_cpu, cpu_pct, mem_available_kb)
        if candidate == self.state:
            self._clear_recovery()
            return self.state
        if (self.state, candidate) in _ESCALATIONS:
            self.state = candidate
            self._clear_recovery()
            return self.state
        return self._recover_toward(candidate, float(now))

    def _candidate_state(
        self,
        load_per_cpu: float | None,
        cpu_pct: float | None,
        mem_available_kb: float | None,
    ) -> ResourceState:
        if self._must_hold_constrained(load_per_cpu, cpu_pct, mem_available_kb):
            return "constrained"
        candidate = resource_state(load_per_cpu, cpu_pct, mem_available_kb)
        if (
            self.state == "busy"
            and candidate == "ok"
            and not _busy_exit_ready(load_per_cpu, cpu_pct, mem_available_kb)
        ):
            return "busy"
        return candidate

    def _must_hold_constrained(
        self,
        load_per_cpu: float | None,
        cpu_pct: float | None,
        mem_available_kb: float | None,
    ) -> bool:
        return self.state == "constrained" and not _constrained_exit_ready(
            load_per_cpu,
            cpu_pct,
            mem_available_kb,
        )

    def _recover_toward(self, candidate: ResourceState, now: float) -> ResourceState:
        recovery = self._updated_recovery(candidate, now)
        self._recovery = recovery
        if now - recovery.started_at >= self.recovery_hold_seconds:
            self.state = candidate
            self._clear_recovery()
        return self.state

    def _updated_recovery(self, candidate: ResourceState, now: float) -> _Recovery:
        recovery = self._recovery
        if recovery is None:
            return _Recovery(candidate, now)
        if candidate == recovery.target:
            return recovery
        if (recovery.target, candidate) in _RECOVERY_IMPROVEMENTS:
            return _Recovery(candidate, recovery.started_at)
        return _Recovery(candidate, now)

    def _clear_recovery(self) -> None:
        self._recovery = None


def _resource_constrained(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> bool:
    return (
        _at_least(load_per_cpu, CONSTRAINED_LOAD_PER_CPU)
        or _at_least(cpu_pct, CONSTRAINED_CPU_PERCENT)
        or _below(mem_available_kb, CONSTRAINED_MEM_AVAILABLE_KB)
    )


def _resource_busy(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> bool:
    return (
        _at_least(load_per_cpu, BUSY_LOAD_PER_CPU)
        or _at_least(cpu_pct, BUSY_CPU_PERCENT)
        or _below(mem_available_kb, BUSY_MEM_AVAILABLE_KB)
    )


def _constrained_exit_ready(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> bool:
    return (
        _below(load_per_cpu, CONSTRAINED_EXIT_LOAD_PER_CPU)
        and _below(cpu_pct, CONSTRAINED_EXIT_CPU_PERCENT)
        and _at_least(mem_available_kb, CONSTRAINED_EXIT_MEM_AVAILABLE_KB)
    )


def _busy_exit_ready(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> bool:
    return (
        _below(load_per_cpu, BUSY_EXIT_LOAD_PER_CPU)
        and _below(cpu_pct, BUSY_EXIT_CPU_PERCENT)
        and _at_least(mem_available_kb, BUSY_EXIT_MEM_AVAILABLE_KB)
    )


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _below(value: float | None, threshold: float) -> bool:
    return value is not None and value < threshold
