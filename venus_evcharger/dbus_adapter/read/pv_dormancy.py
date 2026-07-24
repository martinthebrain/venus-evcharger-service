# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded monotonic availability evidence for AC and DC PV sources."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict

PvDormancyReason = Literal["explicit-dormant-state"]
PvSourceFailureReason = Literal["source-path-unreadable"]

DEFAULT_EVIDENCE_TTL_SECONDS = 18.0 * 60.0 * 60.0
DEFAULT_OBSERVATION_RETENTION_SECONDS = 24.0 * 60.0 * 60.0
DEFAULT_MAX_OBSERVATIONS = 64
DEFAULT_ERROR_BACKOFF_SECONDS = 300.0

_DORMANT_WORDS = frozenset({"asleep", "dormant", "sleeping", "standby"})
_NEGATED_DORMANT_PHRASES = (
    "not asleep",
    "not dormant",
    "not sleeping",
    "not in standby",
)


class PvDormancyEvidencePayload(TypedDict):
    """Serialized adapter-health representation of dormant-source evidence."""

    source_id: str
    reason: PvDormancyReason
    observed_at: float


@dataclass(frozen=True, slots=True)
class PvDormancyEvidence:
    """Semantic evidence exported to adapter health without DBus identities."""

    source_id: str
    reason: PvDormancyReason
    observed_at: float

    def to_payload(self) -> PvDormancyEvidencePayload:
        return {
            "source_id": self.source_id,
            "reason": self.reason,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class PvDormancyPolicy:
    """Bound memory, evidence lifetime, and retry pressure for PV sources."""

    evidence_ttl_seconds: float = DEFAULT_EVIDENCE_TTL_SECONDS
    observation_retention_seconds: float = DEFAULT_OBSERVATION_RETENTION_SECONDS
    max_observations: int = DEFAULT_MAX_OBSERVATIONS
    error_backoff_seconds: float = DEFAULT_ERROR_BACKOFF_SECONDS


DEFAULT_PV_DORMANCY_POLICY = PvDormancyPolicy()


@dataclass(slots=True)
class _PvObservation:
    validated: bool = False
    available: bool | None = None
    failure_reason: PvSourceFailureReason | None = None
    dormant_reason: PvDormancyReason | None = None
    dormant_observed_at: float = 0.0
    dormant_observed_monotonic: float = 0.0
    last_observed_monotonic: float = 0.0
    next_probe_monotonic: float = 0.0

    def semantic_state(
        self,
    ) -> tuple[
        bool,
        bool | None,
        PvSourceFailureReason | None,
        PvDormancyReason | None,
    ]:
        return (
            self.validated,
            self.available,
            self.failure_reason,
            self.dormant_reason,
        )


class PvDormancyTracker:
    """Track validated PV paths, bounded backoff, and explicit dormancy."""

    def __init__(
        self,
        *,
        policy: PvDormancyPolicy = DEFAULT_PV_DORMANCY_POLICY,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self._evidence_ttl_seconds = max(1.0, float(policy.evidence_ttl_seconds))
        self._observation_retention_seconds = max(
            self._evidence_ttl_seconds,
            float(policy.observation_retention_seconds),
        )
        self._max_observations = max(1, int(policy.max_observations))
        self._error_backoff_seconds = max(
            0.0,
            float(policy.error_backoff_seconds),
        )
        self._monotonic = time.monotonic if monotonic is None else monotonic
        self._wall_clock = time.time if wall_clock is None else wall_clock
        self._observations: dict[str, _PvObservation] = {}
        self._revision = 0
        self._evidence_cache_key: tuple[int, frozenset[str]] | None = None
        self._evidence_cache: tuple[PvDormancyEvidence, ...] = ()

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    @property
    def revision(self) -> int:
        return self._revision

    def record_value(
        self,
        source_id: str,
        value: object,
        *,
        active_source_ids: frozenset[str] = frozenset(),
    ) -> bool:
        """Record one successful numeric sample and validate its PV path."""
        if _finite_number(value) is None:
            return False
        current = self._monotonic()
        observation = self._observation(source_id, active_source_ids)
        before = observation.semantic_state()
        observation.validated = True
        observation.available = True
        observation.failure_reason = None
        observation.dormant_reason = None
        observation.last_observed_monotonic = current
        observation.next_probe_monotonic = 0.0
        return self._record_change(observation.semantic_state() != before)

    def record_error(
        self,
        source_id: str,
        error: BaseException | str,
        *,
        active_source_ids: frozenset[str] = frozenset(),
    ) -> bool:
        """Record one failed sample without mistaking generic failure for sleep."""
        current = self._monotonic()
        observation = self._observation(source_id, active_source_ids)
        before = observation.semantic_state()
        observation.available = False
        observation.last_observed_monotonic = current
        observation.next_probe_monotonic = current + self._error_backoff_seconds
        if explicit_dormancy_error(error):
            observation.validated = True
            observation.failure_reason = None
            observation.dormant_reason = "explicit-dormant-state"
            observation.dormant_observed_at = max(0.0, float(self._wall_clock()))
            observation.dormant_observed_monotonic = current
        elif observation.dormant_reason is None:
            observation.failure_reason = "source-path-unreadable"
        return self._record_change(observation.semantic_state() != before)

    def maintain(self, active_source_ids: frozenset[str]) -> bool:
        """Expire semantic evidence and prune inactive history deterministically."""
        current = self._monotonic()
        changed = self._expire_dormancy(current)
        changed = self._prune_inactive(active_source_ids, current) or changed
        return self._record_change(changed)

    def probe_allowed(self, source_id: str) -> bool:
        observation = self._observations.get(source_id)
        return (
            observation is None
            or self._monotonic() >= observation.next_probe_monotonic
        )

    def source_validated(self, source_id: str) -> bool:
        observation = self._observations.get(source_id)
        return bool(observation is not None and observation.validated)

    def source_available(self, source_id: str) -> bool | None:
        observation = self._observations.get(source_id)
        return None if observation is None else observation.available

    def source_failure_reason(
        self,
        source_id: str,
    ) -> PvSourceFailureReason | None:
        observation = self._observations.get(source_id)
        return None if observation is None else observation.failure_reason

    def validated_source_ids(self) -> frozenset[str]:
        return frozenset(
            source_id
            for source_id, observation in self._observations.items()
            if observation.validated
        )

    def evidence(
        self,
        source_ids: frozenset[str],
    ) -> tuple[PvDormancyEvidence, ...]:
        cache_key = (self._revision, source_ids)
        if cache_key == self._evidence_cache_key:
            return self._evidence_cache
        candidates = (
            _observation_evidence(source_id, observation, source_ids)
            for source_id, observation in sorted(self._observations.items())
        )
        evidence = tuple(item for item in candidates if item is not None)
        self._evidence_cache_key = cache_key
        self._evidence_cache = evidence
        return evidence

    def _observation(
        self,
        source_id: str,
        active_source_ids: frozenset[str],
    ) -> _PvObservation:
        observation = self._observations.get(source_id)
        if observation is not None:
            return observation
        self._trim_to_capacity(active_source_ids, reserve=1)
        observation = _PvObservation()
        self._observations[source_id] = observation
        return observation

    def _expire_dormancy(self, current: float) -> bool:
        changed = False
        for observation in self._observations.values():
            if observation.dormant_reason is None:
                continue
            if (
                current - observation.dormant_observed_monotonic
                < self._evidence_ttl_seconds
            ):
                continue
            observation.dormant_reason = None
            observation.failure_reason = "source-path-unreadable"
            changed = True
        return changed

    def _prune_inactive(
        self,
        active_source_ids: frozenset[str],
        current: float,
    ) -> bool:
        expired = [
            source_id
            for source_id, observation in self._observations.items()
            if (
                source_id not in active_source_ids
                and current - observation.last_observed_monotonic
                >= self._observation_retention_seconds
            )
        ]
        for source_id in expired:
            del self._observations[source_id]
        return bool(expired)

    def _trim_to_capacity(
        self,
        active_source_ids: frozenset[str],
        *,
        reserve: int,
    ) -> None:
        target_size = max(0, self._max_observations - reserve)
        while len(self._observations) > target_size:
            source_id = min(
                self._observations,
                key=lambda candidate: (
                    candidate in active_source_ids,
                    self._observations[candidate].last_observed_monotonic,
                    candidate,
                ),
            )
            del self._observations[source_id]

    def _record_change(self, changed: bool) -> bool:
        if changed:
            self._revision += 1
        return changed


def explicit_dormancy_error(error: BaseException | str) -> bool:
    """Return true only for an explicit sleep/standby statement."""
    message = str(error).strip().lower()
    if not message or any(phrase in message for phrase in _NEGATED_DORMANT_PHRASES):
        return False
    return bool(_DORMANT_WORDS.intersection(re.findall(r"[a-z]+", message)))


def _observation_evidence(
    source_id: str,
    observation: _PvObservation,
    source_ids: frozenset[str],
) -> PvDormancyEvidence | None:
    if source_id not in source_ids:
        return None
    if not observation.validated:
        return None
    if observation.available is not False:
        return None
    if observation.dormant_reason is None:
        return None
    return PvDormancyEvidence(
        source_id=source_id,
        reason=observation.dormant_reason,
        observed_at=observation.dormant_observed_at,
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


__all__ = [
    "PvDormancyEvidence",
    "PvDormancyEvidencePayload",
    "PvDormancyPolicy",
    "PvDormancyReason",
    "PvDormancyTracker",
    "PvSourceFailureReason",
    "explicit_dormancy_error",
]
