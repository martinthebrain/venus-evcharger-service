# SPDX-License-Identifier: GPL-3.0-or-later
"""Select external battery SOC values and their matching observation clocks."""

from __future__ import annotations

from venus_evcharger.energy import EnergyClusterSnapshot, EnergySourceSnapshot
from venus_evcharger.energy.physical_sources import unique_weighted_soc_sources
from venus_evcharger.inputs.helper.external_contracts import ExternalSourcePoll
from venus_evcharger.ipc.energy import MeasuredValue


def _with_gateway_source(
    external: tuple[EnergySourceSnapshot, ...],
    gateway: EnergySourceSnapshot | None,
) -> tuple[EnergySourceSnapshot, ...]:
    """Append the semantic gateway source when it is available."""
    return external if gateway is None else external + (gateway,)


def _selected_soc(
    cluster: EnergyClusterSnapshot,
    external: tuple[EnergySourceSnapshot, ...],
    gateway: EnergySourceSnapshot | None,
    use_combined_soc: bool,
) -> tuple[float | None, float | None]:
    """Select effective SOC and its diagnostic epoch observation time."""
    if use_combined_soc and cluster.combined_soc is not None:
        return _combined_soc_selection(cluster.combined_soc, external, gateway)
    return _fallback_soc_selection(external, gateway)


def _selected_soc_observed_monotonic(
    cluster: EnergyClusterSnapshot,
    polls: tuple[ExternalSourcePoll, ...],
    gateway: EnergySourceSnapshot | None,
    gateway_measurement: MeasuredValue | None,
    use_combined_soc: bool,
) -> float | None:
    """Select the monotonic observation paired with effective SOC."""
    contributing_polls = _contributing_polls(polls)
    external = tuple(poll.snapshot for poll in contributing_polls)
    if use_combined_soc and cluster.combined_soc is not None:
        return _combined_soc_observed_monotonic(
            external,
            contributing_polls,
            gateway,
            gateway_measurement,
        )
    return _fallback_soc_observed_monotonic(
        external,
        contributing_polls,
        gateway,
        gateway_measurement,
    )


def _contributing_polls(
    polls: tuple[ExternalSourcePoll, ...],
) -> tuple[ExternalSourcePoll, ...]:
    """Return only external observations admitted by scheduler policy."""
    return tuple(poll for poll in polls if poll.contributing)


def _combined_soc_observed_monotonic(
    external: tuple[EnergySourceSnapshot, ...],
    polls: tuple[ExternalSourcePoll, ...],
    gateway: EnergySourceSnapshot | None,
    gateway_measurement: MeasuredValue | None,
) -> float | None:
    """Return the oldest monotonic observation contributing to combined SOC."""
    sources = unique_weighted_soc_sources(_with_gateway_source(external, gateway))
    source_ids = frozenset(source.source_id for source in sources)
    observed = _poll_observation_times(polls, source_ids)
    gateway_observed = _gateway_observation_time(
        gateway,
        gateway_measurement,
        source_ids,
    )
    return _oldest_monotonic_observation(observed, gateway_observed)


def _fallback_soc_observed_monotonic(
    external: tuple[EnergySourceSnapshot, ...],
    polls: tuple[ExternalSourcePoll, ...],
    gateway: EnergySourceSnapshot | None,
    gateway_measurement: MeasuredValue | None,
) -> float | None:
    """Return the timestamp belonging to the selected fallback SOC source."""
    selected = _first_external_soc_source(external)
    if selected is not None:
        return _source_poll_observation_time(polls, selected.source_id)
    if _gateway_soc_source(gateway) is None:
        return None
    return _measurement_observation_time(gateway_measurement)


def _poll_observation_times(
    polls: tuple[ExternalSourcePoll, ...],
    source_ids: frozenset[str],
) -> tuple[float, ...]:
    """Return monotonic observations for the selected external sources."""
    return tuple(
        float(poll.observed_monotonic)
        for poll in polls
        if poll.snapshot.source_id in source_ids
        and poll.observed_monotonic is not None
    )


def _gateway_observation_time(
    gateway: EnergySourceSnapshot | None,
    measurement: MeasuredValue | None,
    source_ids: frozenset[str],
) -> float | None:
    """Return a gateway observation only when gateway SOC contributes."""
    if gateway is None or gateway.source_id not in source_ids:
        return None
    return _measurement_observation_time(measurement)


def _source_poll_observation_time(
    polls: tuple[ExternalSourcePoll, ...],
    source_id: str,
) -> float | None:
    """Return the observation clock attached to one selected external source."""
    return next(
        (
            float(poll.observed_monotonic)
            for poll in polls
            if poll.snapshot.source_id == source_id
            and poll.observed_monotonic is not None
        ),
        None,
    )


def _measurement_observation_time(
    measurement: MeasuredValue | None,
) -> float | None:
    """Return the monotonic timestamp of an optional semantic measurement."""
    if measurement is None:
        return None
    return float(measurement.observed_monotonic)


def _oldest_monotonic_observation(
    external: tuple[float, ...],
    gateway: float | None,
) -> float | None:
    """Return the oldest timestamp across all contributing source classes."""
    observed = external if gateway is None else (*external, gateway)
    return min(observed) if observed else None


def _combined_soc_selection(
    combined_soc: float,
    external: tuple[EnergySourceSnapshot, ...],
    gateway: EnergySourceSnapshot | None,
) -> tuple[float, float | None]:
    """Pair combined SOC with its oldest weighted epoch observation."""
    return float(combined_soc), _oldest_weighted_soc_observation(external, gateway)


def _fallback_soc_selection(
    external: tuple[EnergySourceSnapshot, ...],
    gateway: EnergySourceSnapshot | None,
) -> tuple[float | None, float | None]:
    """Select the first valid external SOC or the semantic gateway fallback."""
    selected = _first_external_soc_source(external)
    if selected is None:
        selected = _gateway_soc_source(gateway)
    if selected is None or selected.soc is None:
        return None, None
    return float(selected.soc), selected.captured_at


def _oldest_weighted_soc_observation(
    external: tuple[EnergySourceSnapshot, ...],
    gateway: EnergySourceSnapshot | None,
) -> float | None:
    """Return the oldest epoch timestamp contributing to weighted SOC."""
    sources = unique_weighted_soc_sources(_with_gateway_source(external, gateway))
    observed = tuple(
        observed_at
        for observed_at in map(_weighted_soc_observed_at, sources)
        if observed_at is not None
    )
    return min(observed) if observed else None


def _weighted_soc_observed_at(source: EnergySourceSnapshot) -> float | None:
    """Return an epoch observation only for a complete weighted SOC source."""
    if source.soc is None or source.usable_capacity_wh is None:
        return None
    if source.usable_capacity_wh <= 0.0 or source.captured_at is None:
        return None
    return float(source.captured_at)


def _first_external_soc_source(
    external: tuple[EnergySourceSnapshot, ...],
) -> EnergySourceSnapshot | None:
    """Return the first online external source with a current SOC value."""
    for source in external:
        if source.online and source.soc is not None and source.captured_at is not None:
            return source
    return None


def _gateway_soc_source(
    gateway: EnergySourceSnapshot | None,
) -> EnergySourceSnapshot | None:
    """Return a usable semantic gateway SOC source."""
    if gateway is None or gateway.soc is None or gateway.captured_at is None:
        return None
    return gateway
