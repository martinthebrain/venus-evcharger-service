# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared semantic gateway-diagnostics fixtures for direct contract tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticFieldName,
    GatewayDiagnosticSample,
    GatewayDiagnosticStatus,
    GatewayDiagnosticsSnapshot,
    GatewayDiscoveryState,
    GatewayDiscoverySummary,
    GatewayHealthState,
    GatewayHealthSummary,
)

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


def diagnostic_samples(
    *,
    status_overrides: Mapping[GatewayDiagnosticFieldName, GatewayDiagnosticStatus] | None = None,
) -> tuple[GatewayDiagnosticSample, ...]:
    overrides = status_overrides or {}
    samples: list[GatewayDiagnosticSample] = []
    for name, value in _VALUES.items():
        status = overrides.get(name, "fresh")
        unavailable = status in {"unavailable", "error", "unknown"}
        samples.append(
            GatewayDiagnosticSample(
                name=name,
                value=None if unavailable else value,
                status=status,
                observed_at=0.0 if unavailable else 99.0,
                confidence=0.0 if unavailable else 1.0,
                reason_code=f"{name}-{status}" if status in {"unavailable", "error"} else "",
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
            last_success_at=99.5,
            last_error_code="",
        ),
        discovery=GatewayDiscoverySummary(
            enabled=True,
            state=discovery_state,
            pending_work=discovery_pending_work,
            discovered_source_count=discovered_source_count,
            unusable_source_count=unusable_source_count,
        ),
        ev_charger=diagnostic_samples(status_overrides=status_overrides),
    )


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
