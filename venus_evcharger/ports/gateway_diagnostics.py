# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregate and wire contract for transport-neutral gateway diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeGuard, runtime_checkable

from venus_evcharger.ports import gateway_diagnostic_discovery as diagnostic_discovery
from venus_evcharger.ports import gateway_diagnostic_health as diagnostic_health
from venus_evcharger.ports import gateway_diagnostic_values as diagnostic_values
from venus_evcharger.ports.gateway_diagnostics_validation import (
    exact_mapping,
    is_object_tuple,
    is_string_object_mapping,
    non_negative_float,
    non_negative_int,
    object_sequence,
    positive_float,
)

GATEWAY_DIAGNOSTICS_SCHEMA_VERSION = 5
_SUPPORTED_GATEWAY_DIAGNOSTICS_SCHEMA_VERSIONS = frozenset({3, 4, 5})


class GatewayDiagnosticsUnavailable(RuntimeError):
    """Raised when a diagnostics transport cannot provide a valid snapshot."""


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticsSnapshot:
    """Coherent semantic snapshot consumed by probes and forensic tooling."""

    sequence: int
    captured_at: float
    captured_monotonic: float
    health: diagnostic_health.GatewayHealthSummary
    discovery: diagnostic_discovery.GatewayDiscoverySummary
    publication: diagnostic_health.GatewayPublicationSummary
    ev_charger: tuple[diagnostic_values.GatewayDiagnosticSample, ...]
    schema_version: int = GATEWAY_DIAGNOSTICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        non_negative_int(self.sequence, "gateway diagnostics sequence")
        positive_float(self.captured_at, "gateway diagnostics captured_at")
        positive_float(
            self.captured_monotonic,
            "gateway diagnostics captured_monotonic",
        )
        _health_summary(self.health)
        _discovery_summary(self.discovery)
        _publication_summary(self.publication)
        samples = _sample_tuple(self.ev_charger)
        _validate_samples(samples)

    def sample(
        self, name: diagnostic_values.GatewayDiagnosticFieldName
    ) -> diagnostic_values.GatewayDiagnosticSample:
        normalized = diagnostic_values.gateway_diagnostic_field_name(name)
        return next(sample for sample in self.ev_charger if sample.name == normalized)

    def critical_unavailable_fields(
        self,
    ) -> tuple[diagnostic_values.GatewayDiagnosticFieldName, ...]:
        unavailable = {"unavailable", "error", "unknown"}
        return tuple(
            name
            for name in diagnostic_values.CRITICAL_GATEWAY_DIAGNOSTIC_FIELDS
            if self.sample(name).status in unavailable
        )

    def age_seconds(self, monotonic_at: float) -> float:
        """Return snapshot age in the process-independent monotonic clock domain."""
        timestamp = non_negative_float(
            monotonic_at,
            "gateway diagnostics current monotonic time",
        )
        if timestamp < self.captured_monotonic:
            raise ValueError(
                "gateway diagnostics current monotonic time precedes captured_monotonic"
            )
        return timestamp - self.captured_monotonic

    def is_fresh(self, monotonic_at: float, max_age_seconds: float) -> bool:
        maximum = non_negative_float(max_age_seconds, "gateway diagnostics max_age_seconds")
        return self.age_seconds(monotonic_at) <= maximum

    def to_payload(self) -> dict[str, object]:
        health = self.health.to_payload()
        if self.schema_version < 5:
            health.pop("operational_state")
            health.pop("performance_state")
            health.pop("resource_state")
            health.pop("protective_cause")
            health.pop("resource_evidence")
        if self.schema_version == 3:
            health.pop("active_protective_trigger")
            health.pop("last_protective_trigger")
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "captured_monotonic": self.captured_monotonic,
            "health": health,
            "discovery": self.discovery.to_payload(),
            "publication": self.publication.to_payload(),
            "ev_charger": [sample.to_payload() for sample in self.ev_charger],
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayDiagnosticsSnapshot:
        item = _snapshot_mapping(payload)
        return cls(
            sequence=non_negative_int(item["sequence"], "gateway diagnostics sequence"),
            captured_at=positive_float(item["captured_at"], "gateway diagnostics captured_at"),
            captured_monotonic=positive_float(
                item["captured_monotonic"],
                "gateway diagnostics captured_monotonic",
            ),
            health=diagnostic_health.GatewayHealthSummary.from_payload(item["health"]),
            discovery=diagnostic_discovery.GatewayDiscoverySummary.from_payload(item["discovery"]),
            publication=diagnostic_health.GatewayPublicationSummary.from_payload(
                item["publication"]
            ),
            ev_charger=_samples(item["ev_charger"]),
            schema_version=_schema_version(item["schema_version"]),
        )


