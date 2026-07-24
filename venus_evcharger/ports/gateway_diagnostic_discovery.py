# SPDX-License-Identifier: GPL-3.0-or-later
"""Discovery and source contracts for semantic gateway diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    exact_mapping,
    is_object_tuple,
    is_string_object_mapping,
    member_text,
    non_negative_int,
    object_sequence,
    text,
)

GatewayDiscoveryState = Literal[
    "unknown",
    "disabled",
    "idle",
    "running",
    "degraded",
    "protective",
    "error",
    "unavailable",
]
GatewaySourceAvailability = Literal["available", "dormant", "unavailable", "unknown"]

_DISCOVERY_STATES = frozenset(
    {"unknown", "disabled", "idle", "running", "degraded", "protective", "error", "unavailable"}
)
_SOURCE_AVAILABILITIES = frozenset({"available", "dormant", "unavailable", "unknown"})
_SOURCE_KINDS = frozenset({"grid", "pv_ac", "pv_dc", "battery"})


@dataclass(frozen=True, slots=True)
class GatewaySourceSummary:
    """Semantic availability of one gateway-discovered energy source."""

    source_id: str
    kind: str
    availability: GatewaySourceAvailability
    reason_code: str = ""

    def __post_init__(self) -> None:
        text(self.source_id, "gateway source source_id")
        member_text(self.kind, _SOURCE_KINDS, "gateway source kind")
        availability = _source_availability(self.availability)
        reason = text(self.reason_code, "gateway source reason_code", allow_empty=True)
        if availability != "available" and not reason:
            raise ValueError(f"{availability} gateway source requires reason_code")

    def to_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "availability": self.availability,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewaySourceSummary:
        item = exact_mapping(
            payload,
            "gateway source summary",
            {"source_id", "kind", "availability", "reason_code"},
        )
        return cls(
            source_id=text(item["source_id"], "gateway source source_id"),
            kind=member_text(item["kind"], _SOURCE_KINDS, "gateway source kind"),
            availability=_source_availability(item["availability"]),
            reason_code=text(
                item["reason_code"],
                "gateway source reason_code",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class GatewayDiscoverySummary:
    """Bounded summary of gateway-owned discovery and inspection work."""

    enabled: bool
    state: GatewayDiscoveryState
    pending_work: int
    discovered_source_count: int
    unusable_source_count: int
    dormant_source_count: int = 0
    sources: tuple[GatewaySourceSummary, ...] = ()

    def __post_init__(self) -> None:
        enabled = boolean(self.enabled, "gateway discovery enabled")
        state = _discovery_state(self.state)
        non_negative_int(self.pending_work, "gateway discovery pending_work")
        discovered = non_negative_int(
            self.discovered_source_count, "gateway discovery discovered_source_count"
        )
        unusable = non_negative_int(
            self.unusable_source_count, "gateway discovery unusable_source_count"
        )
        dormant = non_negative_int(
            self.dormant_source_count,
            "gateway discovery dormant_source_count",
        )
        sources = _source_tuple(self.sources)
        _validate_configuration(enabled, state, discovered, unusable)
        _validate_source_counts(discovered, unusable, dormant, sources)

    def to_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "state": self.state,
            "pending_work": self.pending_work,
            "discovered_source_count": self.discovered_source_count,
            "unusable_source_count": self.unusable_source_count,
            "dormant_source_count": self.dormant_source_count,
            "sources": [source.to_payload() for source in self.sources],
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayDiscoverySummary:
        names = {
            "enabled",
            "state",
            "pending_work",
            "discovered_source_count",
            "unusable_source_count",
            "dormant_source_count",
            "sources",
        }
        item = _discovery_mapping(payload, names)
        return cls(
            enabled=boolean(item["enabled"], "gateway discovery enabled"),
            state=_discovery_state(item["state"]),
            pending_work=non_negative_int(item["pending_work"], "gateway discovery pending_work"),
            discovered_source_count=non_negative_int(
                item["discovered_source_count"], "gateway discovery discovered_source_count"
            ),
            unusable_source_count=non_negative_int(
                item["unusable_source_count"], "gateway discovery unusable_source_count"
            ),
            dormant_source_count=non_negative_int(
                item["dormant_source_count"],
                "gateway discovery dormant_source_count",
            ),
            sources=_sources(item["sources"]),
        )


def _validate_configuration(
    enabled: bool,
    state: GatewayDiscoveryState,
    discovered: int,
    unusable: int,
) -> None:
    _validate_discovery_counts(discovered, unusable)
    _validate_discovery_state(enabled, state)


def _validate_discovery_counts(discovered: int, unusable: int) -> None:
    if unusable > discovered:
        raise ValueError("gateway discovery unusable_source_count exceeds discovered_source_count")


def _validate_discovery_state(enabled: bool, state: GatewayDiscoveryState) -> None:
    if not enabled and state != "disabled":
        raise ValueError("disabled gateway discovery requires state='disabled'")
    if enabled and state == "disabled":
        raise ValueError("enabled gateway discovery must not report state='disabled'")


def _validate_source_counts(
    discovered: int,
    unusable: int,
    dormant: int,
    sources: tuple[GatewaySourceSummary, ...],
) -> None:
    _validate_availability_bounds(discovered, unusable, dormant)
    if sources:
        _validate_source_details(discovered, unusable, dormant, sources)


def _validate_availability_bounds(discovered: int, unusable: int, dormant: int) -> None:
    if dormant > discovered:
        raise ValueError("gateway discovery dormant_source_count exceeds discovered_source_count")
    if unusable + dormant > discovered:
        raise ValueError("gateway discovery unavailable and dormant counts exceed discovered sources")


def _validate_source_details(
    discovered: int,
    unusable: int,
    dormant: int,
    sources: tuple[GatewaySourceSummary, ...],
) -> None:
    if len(sources) != discovered:
        raise ValueError("gateway discovery source count does not match sources")
    unavailable = _unavailable_source_count(sources)
    dormant_count = _dormant_source_count(sources)
    if unavailable != unusable or dormant_count != dormant:
        raise ValueError("gateway discovery availability counts do not match sources")


def _unavailable_source_count(sources: tuple[GatewaySourceSummary, ...]) -> int:
    return sum(source.availability in {"unavailable", "unknown"} for source in sources)


def _dormant_source_count(sources: tuple[GatewaySourceSummary, ...]) -> int:
    return sum(source.availability == "dormant" for source in sources)


def _sources(value: object) -> tuple[GatewaySourceSummary, ...]:
    items = object_sequence(value, "gateway diagnostics sources")
    return tuple(GatewaySourceSummary.from_payload(item) for item in items)


def _source_tuple(value: object) -> tuple[GatewaySourceSummary, ...]:
    if not _is_source_tuple(value):
        raise TypeError("gateway diagnostics sources contains an invalid source")
    _validate_unique_source_ids(value)
    return value


def _is_source_tuple(value: object) -> TypeGuard[tuple[GatewaySourceSummary, ...]]:
    return (
        is_object_tuple(value)
        and all(isinstance(source, GatewaySourceSummary) for source in value)
    )


def _validate_unique_source_ids(sources: tuple[GatewaySourceSummary, ...]) -> None:
    source_ids = tuple(source.source_id for source in sources)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("gateway diagnostics sources contains duplicate source_id values")


def _discovery_mapping(payload: object, current_names: set[str]) -> Mapping[str, object]:
    if is_string_object_mapping(payload) and set(payload) == current_names:
        return payload
    legacy_names = {
        "enabled",
        "state",
        "pending_work",
        "discovered_source_count",
        "unusable_source_count",
    }
    legacy = exact_mapping(payload, "gateway discovery summary", legacy_names)
    return {**legacy, "dormant_source_count": 0, "sources": []}


def _discovery_state(value: object) -> GatewayDiscoveryState:
    if not _is_discovery_state(value):
        raise ValueError("gateway discovery state is invalid")
    return value


def _is_discovery_state(value: object) -> TypeGuard[GatewayDiscoveryState]:
    return isinstance(value, str) and value in _DISCOVERY_STATES


def _source_availability(value: object) -> GatewaySourceAvailability:
    if not _is_source_availability(value):
        raise ValueError("gateway source availability is invalid")
    return value


def _is_source_availability(value: object) -> TypeGuard[GatewaySourceAvailability]:
    return isinstance(value, str) and value in _SOURCE_AVAILABILITIES


__all__ = [
    "GatewayDiscoveryState",
    "GatewayDiscoverySummary",
    "GatewaySourceAvailability",
    "GatewaySourceSummary",
]
