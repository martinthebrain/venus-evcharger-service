# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded round-robin polling for configured external energy sources."""

from __future__ import annotations

import logging
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.energy.read_steps import EnergySourceStepReader
from venus_evcharger.inputs.helper.external_contracts import (
    MAX_EXTERNAL_CYCLE_BUDGET_SECONDS,
    ExternalPollingPolicy,
    ExternalSourcePoll,
)

_SOURCE_READ_ERRORS = (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError, TypeError, ValueError)
_AttemptStatus = Literal["success", "failed", "in_progress"]


class EnergyConnectorRuntime:
    """Narrow mutable runtime required by existing connector contracts."""

    def __init__(
        self,
        request_timeout_seconds: float,
        session: object | None,
        monotonic: Callable[[], float],
    ) -> None:
        self.shelly_request_timeout_seconds = float(request_timeout_seconds)
        self.session = session
        self._monotonic = monotonic
        self._deadline: float | None = None

    def begin_attempt(self, deadline: float) -> None:
        self._deadline = float(deadline)

    def bounded_request_timeout_seconds(self, configured_seconds: float) -> float:
        configured = max(
            0.001,
            min(float(configured_seconds), self.shelly_request_timeout_seconds),
        )
        if self._deadline is None:
            return configured
        remaining = max(0.001, self._deadline - self._monotonic())
        return min(configured, remaining)


@dataclass(slots=True)
class _SourceState:
    last_good: EnergySourceSnapshot | None = None
    last_good_monotonic: float | None = None
    attempted_at: float | None = None
    next_poll_monotonic: float = 0.0
    consecutive_failures: int = 0
    last_error: str = ""
    in_progress: bool = False


