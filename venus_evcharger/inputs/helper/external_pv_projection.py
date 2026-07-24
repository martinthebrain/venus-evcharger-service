# SPDX-License-Identifier: GPL-3.0-or-later
"""PV projection from one coherent external-source polling cycle."""

from __future__ import annotations

from venus_evcharger.energy import aggregate_energy_sources
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalSourcePoll,
    ProjectedEnergyValue,
    ProjectionMeasurementStatus,
    projection_measurement_status,
)


def external_pv_projection(
    polls: tuple[ExternalSourcePoll, ...],
    source_id: str,
) -> ProjectedEnergyValue | None:
    """Select one configured source or aggregate all usable external PV."""
    candidates = tuple(poll for poll in polls if usable_pv_poll(poll, source_id))
    if not candidates:
        return None
    if source_id:
        return single_pv_projection(candidates[0])
    return aggregate_pv_projection(candidates)


def usable_pv_poll(poll: ExternalSourcePoll, source_id: str) -> bool:
    value = poll.snapshot.pv_input_power_w
    source_matches = not source_id or poll.snapshot.source_id == source_id
    return poll.contributing and source_matches and value is not None and value >= 0.0


def single_pv_projection(
    poll: ExternalSourcePoll,
) -> ProjectedEnergyValue | None:
    snapshot = poll.snapshot
    if snapshot.pv_input_power_w is None or poll.observed_at is None:
        return None
    return ProjectedEnergyValue(
        value=float(snapshot.pv_input_power_w),
        observed_at=float(poll.observed_at),
        source_id=snapshot.source_id,
        confidence=float(snapshot.confidence),
        measurement_status=projection_measurement_status(
            poll.measurement_status
        ),
    )


def aggregate_pv_projection(
    polls: tuple[ExternalSourcePoll, ...],
) -> ProjectedEnergyValue | None:
    cluster = aggregate_energy_sources(tuple(poll.snapshot for poll in polls))
    if cluster.combined_pv_input_power_w is None:
        return None
    observed = pv_observation_times(polls)
    if not observed:
        return None
    return ProjectedEnergyValue(
        value=float(cluster.combined_pv_input_power_w),
        observed_at=min(observed),
        source_id="external-aggregate",
        confidence=_minimum_confidence(polls),
        measurement_status=_aggregate_measurement_status(polls),
    )


def pv_observation_times(
    polls: tuple[ExternalSourcePoll, ...],
) -> tuple[float, ...]:
    return tuple(
        float(poll.observed_at)
        for poll in polls
        if poll.observed_at is not None
    )


def _minimum_confidence(polls: tuple[ExternalSourcePoll, ...]) -> float:
    return min(float(poll.snapshot.confidence) for poll in polls)


def _aggregate_measurement_status(
    polls: tuple[ExternalSourcePoll, ...],
) -> ProjectionMeasurementStatus:
    if any(poll.measurement_status == "stale" for poll in polls):
        return "stale"
    return "fresh"


__all__ = [
    "aggregate_pv_projection",
    "external_pv_projection",
    "pv_observation_times",
    "single_pv_projection",
    "usable_pv_poll",
]
