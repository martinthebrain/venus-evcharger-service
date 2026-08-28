#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Planning for one bounded aggregate PV read cycle."""

from __future__ import annotations

from venus_evcharger.dbus_adapter.read.aggregate import (
    PV_TOTAL_AGGREGATE,
    AggregateCompletion,
    AggregateState,
    AggregateStepPlan,
)
from venus_evcharger.dbus_adapter.read.pv import configured_pv_aggregate_members, dc_pv_members
from venus_evcharger.dbus_adapter.read.pv_last_good import PvAggregateContinuity
from venus_evcharger.dbus_adapter.read.spec import ReadSpec, read_spec_optional_confidence


def plan_pv_aggregate_read(
    continuity: PvAggregateContinuity,
    key: str,
    spec: ReadSpec,
    completion: AggregateCompletion,
) -> AggregateState | AggregateStepPlan:
    """Return either a completed continuity hold or a physical read plan."""
    configured_members = configured_pv_aggregate_members(spec)
    explicit_members = [*configured_members, *dc_pv_members(spec)] if configured_members else None
    members, held_state = continuity.plan(
        key,
        spec,
        explicit_members=explicit_members,
    )
    if held_state is not None:
        return held_state
    if not members:
        raise RuntimeError("No available AC or DC PV source candidates")
    return AggregateStepPlan(
        key=key,
        signature=(PV_TOTAL_AGGREGATE, tuple(members)),
        members=tuple(members),
        completion=completion,
        ignore_member_errors=True,
        empty_confidence=read_spec_optional_confidence(spec),
        record_discovery_values=not configured_members,
    )