class ExternalSourceScheduler:
    """Poll sources fairly without allowing timeout multiplication."""

    def __init__(
        self,
        definitions: tuple[EnergySourceDefinition, ...],
        policy: ExternalPollingPolicy,
        request_timeout_seconds: float,
        reader: EnergySourceStepReader,
        *,
        session: object | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.definitions = definitions
        self.policy = policy
        self._reader = reader
        self._monotonic = monotonic
        self._runtime = EnergyConnectorRuntime(request_timeout_seconds, session, monotonic)
        self._states = {definition.source_id: _SourceState() for definition in definitions}
        self._cursor = 0
        self._failed_sources: set[str] = set()

    def poll(self, now: float) -> tuple[ExternalSourcePoll, ...]:
        cycle_started_monotonic = self._monotonic()
        deadline = cycle_started_monotonic + min(
            self.policy.cycle_budget_seconds,
            MAX_EXTERNAL_CYCLE_BUDGET_SECONDS,
        )
        attempted: dict[str, _AttemptStatus] = {}
        monotonic_now = cycle_started_monotonic
        definition = self._next_due_definition(monotonic_now)
        if definition is not None:
            attempted[definition.source_id] = self._attempt(
                definition,
                now,
                deadline,
            )
        result_monotonic = self._monotonic()
        return tuple(
            self._poll_result(
                definition,
                now,
                cycle_started_monotonic,
                result_monotonic,
                attempted,
            )
            for definition in self.definitions
        )

    def _next_due_definition(
        self,
        monotonic_now: float,
    ) -> EnergySourceDefinition | None:
        count = len(self.definitions)
        for offset in range(count):
            index = (self._cursor + offset) % count
            definition = self.definitions[index]
            state = self._states[definition.source_id]
            if monotonic_now >= state.next_poll_monotonic:
                self._cursor = (index + 1) % count
                return definition
        return None

    def _attempt(
        self,
        definition: EnergySourceDefinition,
        now: float,
        deadline: float,
    ) -> _AttemptStatus:
        state = self._states[definition.source_id]
        state.attempted_at = now
        self._runtime.begin_attempt(deadline)
        try:
            step = self._reader(self._runtime, definition, now)
        except _SOURCE_READ_ERRORS as error:
            self._record_failure(
                definition,
                state,
                self._monotonic(),
                str(error),
            )
            return "failed"
        if not step.complete:
            self._record_in_progress(state, self._monotonic())
            return "in_progress"
        snapshot = step.snapshot
        assert snapshot is not None
        snapshot = _configured_snapshot(definition, snapshot)
        completed_monotonic = self._monotonic()
        if not _confirms_measurement(snapshot):
            self._record_failure(
                definition,
                state,
                completed_monotonic,
                "source reported no online measurement",
            )
            return "failed"
        self._record_success(
            definition,
            state,
            snapshot,
            completed_monotonic,
        )
        return "success"

    def _record_failure(
        self,
        definition: EnergySourceDefinition,
        state: _SourceState,
        completed_monotonic: float,
        message: str,
    ) -> None:
        state.consecutive_failures += 1
        state.last_error = message
        state.in_progress = False
        state.next_poll_monotonic = completed_monotonic + _backoff_seconds(
            self.policy,
            state.consecutive_failures,
        )
        if definition.source_id not in self._failed_sources:
            logging.warning("External energy source unavailable source=%s: %s", definition.source_id, message)
        self._failed_sources.add(definition.source_id)

    def _record_success(
        self,
        definition: EnergySourceDefinition,
        state: _SourceState,
        snapshot: EnergySourceSnapshot,
        completed_monotonic: float,
    ) -> None:
        if definition.source_id in self._failed_sources:
            logging.info("External energy source recovered source=%s", definition.source_id)
        self._failed_sources.discard(definition.source_id)
        state.last_good = snapshot
        state.last_good_monotonic = completed_monotonic
        state.consecutive_failures = 0
        state.last_error = ""
        state.in_progress = False
        state.next_poll_monotonic = (
            completed_monotonic + self.policy.poll_interval_seconds
        )

    @staticmethod
    def _record_in_progress(
        state: _SourceState,
        completed_monotonic: float,
    ) -> None:
        state.in_progress = True
        state.last_error = ""
        state.next_poll_monotonic = completed_monotonic

    def _poll_result(
        self,
        definition: EnergySourceDefinition,
        now: float,
        cycle_started_monotonic: float,
        monotonic_now: float,
        attempted: dict[str, _AttemptStatus],
    ) -> ExternalSourcePoll:
        state = self._states[definition.source_id]
        age = _snapshot_age(state.last_good_monotonic, monotonic_now)
        contributing = age is not None and age <= self.policy.last_good_max_age_seconds
        return ExternalSourcePoll(
            snapshot=state.last_good or _offline_source(definition),
            contributing=contributing,
            poll_status=_poll_status(
                state,
                monotonic_now,
                attempted.get(definition.source_id),
            ),
            measurement_status=_measurement_status(state, age, contributing),
            attempted_at=state.attempted_at,
            observed_at=_observed_at(state.last_good),
            observed_monotonic=state.last_good_monotonic,
            next_poll_at=now + max(
                0.0,
                state.next_poll_monotonic - cycle_started_monotonic,
            ),
            age_seconds=age,
            consecutive_failures=state.consecutive_failures,
            last_error=state.last_error,
        )


def _confirms_measurement(
    snapshot: EnergySourceSnapshot,
) -> bool:
    return (
        snapshot.online
        and _valid_observed_at(snapshot.captured_at)
        and _has_contributing_value(snapshot)
    )


def _configured_snapshot(
    definition: EnergySourceDefinition,
    snapshot: EnergySourceSnapshot,
) -> EnergySourceSnapshot:
    """Apply identity fields owned by configuration at the connector boundary."""
    return replace(
        snapshot,
        source_id=definition.source_id,
        role=definition.role,
        physical_id=definition.physical_id,
        physical_priority=definition.physical_priority,
    )


def _has_contributing_value(snapshot: EnergySourceSnapshot) -> bool:
    values = (
        snapshot.soc,
        snapshot.usable_capacity_wh,
        snapshot.net_battery_power_w,
        snapshot.charge_limit_power_w,
        snapshot.discharge_limit_power_w,
        snapshot.ac_power_w,
        snapshot.pv_input_power_w,
        snapshot.grid_interaction_w,
    )
    return any(value is not None for value in values)


def _backoff_seconds(policy: ExternalPollingPolicy, failures: int) -> float:
    exponent = min(30, max(0, failures - 1))
    multiplier = float(2**exponent)
    return float(min(policy.backoff_max_seconds, policy.backoff_base_seconds * multiplier))


def _snapshot_age(observed_monotonic: float | None, monotonic_now: float) -> float | None:
    if not _valid_observed_at(observed_monotonic):
        return None
    assert observed_monotonic is not None
    if monotonic_now < observed_monotonic:
        return None
    return monotonic_now - observed_monotonic


def _valid_observed_at(observed_at: float | None) -> bool:
    if observed_at is None:
        return False
    normalized = float(observed_at)
    return math.isfinite(normalized) and normalized >= 0.0


def _observed_at(snapshot: EnergySourceSnapshot | None) -> float | None:
    return None if snapshot is None else snapshot.captured_at


def _poll_status(
    state: _SourceState,
    monotonic_now: float,
    attempted: _AttemptStatus | None,
) -> str:
    if attempted is not None:
        return attempted
    if state.in_progress:
        return "in_progress"
    if monotonic_now >= state.next_poll_monotonic:
        return "deferred_budget"
    return "backoff" if state.consecutive_failures else "idle"


def _measurement_status(
    state: _SourceState,
    age: float | None,
    contributing: bool,
) -> str:
    if age is None:
        return "missing"
    if not contributing:
        return "expired"
    return "stale" if state.consecutive_failures else "fresh"


def _offline_source(definition: EnergySourceDefinition) -> EnergySourceSnapshot:
    return EnergySourceSnapshot(
        source_id=definition.source_id,
        role=definition.role,
        service_name=definition.service_name or definition.config_path or definition.source_id,
        usable_capacity_wh=definition.usable_capacity_wh,
        battery_chemistry=definition.battery_chemistry,
        physical_id=definition.physical_id,
        physical_priority=definition.physical_priority,
    )


__all__ = ["EnergyConnectorRuntime", "ExternalSourceScheduler"]
