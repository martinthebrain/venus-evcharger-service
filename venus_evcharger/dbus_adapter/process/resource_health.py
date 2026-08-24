# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource-pressure classification for public gateway health."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard, cast


def resource_pressure_evidence(
    resources: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Return a typed resource-evidence mapping when one is present."""
    evidence = resources.get("pressure_evidence")
    if not isinstance(evidence, Mapping):
        return None
    return cast(Mapping[str, object], evidence)


def resource_pressure_is_protective(
    resource_state: str,
    evidence: Mapping[str, object] | None,
) -> bool:
    """Return whether constrained pressure came from critical CPU or RAM."""
    if resource_state != "constrained" or evidence is None:
        return False
    return bool(_critical_resource_causes(evidence))


def protective_cause(
    *,
    aggregate_state: str,
    operational_state: str,
    backpressure_state: str,
    resource_protective: bool,
    resource_evidence: Mapping[str, object] | None,
) -> str:
    """Explain a protective aggregate state using bounded semantic causes."""
    if aggregate_state != "protective":
        return ""
    direct_cause = _direct_protective_cause(operational_state, backpressure_state)
    if direct_cause:
        return direct_cause
    if resource_protective:
        critical = _critical_resource_causes(resource_evidence)
        if critical:
            return "resource-" + "+".join(critical)
    return "recovery-hold"


def _direct_protective_cause(operational_state: str, backpressure_state: str) -> str:
    if operational_state == "protective":
        return "circuit-breaker"
    if backpressure_state == "protective":
        return "backpressure"
    return ""


def _critical_resource_causes(
    evidence: Mapping[str, object] | None,
) -> tuple[str, ...]:
    causes = None if evidence is None else evidence.get("causes")
    if not isinstance(causes, (list, tuple)):
        return ()
    values = cast(list[object] | tuple[object, ...], causes)
    return tuple(filter(_is_critical_resource_cause, values))


def _is_critical_resource_cause(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value in {"cpu", "memory"}


__all__ = [
    "protective_cause",
    "resource_pressure_evidence",
    "resource_pressure_is_protective",
]
