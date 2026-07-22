# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral gateway diagnostics exposed to operational consumers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast, runtime_checkable

from venus_evcharger.ports.gateway_diagnostic_values import (
    CRITICAL_GATEWAY_DIAGNOSTIC_FIELDS,
    GATEWAY_DIAGNOSTIC_FIELD_NAMES,
    GatewayDiagnosticFieldName,
    GatewayDiagnosticSample,
    GatewayDiagnosticStatus,
    gateway_diagnostic_field_name,
)
from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    exact_mapping,
    member_text,
    non_negative_float,
    non_negative_int,
    positive_float,
    text,
)

GATEWAY_DIAGNOSTICS_SCHEMA_VERSION = 1

GatewayHealthState = Literal["unknown", "ok", "degraded", "protective", "unavailable"]
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

_HEALTH_STATES = frozenset({"unknown", "ok", "degraded", "protective", "unavailable"})
_DISCOVERY_STATES = frozenset(
    {"unknown", "disabled", "idle", "running", "degraded", "protective", "error", "unavailable"}
)


class GatewayDiagnosticsUnavailable(RuntimeError):
    """Raised when a diagnostics transport cannot provide a valid snapshot."""


@dataclass(frozen=True, slots=True)
class GatewayHealthSummary:
    """Operational gateway health without transport- or DBus-specific details."""

    state: GatewayHealthState
    stale: bool
    timeouts_60s: int
    average_latency_ms: float
    maximum_latency_ms: float
    pending_gateway_commands: int
    pending_core_commands: int
    maximum_event_loop_gap_ms_60s: float
    last_success_at: float
    last_error_code: str = ""

    def __post_init__(self) -> None:
        _health_state(self.state)
        boolean(self.stale, "gateway health stale")
        non_negative_int(self.timeouts_60s, "gateway health timeouts_60s")
        average = non_negative_float(self.average_latency_ms, "gateway health average_latency_ms")
        maximum = non_negative_float(self.maximum_latency_ms, "gateway health maximum_latency_ms")
        _validate_latency_bounds(average, maximum)
        non_negative_int(self.pending_gateway_commands, "gateway health pending_gateway_commands")
        non_negative_int(self.pending_core_commands, "gateway health pending_core_commands")
        non_negative_float(
            self.maximum_event_loop_gap_ms_60s,
            "gateway health maximum_event_loop_gap_ms_60s",
        )
        non_negative_float(self.last_success_at, "gateway health last_success_at")
        text(self.last_error_code, "gateway health last_error_code", allow_empty=True)

    def to_payload(self) -> dict[str, object]:
        return {
            "state": self.state,
            "stale": self.stale,
            "timeouts_60s": self.timeouts_60s,
            "average_latency_ms": self.average_latency_ms,
            "maximum_latency_ms": self.maximum_latency_ms,
            "pending_gateway_commands": self.pending_gateway_commands,
            "pending_core_commands": self.pending_core_commands,
            "maximum_event_loop_gap_ms_60s": self.maximum_event_loop_gap_ms_60s,
            "last_success_at": self.last_success_at,
            "last_error_code": self.last_error_code,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayHealthSummary:
        names = {
            "state",
            "stale",
            "timeouts_60s",
            "average_latency_ms",
            "maximum_latency_ms",
            "pending_gateway_commands",
            "pending_core_commands",
            "maximum_event_loop_gap_ms_60s",
            "last_success_at",
            "last_error_code",
        }
        item = exact_mapping(payload, "gateway health summary", names)
        return cls(
            state=_health_state(item["state"]),
            stale=boolean(item["stale"], "gateway health stale"),
            timeouts_60s=non_negative_int(item["timeouts_60s"], "gateway health timeouts_60s"),
            average_latency_ms=non_negative_float(
                item["average_latency_ms"], "gateway health average_latency_ms"
            ),
            maximum_latency_ms=non_negative_float(
                item["maximum_latency_ms"], "gateway health maximum_latency_ms"
            ),
            pending_gateway_commands=non_negative_int(
                item["pending_gateway_commands"], "gateway health pending_gateway_commands"
            ),
            pending_core_commands=non_negative_int(
                item["pending_core_commands"], "gateway health pending_core_commands"
            ),
            maximum_event_loop_gap_ms_60s=non_negative_float(
                item["maximum_event_loop_gap_ms_60s"],
                "gateway health maximum_event_loop_gap_ms_60s",
            ),
            last_success_at=non_negative_float(
                item["last_success_at"], "gateway health last_success_at"
            ),
            last_error_code=text(
                item["last_error_code"], "gateway health last_error_code", allow_empty=True
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
        _validate_discovery_configuration(enabled, state, discovered, unusable)

    def to_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "state": self.state,
            "pending_work": self.pending_work,
            "discovered_source_count": self.discovered_source_count,
            "unusable_source_count": self.unusable_source_count,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayDiscoverySummary:
        names = {
            "enabled",
            "state",
            "pending_work",
            "discovered_source_count",
            "unusable_source_count",
        }
        item = exact_mapping(payload, "gateway discovery summary", names)
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
        )


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticsSnapshot:
    """Coherent semantic snapshot consumed by probes and forensic tooling."""

    sequence: int
    captured_at: float
    health: GatewayHealthSummary
    discovery: GatewayDiscoverySummary
    ev_charger: tuple[GatewayDiagnosticSample, ...]
    schema_version: int = GATEWAY_DIAGNOSTICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        non_negative_int(self.sequence, "gateway diagnostics sequence")
        positive_float(self.captured_at, "gateway diagnostics captured_at")
        _health_summary(self.health)
        _discovery_summary(self.discovery)
        _validate_samples(_sample_tuple(self.ev_charger))

    def sample(self, name: GatewayDiagnosticFieldName) -> GatewayDiagnosticSample:
        normalized = gateway_diagnostic_field_name(name)
        return next(sample for sample in self.ev_charger if sample.name == normalized)

    def critical_unavailable_fields(self) -> tuple[GatewayDiagnosticFieldName, ...]:
        unavailable = {"unavailable", "error", "unknown"}
        return tuple(
            name
            for name in CRITICAL_GATEWAY_DIAGNOSTIC_FIELDS
            if self.sample(name).status in unavailable
        )

    def age_seconds(self, now: float) -> float:
        timestamp = non_negative_float(now, "gateway diagnostics current time")
        return max(0.0, timestamp - self.captured_at)

    def is_fresh(self, now: float, max_age_seconds: float) -> bool:
        maximum = non_negative_float(max_age_seconds, "gateway diagnostics max_age_seconds")
        return self.age_seconds(now) <= maximum

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "health": self.health.to_payload(),
            "discovery": self.discovery.to_payload(),
            "ev_charger": [sample.to_payload() for sample in self.ev_charger],
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayDiagnosticsSnapshot:
        names = {"schema_version", "sequence", "captured_at", "health", "discovery", "ev_charger"}
        item = exact_mapping(payload, "gateway diagnostics snapshot", names)
        return cls(
            sequence=non_negative_int(item["sequence"], "gateway diagnostics sequence"),
            captured_at=positive_float(item["captured_at"], "gateway diagnostics captured_at"),
            health=GatewayHealthSummary.from_payload(item["health"]),
            discovery=GatewayDiscoverySummary.from_payload(item["discovery"]),
            ev_charger=_samples(item["ev_charger"]),
            schema_version=_schema_version(item["schema_version"]),
        )


@runtime_checkable
class GatewayDiagnosticsReader(Protocol):  # pragma: no cover - declarative port
    """Read the latest semantic diagnostics regardless of its transport."""

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot: ...


def _validate_latency_bounds(average: float, maximum: float) -> None:
    if maximum < average:
        raise ValueError("gateway health maximum_latency_ms must be at least average_latency_ms")


def _validate_discovery_configuration(
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


def _validate_samples(samples: tuple[GatewayDiagnosticSample, ...]) -> None:
    names = [sample.name for sample in samples]
    if len(names) != len(set(names)):
        raise ValueError("gateway diagnostics ev_charger contains duplicate fields")
    if frozenset(names) != GATEWAY_DIAGNOSTIC_FIELD_NAMES:
        raise ValueError("gateway diagnostics ev_charger must contain every semantic field exactly once")


def _sample_tuple(value: object) -> tuple[GatewayDiagnosticSample, ...]:
    if not isinstance(value, tuple):
        raise TypeError("gateway diagnostics ev_charger must be a tuple")
    untyped = cast(tuple[object, ...], value)
    if any(not isinstance(sample, GatewayDiagnosticSample) for sample in untyped):
        raise TypeError("gateway diagnostics ev_charger contains an invalid sample")
    return cast(tuple[GatewayDiagnosticSample, ...], untyped)


def _samples(value: object) -> tuple[GatewayDiagnosticSample, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("gateway diagnostics ev_charger must be an array")
    if not isinstance(value, Sequence):
        raise TypeError("gateway diagnostics ev_charger must be an array")
    untyped = cast(Sequence[object], value)
    return tuple(GatewayDiagnosticSample.from_payload(item) for item in untyped)


def _schema_version(value: object) -> int:
    version = non_negative_int(value, "gateway diagnostics schema_version")
    if version != GATEWAY_DIAGNOSTICS_SCHEMA_VERSION:
        raise ValueError("gateway diagnostics has an unsupported schema_version")
    return version


def _health_summary(value: object) -> GatewayHealthSummary:
    if not isinstance(value, GatewayHealthSummary):
        raise TypeError("gateway diagnostics health must be GatewayHealthSummary")
    return value


def _discovery_summary(value: object) -> GatewayDiscoverySummary:
    if not isinstance(value, GatewayDiscoverySummary):
        raise TypeError("gateway diagnostics discovery must be GatewayDiscoverySummary")
    return value


def _health_state(value: object) -> GatewayHealthState:
    normalized = member_text(value, _HEALTH_STATES, "gateway health state")
    return cast(GatewayHealthState, normalized)


def _discovery_state(value: object) -> GatewayDiscoveryState:
    normalized = member_text(value, _DISCOVERY_STATES, "gateway discovery state")
    return cast(GatewayDiscoveryState, normalized)


__all__ = [
    "GATEWAY_DIAGNOSTICS_SCHEMA_VERSION",
    "GatewayDiagnosticFieldName",
    "GatewayDiagnosticSample",
    "GatewayDiagnosticStatus",
    "GatewayDiagnosticsReader",
    "GatewayDiagnosticsSnapshot",
    "GatewayDiagnosticsUnavailable",
    "GatewayDiscoveryState",
    "GatewayDiscoverySummary",
    "GatewayHealthState",
    "GatewayHealthSummary",
]
