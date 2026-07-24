# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed policies and cycle results for configured external energy sources."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.energy.models import EnergySourceSnapshot
from venus_evcharger.inputs.helper.contracts import Snapshot

PvSourcePolicyName = Literal[
    "gateway_only",
    "gateway_preferred",
    "external_preferred",
    "external_only",
]
ProjectionMeasurementStatus = Literal["fresh", "stale"]
PV_SOURCE_POLICIES = frozenset(
    {"gateway_only", "gateway_preferred", "external_preferred", "external_only"}
)


@dataclass(frozen=True, slots=True)
class ExternalPollingPolicy:
    """Bound one helper cycle and define source retry/freshness behavior."""

    poll_interval_seconds: float = 1.0
    backoff_base_seconds: float = 5.0
    backoff_max_seconds: float = 60.0
    last_good_max_age_seconds: float = 30.0
    cycle_budget_seconds: float = 2.0

    def __post_init__(self) -> None:
        _validate_external_polling_policy(self)


@dataclass(frozen=True, slots=True)
class PvProjectionPolicy:
    """Choose between semantic gateway PV and configured external PV."""

    name: PvSourcePolicyName = "gateway_preferred"
    external_source_id: str = ""


@dataclass(frozen=True, slots=True)
class ExternalSourcePoll:
    """One source's cached measurement and explicit polling diagnostics."""

    snapshot: EnergySourceSnapshot
    contributing: bool
    poll_status: str
    measurement_status: str
    attempted_at: float | None
    observed_at: float | None
    next_poll_at: float
    age_seconds: float | None
    consecutive_failures: int
    last_error: str

    def payload(self) -> dict[str, object]:
        payload = dict(self.snapshot.as_dict())
        payload.update(
            {
                "contributing": self.contributing,
                "poll_status": self.poll_status,
                "measurement_status": self.measurement_status,
                "attempted_at": self.attempted_at,
                "observed_at": self.observed_at,
                "next_poll_at": self.next_poll_at,
                "age_seconds": self.age_seconds,
                "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class ProjectedEnergyValue:
    """One selected semantic value with its true observation timestamp."""

    value: float
    observed_at: float
    source_id: str
    confidence: float
    measurement_status: ProjectionMeasurementStatus = "fresh"


@dataclass(frozen=True, slots=True)
class ExternalEnergyCycle:
    """One coherent external poll result reused by every helper projection."""

    battery: Snapshot
    pv: ProjectedEnergyValue | None
    battery_observed_at: float | None
    polls: tuple[ExternalSourcePoll, ...]


def _validate_external_polling_policy(policy: ExternalPollingPolicy) -> None:
    _require_positive(policy.poll_interval_seconds, "poll interval")
    _require_positive(policy.backoff_base_seconds, "backoff base")
    _require_positive(policy.backoff_max_seconds, "backoff maximum")
    _require_non_negative(policy.last_good_max_age_seconds, "last-good maximum age")
    _require_positive(policy.cycle_budget_seconds, "cycle time budget")
    if policy.backoff_max_seconds < policy.backoff_base_seconds:
        raise ValueError("External energy-source backoff maximum must cover its base")
def _require_positive(value: float, label: str) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"External energy-source {label} must be positive")


def _require_non_negative(value: float, label: str) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"External energy-source {label} must be non-negative")


def projection_measurement_status(value: str) -> ProjectionMeasurementStatus:
    """Narrow one contributing source status to the projection contract."""
    if value == "fresh":
        return "fresh"
    if value == "stale":
        return "stale"
    raise ValueError(f"Unsupported contributing measurement status: {value}")


__all__ = [
    "ExternalEnergyCycle",
    "ExternalPollingPolicy",
    "ExternalSourcePoll",
    "PV_SOURCE_POLICIES",
    "ProjectedEnergyValue",
    "ProjectionMeasurementStatus",
    "PvProjectionPolicy",
    "PvSourcePolicyName",
    "projection_measurement_status",
]
