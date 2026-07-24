# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared semantic gateway-diagnostics fixtures for direct contract tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.ports.gateway_diagnostic_discovery import (
    GatewayDiscoveryState,
    GatewayDiscoverySummary,
)
from venus_evcharger.ports.gateway_diagnostic_health import (
    GatewayHealthState,
    GatewayHealthSummary,
    GatewayPublicationSummary,
)
from venus_evcharger.ports.gateway_diagnostic_values import (
    GatewayDiagnosticApplicability,
    GatewayDiagnosticFieldName,
    GatewayDiagnosticSample,
    GatewayDiagnosticStatus,
)
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsSnapshot

_VALUES: Mapping[GatewayDiagnosticFieldName, str | int | float | bool] = {
    "operating_mode": 2,
    "charging_enabled": True,
    "auto_start_enabled": True,
    "ac_power_w": 1234.5,
    "charger_state_code": 2,
    "decision_reason": "scheduled-window",
    "decision_state": "charging",
    "last_health_reason": "healthy",
    "runtime_overrides_active": False,
    "runtime_overrides_source": "none",
}


def _sample_value(
    value: str | int | float | bool,
    status: GatewayDiagnosticStatus,
) -> str | int | float | bool | None:
    return None if status in {"unavailable", "error", "unknown"} else value


def _sample_timestamp(status: GatewayDiagnosticStatus, timestamp: float) -> float:
    return 0.0 if status in {"unavailable", "error", "unknown"} else timestamp


def _sample_applicability(
    status: GatewayDiagnosticStatus,
) -> GatewayDiagnosticApplicability:
    if status == "unknown":
        return "unknown"
    if status == "inactive":
        return "not-applicable"
    return "applicable"


def _sample_reason(
    name: GatewayDiagnosticFieldName,
    status: GatewayDiagnosticStatus,
) -> str:
    return f"{name}-{status}" if status in {"inactive", "unavailable", "error"} else ""


def diagnostic_samples(
    *,
    status_overrides: Mapping[GatewayDiagnosticFieldName, GatewayDiagnosticStatus] | None = None,
    captured_at: float = 100.0,
) -> tuple[GatewayDiagnosticSample, ...]:
    overrides = status_overrides or {}
    samples: list[GatewayDiagnosticSample] = []
    for name, value in _VALUES.items():
        status = overrides.get(name, "fresh")
        samples.append(
            GatewayDiagnosticSample(
                name=name,
                value=_sample_value(value, status),
                status=status,
                changed_at=_sample_timestamp(status, max(0.0, captured_at - 2.0)),
                confirmed_at=_sample_timestamp(status, max(0.0, captured_at - 1.0)),
                confidence=_sample_timestamp(status, 1.0),
                applicability=_sample_applicability(status),
                reason_code=_sample_reason(name, status),
            )
        )
    return tuple(samples)


def gateway_diagnostics_snapshot(
    *,
    status_overrides: Mapping[GatewayDiagnosticFieldName, GatewayDiagnosticStatus] | None = None,
    health_state: GatewayHealthState = "ok",
    health_stale: bool = False,
    sequence: int = 7,
    captured_at: float = 100.0,
    discovery_state: GatewayDiscoveryState = "idle",
    discovery_pending_work: int = 0,
    discovered_source_count: int = 4,
    unusable_source_count: int = 1,
) -> GatewayDiagnosticsSnapshot:
    return GatewayDiagnosticsSnapshot(
        sequence=sequence,
        captured_at=captured_at,
        health=GatewayHealthSummary(
            state=health_state,
            stale=health_stale,
            timeouts_60s=0,
            average_latency_ms=4.0,
            maximum_latency_ms=8.0,
            pending_gateway_commands=1,
            pending_core_commands=2,
            maximum_event_loop_gap_ms_60s=20.0,
            last_success_at=max(0.0, captured_at - 0.5),
            last_error_code="",
        ),
        discovery=GatewayDiscoverySummary(
            enabled=True,
            state=discovery_state,
            pending_work=discovery_pending_work,
            discovered_source_count=discovered_source_count,
            unusable_source_count=unusable_source_count,
        ),
        publication=GatewayPublicationSummary(
            registered=True,
            heartbeat_at=max(0.0, captured_at - 0.5),
            stale=False,
        ),
        ev_charger=diagnostic_samples(
            status_overrides=status_overrides,
            captured_at=captured_at,
        ),
    )


def gateway_diagnostics_legacy_payload(
    *,
    observed_at: float | None = None,
) -> dict[str, object]:
    """Return the canonical schema-v1 representation used by migration tests."""
    snapshot = gateway_diagnostics_snapshot()
    legacy_samples = [
        {
            "name": sample.name,
            "value": sample.value,
            "status": sample.status,
            "observed_at": sample.confirmed_at if observed_at is None else observed_at,
            "confidence": sample.confidence,
            "reason_code": sample.reason_code,
        }
        for sample in snapshot.ev_charger
    ]
    discovery = snapshot.discovery.to_payload()
    legacy_discovery = {
        key: discovery[key]
        for key in (
            "enabled",
            "state",
            "pending_work",
            "discovered_source_count",
            "unusable_source_count",
        )
    }
    return {
        "schema_version": 1,
        "sequence": snapshot.sequence,
        "captured_at": snapshot.captured_at,
        "health": snapshot.health.to_payload(),
        "discovery": legacy_discovery,
        "ev_charger": legacy_samples,
    }


@dataclass(frozen=True, slots=True)
class StaticGatewayDiagnosticsReader:
    """Deterministic transport-neutral reader for consumer contract tests."""

    snapshot: GatewayDiagnosticsSnapshot

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
        return self.snapshot


def gateway_diagnostics_reader(
    *,
    captured_at: float = 100.0,
    discovery_state: GatewayDiscoveryState = "idle",
    discovery_pending_work: int = 0,
    discovered_source_count: int = 4,
    unusable_source_count: int = 1,
) -> StaticGatewayDiagnosticsReader:
    """Return a reader around one canonical semantic diagnostics snapshot."""
    return StaticGatewayDiagnosticsReader(
        gateway_diagnostics_snapshot(
            captured_at=captured_at,
            discovery_state=discovery_state,
            discovery_pending_work=discovery_pending_work,
            discovered_source_count=discovered_source_count,
            unusable_source_count=unusable_source_count,
        )
    )
