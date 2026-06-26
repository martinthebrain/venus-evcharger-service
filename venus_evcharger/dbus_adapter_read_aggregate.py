# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregate read state for the dedicated DBus adapter reader."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

PV_TOTAL_AGGREGATE = "pv-total"
OPTIONAL_MEMBER_FAILED = object()


def aggregate_signature_members(signature: Any, aggregate: str) -> list[tuple[str, str]] | None:  # pragma: no mutate block
    if not isinstance(signature, tuple):
        return None
    try:
        name, members = signature
    except ValueError:
        return None
    if name != aggregate:
        return None
    return [(str(service), str(path)) for service, path in members]


@dataclass
class AggregateState:
    signature: tuple[Any, ...]
    empty_confidence: float
    index: int = 0
    total: float = 0.0
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record_member(self, service: str, path: str, value: Any) -> None:  # pragma: no mutate block
        if value is None:
            return
        self.total += float(value)
        self.sources.append(f"{service}{path}")

    def record_error(self, service: str, path: str, error: BaseException) -> None:  # pragma: no mutate block
        self.errors.append(f"{service}{path}: {error}")

    def complete(self, member_count: int) -> bool:
        return self.index >= member_count

    def payload(self, key: str) -> Mapping[str, Any]:  # pragma: no mutate block
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

    def state_for(self, key: str, signature: tuple[Any, ...], empty_confidence: float) -> AggregateState:
        state = self._states.get(key)
        if state is not None and state.signature == signature:
            return state
        state = AggregateState(signature=signature, empty_confidence=empty_confidence)
        self._states[key] = state
        return state

    def signature_members(self, key: str, aggregate: str) -> list[tuple[str, str]] | None:
        state = self._states.get(key)
        return None if state is None else aggregate_signature_members(state.signature, aggregate)
