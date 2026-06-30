# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregate read state for the dedicated DBus adapter reader."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import SupportsFloat, SupportsIndex, TypedDict

PV_TOTAL_AGGREGATE = "pv-total"
OPTIONAL_MEMBER_FAILED = object()


class AggregatePayload(TypedDict):
    value: float
    source: str
    confidence: float
    last_error: str


def aggregate_signature_members(signature: object, aggregate: str) -> list[tuple[str, str]] | None:  # pragma: no mutate block
    if not isinstance(signature, tuple):
        return None
    if len(signature) != 2:
        return None
    name, members = signature
    if name != aggregate:
        return None
    if not isinstance(members, tuple):
        return None
    return _member_pairs(members)


def _member_pairs(raw_members: tuple[object, ...]) -> list[tuple[str, str]] | None:  # pragma: no mutate block
    pairs: list[tuple[str, str]] = []
    for item in raw_members:
        if not isinstance(item, tuple) or len(item) != 2:
            return None
        service, path = item
        pairs.append((str(service), str(path)))
    return pairs


def aggregate_member_float(value: object) -> float:  # pragma: no mutate block
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (str, bytes, SupportsFloat, SupportsIndex)):
        return float(value)
    raise TypeError(f"Aggregate member is not numeric: {value!r}")


@dataclass
class AggregateState:
    signature: tuple[object, ...]
    empty_confidence: float
    index: int = 0
    total: float = 0.0
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record_member(self, service: str, path: str, value: object) -> None:  # pragma: no mutate block
        if value is None:
            return
        self.total += aggregate_member_float(value)
        self.sources.append(f"{service}{path}")

    def record_error(self, service: str, path: str, error: BaseException) -> None:  # pragma: no mutate block
        self.errors.append(f"{service}{path}: {error}")

    def complete(self, member_count: int) -> bool:
        return self.index >= member_count

    def payload(self, key: str) -> AggregatePayload:  # pragma: no mutate block
        return {
            "value": self.total,
            "source": ",".join(self.sources) if self.sources else key,
            "confidence": 1.0 if self.sources else self.empty_confidence,
            "last_error": "; ".join(str(error) for error in self.errors),
        }


class AggregateStore:
    def __init__(self) -> None:
        self._states: dict[str, AggregateState] = {}

    def has_pending(self) -> bool:
        return bool(self._states)

    def discard(self, key: str) -> None:
        self._states.pop(key, None)

    def state_for(self, key: str, signature: tuple[object, ...], empty_confidence: float) -> AggregateState:
        state = self._states.get(key)
        if state is not None and state.signature == signature:
            return state
        state = AggregateState(signature=signature, empty_confidence=empty_confidence)
        self._states[key] = state
        return state

    def signature_members(self, key: str, aggregate: str) -> list[tuple[str, str]] | None:
        state = self._states.get(key)
        return None if state is None else aggregate_signature_members(state.signature, aggregate)
