# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregate and wire contract for transport-neutral gateway diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeGuard, runtime_checkable

from venus_evcharger.core.contracts import timestamp_age_within
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
    timestamp_not_after,
)

GATEWAY_DIAGNOSTICS_SCHEMA_VERSION = 2
LEGACY_GATEWAY_DIAGNOSTICS_SCHEMA_VERSION = 1
_LEGACY_PUBLICATION_MAX_AGE_SECONDS = 1.0


class GatewayDiagnosticsUnavailable(RuntimeError):
    """Raised when a diagnostics transport cannot provide a valid snapshot."""


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticsSnapshot:
    """Coherent semantic snapshot consumed by probes and forensic tooling."""

    sequence: int
    captured_at: float
    health: diagnostic_health.GatewayHealthSummary
    discovery: diagnostic_discovery.GatewayDiscoverySummary
    publication: diagnostic_health.GatewayPublicationSummary
    ev_charger: tuple[diagnostic_values.GatewayDiagnosticSample, ...]
    schema_version: int = GATEWAY_DIAGNOSTICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        non_negative_int(self.sequence, "gateway diagnostics sequence")
        captured_at = positive_float(self.captured_at, "gateway diagnostics captured_at")
        _health_summary(self.health)
        _discovery_summary(self.discovery)
        _publication_summary(self.publication)
        samples = _sample_tuple(self.ev_charger)
        _validate_samples(samples)
        _validate_snapshot_timestamps(
            captured_at,
            self.health,
            self.publication,
            samples,
        )

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

    def age_seconds(self, now: float) -> float:
        timestamp = non_negative_float(now, "gateway diagnostics current time")
        timestamp_not_after(
            self.captured_at,
            timestamp,
            "gateway diagnostics captured_at",
        )
        return timestamp - self.captured_at

    def is_fresh(self, now: float, max_age_seconds: float) -> bool:
        timestamp = non_negative_float(now, "gateway diagnostics current time")
        maximum = non_negative_float(max_age_seconds, "gateway diagnostics max_age_seconds")
        return timestamp_age_within(self.captured_at, timestamp, maximum)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "health": self.health.to_payload(),
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
            health=diagnostic_health.GatewayHealthSummary.from_payload(item["health"]),
            discovery=diagnostic_discovery.GatewayDiscoverySummary.from_payload(item["discovery"]),
            publication=diagnostic_health.GatewayPublicationSummary.from_payload(
                item["publication"]
            ),
            ev_charger=_samples(item["ev_charger"]),
            schema_version=GATEWAY_DIAGNOSTICS_SCHEMA_VERSION,
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


def _validate_snapshot_timestamps(
    captured_at: float,
    health: diagnostic_health.GatewayHealthSummary,
    publication: diagnostic_health.GatewayPublicationSummary,
    samples: tuple[diagnostic_values.GatewayDiagnosticSample, ...],
) -> None:
    timestamp_not_after(
        health.last_success_at,
        captured_at,
        "gateway health last_success_at",
    )
    timestamp_not_after(
        publication.heartbeat_at,
        captured_at,
        "gateway publication heartbeat_at",
    )
    for sample in samples:
        timestamp_not_after(
            sample.changed_at,
            captured_at,
            f"{sample.name} diagnostic changed_at",
        )
        timestamp_not_after(
            sample.confirmed_at,
            captured_at,
            f"{sample.name} diagnostic confirmed_at",
        )


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
    base_names = {"schema_version", "sequence", "captured_at", "health", "discovery", "ev_charger"}
    if not is_string_object_mapping(payload):
        raise TypeError("gateway diagnostics snapshot must be an object with string keys")
    if payload.get("schema_version") == GATEWAY_DIAGNOSTICS_SCHEMA_VERSION:
        return exact_mapping(
            payload,
            "gateway diagnostics snapshot",
            base_names | {"publication"},
        )
    return _migrate_legacy_snapshot(payload, base_names)


def _migrate_legacy_snapshot(
    payload: object,
    base_names: set[str],
) -> Mapping[str, object]:
    legacy = exact_mapping(payload, "gateway diagnostics snapshot", base_names)
    if _legacy_schema_version(legacy["schema_version"]) != LEGACY_GATEWAY_DIAGNOSTICS_SCHEMA_VERSION:
        raise ValueError("gateway diagnostics has an unsupported schema_version")
    samples = _samples(legacy["ev_charger"])
    captured_at = positive_float(legacy["captured_at"], "gateway diagnostics captured_at")
    health = diagnostic_health.GatewayHealthSummary.from_payload(legacy["health"])
    migrated_discovery = diagnostic_discovery.GatewayDiscoverySummary.from_payload(
        legacy["discovery"]
    ).to_payload()
    return {
        **legacy,
        "schema_version": GATEWAY_DIAGNOSTICS_SCHEMA_VERSION,
        "discovery": migrated_discovery,
        "publication": _legacy_publication_payload(samples, captured_at, health),
    }


def _legacy_publication_payload(
    samples: tuple[diagnostic_values.GatewayDiagnosticSample, ...],
    captured_at: float,
    health: diagnostic_health.GatewayHealthSummary,
) -> dict[str, object]:
    heartbeat_at = max((sample.confirmed_at for sample in samples), default=0.0)
    registered = heartbeat_at > 0.0
    return {
        "registered": registered,
        "heartbeat_at": heartbeat_at if registered else 0.0,
        "stale": _legacy_publication_stale(
            registered,
            heartbeat_at,
            captured_at,
            health,
            samples,
        ),
    }


def _legacy_publication_stale(
    registered: bool,
    heartbeat_at: float,
    captured_at: float,
    health: diagnostic_health.GatewayHealthSummary,
    samples: tuple[diagnostic_values.GatewayDiagnosticSample, ...],
) -> bool:
    if not registered or health.stale:
        return True
    if any(sample.status == "stale" for sample in samples):
        return True
    return not timestamp_age_within(
        heartbeat_at,
        captured_at,
        _LEGACY_PUBLICATION_MAX_AGE_SECONDS,
    )


def _schema_version(value: object) -> int:
    version = non_negative_int(value, "gateway diagnostics schema_version")
    if version != GATEWAY_DIAGNOSTICS_SCHEMA_VERSION:
        raise ValueError("gateway diagnostics has an unsupported schema_version")
    return version


def _legacy_schema_version(value: object) -> int:
    return non_negative_int(value, "gateway diagnostics schema_version")


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
    "LEGACY_GATEWAY_DIAGNOSTICS_SCHEMA_VERSION",
    "GatewayDiagnosticsReader",
    "GatewayDiagnosticsSnapshot",
    "GatewayDiagnosticsUnavailable",
]
