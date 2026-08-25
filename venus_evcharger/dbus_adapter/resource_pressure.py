# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource pressure classification and recovery hysteresis."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
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
ResourcePressureCause: TypeAlias = Literal["load", "cpu", "memory"]
_ESCALATIONS: frozenset[tuple[ResourceState, ResourceState]] = frozenset(
    {
        ("ok", "busy"),
        ("ok", "constrained"),
        ("busy", "constrained"),
    }
)
_RECOVERY_IMPROVEMENTS: frozenset[tuple[ResourceState, ResourceState]] = frozenset({("busy", "ok")})


@dataclass(frozen=True, slots=True)
class _Recovery:
    target: ResourceState
    started_at: float


@dataclass(frozen=True, slots=True)
class ResourcePressureEvidence:
    """Measurements that caused the most recent constrained transition."""

    triggered_at: float
    causes: tuple[ResourcePressureCause, ...]
    load_per_cpu_1m: float | None
    system_cpu_pct: float | None
    mem_available_kb: float | None

    def to_payload(self, *, active: bool) -> dict[str, object]:
        """Return bounded transport-neutral evidence for diagnostics."""
        return {
            "active": bool(active),
            "triggered_at": self.triggered_at,
            "causes": list(self.causes),
            "load_per_cpu_1m": self.load_per_cpu_1m,
            "system_cpu_pct": self.system_cpu_pct,
            "mem_available_kb": self.mem_available_kb,
        }


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
        self._last_constrained_evidence: ResourcePressureEvidence | None = None

    @property
    def last_constrained_evidence(self) -> ResourcePressureEvidence | None:
        """Return the evidence retained from the latest constrained entry."""
        return self._last_constrained_evidence

    def observe(
        self,
        *,
        load_per_cpu: float | None,
        cpu_pct: float | None,
        mem_available_kb: float | None,
        now: float,
        observed_at: float | None = None,
    ) -> ResourceState:
        previous = self.state
        candidate = self._candidate_state(load_per_cpu, cpu_pct, mem_available_kb)
        if candidate == self.state:
            self._clear_recovery()
        elif (self.state, candidate) in _ESCALATIONS:
            self.state = candidate
            self._clear_recovery()
        else:
            self._recover_toward(candidate, float(now))
        self._capture_constrained_transition(
            previous=previous,
            load_per_cpu=load_per_cpu,
            cpu_pct=cpu_pct,
            mem_available_kb=mem_available_kb,
            observed_at=observed_at,
        )
        return self.state

    def pressure_evidence_payload(self) -> dict[str, object] | None:
        """Return the latest trigger and whether constrained pressure is active."""
        evidence = self._last_constrained_evidence
        if evidence is None:
            return None
        return evidence.to_payload(active=self.state == "constrained")

    def _capture_constrained_transition(
        self,
        *,
        previous: ResourceState,
        load_per_cpu: float | None,
        cpu_pct: float | None,
        mem_available_kb: float | None,
        observed_at: float | None,
    ) -> None:
        if self.state != "constrained":
            return
        causes = _constrained_causes(load_per_cpu, cpu_pct, mem_available_kb)
        if not causes:
            return
        if not _should_replace_evidence(
            previous,
            self._last_constrained_evidence,
            causes,
        ):
            return
        self._last_constrained_evidence = ResourcePressureEvidence(
            triggered_at=_non_negative_finite(observed_at),
            causes=causes,
            load_per_cpu_1m=_optional_non_negative_finite(load_per_cpu),
            system_cpu_pct=_optional_non_negative_finite(cpu_pct),
            mem_available_kb=_optional_non_negative_finite(mem_available_kb),
        )

    def _candidate_state(
        self,
        load_per_cpu: float | None,
        cpu_pct: float | None,
        mem_available_kb: float | None,
    ) -> ResourceState:
        if self._must_hold_constrained(load_per_cpu, cpu_pct, mem_available_kb):
            return "constrained"
        candidate = resource_state(load_per_cpu, cpu_pct, mem_available_kb)
        if self.state == "busy" and candidate == "ok" and not _busy_exit_ready(load_per_cpu, cpu_pct, mem_available_kb):
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


def _constrained_causes(
    load_per_cpu: float | None,
    cpu_pct: float | None,
    mem_available_kb: float | None,
) -> tuple[ResourcePressureCause, ...]:
    causes: list[ResourcePressureCause] = []
    if _at_least(load_per_cpu, CONSTRAINED_LOAD_PER_CPU):
        causes.append("load")
    if _at_least(cpu_pct, CONSTRAINED_CPU_PERCENT):
        causes.append("cpu")
    if _below(mem_available_kb, CONSTRAINED_MEM_AVAILABLE_KB):
        causes.append("memory")
    return tuple(causes)


def _has_critical_cause(causes: tuple[ResourcePressureCause, ...]) -> bool:
    return any(cause in {"cpu", "memory"} for cause in causes)


def _should_replace_evidence(
    previous: ResourceState,
    prior: ResourcePressureEvidence | None,
    current_causes: tuple[ResourcePressureCause, ...],
) -> bool:
    if previous != "constrained" or prior is None:
        return True
    return not _has_critical_cause(prior.causes) and _has_critical_cause(current_causes)


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


def _non_negative_finite(value: float | None) -> float:
    normalized = _optional_non_negative_finite(value)
    return 0.0 if normalized is None else normalized


def _optional_non_negative_finite(value: float | None) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not isfinite(normalized):
        return None
    return max(0.0, normalized)