@runtime_checkable
class GatewayDiagnosticsReader(Protocol):
    """Read the latest semantic diagnostics regardless of its transport."""

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot: ...


def _validate_samples(samples: tuple[diagnostic_values.GatewayDiagnosticSample, ...]) -> None:
    names = [sample.name for sample in samples]
    if len(names) != len(set(names)):
        raise ValueError("gateway diagnostics ev_charger contains duplicate fields")
    if frozenset(names) != diagnostic_values.GATEWAY_DIAGNOSTIC_FIELD_NAMES:
        raise ValueError("gateway diagnostics ev_charger must contain every semantic field exactly once")


def _sample_tuple(value: object) -> tuple[diagnostic_values.GatewayDiagnosticSample, ...]:
    if not _is_sample_tuple(value):
        raise TypeError("gateway diagnostics ev_charger contains an invalid sample")
    return value


def _is_sample_tuple(
    value: object,
) -> TypeGuard[tuple[diagnostic_values.GatewayDiagnosticSample, ...]]:
    return (
        is_object_tuple(value)
        and all(isinstance(sample, diagnostic_values.GatewayDiagnosticSample) for sample in value)
    )


def _samples(value: object) -> tuple[diagnostic_values.GatewayDiagnosticSample, ...]:
    items = object_sequence(value, "gateway diagnostics ev_charger")
    return tuple(diagnostic_values.GatewayDiagnosticSample.from_payload(item) for item in items)


def _snapshot_mapping(payload: object) -> Mapping[str, object]:
    if not is_string_object_mapping(payload):
        raise TypeError("gateway diagnostics snapshot must be an object with string keys")
    # Diagnostics live under /run. After a schema change the active producer
    # replaces them on its next tick; inventing a monotonic timestamp for an
    # older document would make freshness and ordering unsafe.
    if payload.get("schema_version") not in _SUPPORTED_GATEWAY_DIAGNOSTICS_SCHEMA_VERSIONS:
        raise ValueError("gateway diagnostics has an unsupported schema_version")
    return exact_mapping(
        payload,
        "gateway diagnostics snapshot",
        {
            "schema_version",
            "sequence",
            "captured_at",
            "captured_monotonic",
            "health",
            "discovery",
            "publication",
            "ev_charger",
        },
    )


def _schema_version(value: object) -> int:
    version = non_negative_int(value, "gateway diagnostics schema_version")
    if version not in _SUPPORTED_GATEWAY_DIAGNOSTICS_SCHEMA_VERSIONS:
        raise ValueError("gateway diagnostics has an unsupported schema_version")
    return version


def _health_summary(value: object) -> diagnostic_health.GatewayHealthSummary:
    if not isinstance(value, diagnostic_health.GatewayHealthSummary):
        raise TypeError("gateway diagnostics health must be GatewayHealthSummary")
    return value


def _discovery_summary(value: object) -> diagnostic_discovery.GatewayDiscoverySummary:
    if not isinstance(value, diagnostic_discovery.GatewayDiscoverySummary):
        raise TypeError("gateway diagnostics discovery must be GatewayDiscoverySummary")
    return value


def _publication_summary(value: object) -> diagnostic_health.GatewayPublicationSummary:
    if not isinstance(value, diagnostic_health.GatewayPublicationSummary):
        raise TypeError("gateway diagnostics publication must be GatewayPublicationSummary")
    return value


__all__ = [
    "GATEWAY_DIAGNOSTICS_SCHEMA_VERSION",
    "GatewayDiagnosticsReader",
    "GatewayDiagnosticsSnapshot",
    "GatewayDiagnosticsUnavailable",
]
