# SPDX-License-Identifier: GPL-3.0-or-later
"""Short, source-local PV estimates for transient DBus reply gaps."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.dbus_errors import dbus_error_is_timeout
from venus_evcharger.dbus_adapter.read.aggregate import (
    PV_TOTAL_AGGREGATE,
    AggregateState,
    AggregateStore,
    aggregate_member_float,
)
from venus_evcharger.dbus_adapter.read.protocols import DbusReadAdapter
from venus_evcharger.dbus_adapter.read.spec import ReadSpec, read_spec_optional_confidence
from venus_evcharger.dbus_gateway_cache_metadata import ExternalReadMetadata

PV_LAST_GOOD_INITIAL_FACTOR = 0.8
PV_LAST_GOOD_WINDOW_SECONDS = 5.0
PV_TRANSIENT_HOLD_REASON = "transient-hold"

PvMember = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PvHoldEstimate:
    """One decaying estimate derived from a confirmed member sample."""

    member: PvMember
    value: float
    confidence: float
    age_seconds: float


@dataclass(slots=True)
class _LastGoodSample:
    value: float
    hold_started_at: float | None = None


class PvLastGoodWindow:
    """Retain bounded PV samples only across timeout and NoReply failures."""

    def __init__(
        self,
        *,
        initial_factor: float = PV_LAST_GOOD_INITIAL_FACTOR,
        window_seconds: float = PV_LAST_GOOD_WINDOW_SECONDS,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._initial_factor = _validated_initial_factor(initial_factor)
        self._window_seconds = _validated_window_seconds(window_seconds)
        self._monotonic = time.monotonic if monotonic is None else monotonic
        self._samples: dict[PvMember, _LastGoodSample] = {}

    def record_confirmed(self, service: str, path: str, value: object) -> float | None:
        """Store a finite confirmed value and end any active hold."""
        member = (str(service), str(path))
        numeric = _finite_member_value(value)
        if numeric is None:
            self._samples.pop(member, None)
            return None
        self._samples[member] = _LastGoodSample(numeric)
        return numeric

    def record_error(self, service: str, path: str, error: BaseException) -> None:
        """Start one hold for a transient failure or invalidate other failures."""
        member = (str(service), str(path))
        sample = self._samples.get(member)
        if not dbus_error_is_timeout(error):
            self._samples.pop(member, None)
            return
        if sample is not None and sample.hold_started_at is None:
            sample.hold_started_at = self._monotonic()

    def retain_members(self, members: Iterable[PvMember]) -> None:
        """Invalidate samples whose service/path is no longer in the topology."""
        retained = frozenset((str(service), str(path)) for service, path in members)
        for member in tuple(self._samples):
            if member not in retained:
                del self._samples[member]

    def estimates(self, members: Iterable[PvMember]) -> tuple[PvHoldEstimate, ...]:
        """Return active estimates at one shared monotonic instant."""
        normalized = _normalized_members(members)
        self.retain_members(normalized)
        return self._active_estimates(normalized, self._monotonic())

    def _active_estimates(
        self,
        members: tuple[PvMember, ...],
        current: float,
    ) -> tuple[PvHoldEstimate, ...]:
        estimates: list[PvHoldEstimate] = []
        for member in members:
            sample = self._samples.get(member)
            if sample is None:
                continue
            estimate = self._estimate(member, sample, current)
            if estimate is not None:
                estimates.append(estimate)
        return tuple(estimates)

    def _estimate(
        self,
        member: PvMember,
        sample: _LastGoodSample,
        current: float,
    ) -> PvHoldEstimate | None:
        started_at = sample.hold_started_at
        if started_at is None:
            return None
        age = max(0.0, current - started_at)
        remaining = max(0.0, 1.0 - age / self._window_seconds)
        confidence = self._initial_factor * remaining
        if confidence <= 0.0:
            return None
        return PvHoldEstimate(
            member=member,
            value=sample.value * confidence,
            confidence=confidence,
            age_seconds=age,
        )


class PvAggregateContinuity:
    """Coordinate last-good state with PV discovery and aggregate cycles."""

    def __init__(
        self,
        adapter: DbusReadAdapter,
        aggregates: AggregateStore,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._adapter = adapter
        self._aggregates = aggregates
        self._window = PvLastGoodWindow(monotonic=monotonic)
        self._candidates_by_key: dict[str, tuple[PvMember, ...]] = {}

    def plan(
        self,
        key: str,
        spec: ReadSpec,
        *,
        explicit_members: Iterable[PvMember] | None = None,
    ) -> tuple[tuple[PvMember, ...], AggregateState | None]:
        """Return probe members or a completed hold-only aggregate state."""
        candidates = self._plan_candidates(spec, explicit_members)
        self._window.retain_members(candidates)
        self._candidates_by_key[key] = candidates
        in_progress = self._aggregates.signature_members(key, PV_TOTAL_AGGREGATE)
        members = self._plan_members(
            spec,
            candidates,
            explicit_members=explicit_members,
            in_progress=in_progress,
        )
        if members:
            return members, None
        return (), self._hold_only_state(key, spec)

    def _plan_candidates(
        self,
        spec: ReadSpec,
        explicit_members: Iterable[PvMember] | None,
    ) -> tuple[PvMember, ...]:
        if explicit_members is not None:
            return _normalized_members(explicit_members)
        return tuple(self._adapter.energy_discovery.pv_candidates(spec))

    def _plan_members(
        self,
        spec: ReadSpec,
        candidates: tuple[PvMember, ...],
        *,
        explicit_members: Iterable[PvMember] | None,
        in_progress: Iterable[PvMember] | None,
    ) -> tuple[PvMember, ...]:
        if in_progress is not None:
            return tuple(in_progress)
        if explicit_members is not None:
            return candidates
        return tuple(self._adapter.energy_discovery.pv_members(spec))

    def _hold_only_state(self, key: str, spec: ReadSpec) -> AggregateState | None:
        state = self._aggregates.state_for(
            key,
            (PV_TOTAL_AGGREGATE, ()),
            read_spec_optional_confidence(spec),
        )
        self.add_estimates(key, state)
        if state.estimated:
            return state
        self._aggregates.discard(key)
        self.discard(key)
        return None

    def record_confirmed(
        self,
        state: AggregateState,
        service: str,
        path: str,
        value: object,
    ) -> object | None:
        """Normalize a PV value while leaving non-PV aggregates untouched."""
        if not self.is_pv_total(state):
            return value
        return self._window.record_confirmed(service, path, value)

    def record_error(
        self,
        state: AggregateState,
        service: str,
        path: str,
        error: BaseException,
    ) -> None:
        """Update continuity only for PV aggregate member failures."""
        if self.is_pv_total(state):
            self._window.record_error(service, path, error)

    def add_estimates(self, key: str, state: AggregateState) -> None:
        """Append still-valid estimates without duplicating them."""
        if not self.is_pv_total(state):
            return
        if state.estimated:
            return
        for estimate in self._available_estimates(key):
            service, path = estimate.member
            state.record_estimate(
                service,
                path,
                estimate.value,
                confidence=estimate.confidence,
            )

    def complete(
        self,
        key: str,
        state: AggregateState,
        *,
        stale_after_seconds: float | None,
    ) -> None:
        """Publish an aggregate with explicit metadata for held estimates."""
        self.add_estimates(key, state)
        payload = state.payload(key)
        metadata: ExternalReadMetadata = {
            "source": payload["source"],
            "confidence": payload["confidence"],
            "last_error": payload["last_error"],
            "stale_after_seconds": stale_after_seconds,
        }
        if state.estimated:
            metadata["status"] = "stale"
            metadata["confirmed"] = False
            metadata["reason_code"] = PV_TRANSIENT_HOLD_REASON
        self._adapter.cache.update_external_read(
            key,
            payload["value"],
            **metadata,
        )
        self._aggregates.discard(key)
        self.discard(key)

    def _available_estimates(self, key: str) -> tuple[PvHoldEstimate, ...]:
        advertised = self._adapter.cache.services
        candidates = tuple(member for member in self._candidates_by_key.get(key, ()) if member[0] in advertised)
        return self._window.estimates(candidates)

    def discard(self, key: str) -> None:
        """Forget aggregate-cycle bookkeeping without erasing confirmed samples."""
        self._candidates_by_key.pop(key, None)

    @staticmethod
    def is_pv_total(state: AggregateState) -> bool:
        """Return whether state belongs to the semantic PV total."""
        return bool(state.signature) and state.signature[0] == PV_TOTAL_AGGREGATE


def _finite_member_value(value: object) -> float | None:
    try:
        numeric = aggregate_member_float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _normalized_members(members: Iterable[PvMember]) -> tuple[PvMember, ...]:
    return tuple((str(service), str(path)) for service, path in members)


def _validated_initial_factor(value: float) -> float:
    factor = float(value)
    if math.isfinite(factor) and 0.0 <= factor <= 1.0:
        return factor
    raise ValueError("PV last-good initial factor must be between zero and one")


def _validated_window_seconds(value: float) -> float:
    duration = float(value)
    if math.isfinite(duration) and duration > 0.0:
        return duration
    raise ValueError("PV last-good window must be finite and positive")


__all__ = [
    "PV_LAST_GOOD_INITIAL_FACTOR",
    "PV_LAST_GOOD_WINDOW_SECONDS",
    "PV_TRANSIENT_HOLD_REASON",
    "PvAggregateContinuity",
    "PvHoldEstimate",
    "PvLastGoodWindow",
]
