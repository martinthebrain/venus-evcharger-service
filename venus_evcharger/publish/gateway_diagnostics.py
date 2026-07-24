# SPDX-License-Identifier: GPL-3.0-or-later
"""Project transport-neutral gateway diagnostics onto EVCS diagnostic fields."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.ports.gateway_diagnostic_discovery import GatewayDiscoveryState
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsReader,
    GatewayDiagnosticsUnavailable,
)
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue


@dataclass(frozen=True, slots=True)
class GatewayDiscoveryDiagnosticValues:
    """One coherent semantic discovery view for an EVCS publish cycle."""

    state: GatewayDiscoveryState
    pending_work: int
    discovered_source_count: int
    unusable_source_count: int
    age_seconds: float

    def counter_values(self) -> dict[str, DiagnosticValue]:
        return {
            "auto_gateway_discovery_state": self.state,
            "auto_gateway_discovery_pending_work": self.pending_work,
            "auto_gateway_discovered_source_count": self.discovered_source_count,
            "auto_gateway_unusable_source_count": self.unusable_source_count,
        }


class GatewayDiscoveryDiagnostics:
    """Read and project gateway-owned discovery health without DBus knowledge."""

    def __init__(self, reader: GatewayDiagnosticsReader) -> None:
        self._reader = reader

    def values(self, now: float) -> GatewayDiscoveryDiagnosticValues:
        try:
            snapshot = self._reader.read_snapshot()
        except GatewayDiagnosticsUnavailable:
            return GatewayDiscoveryDiagnosticValues("unavailable", 0, 0, 0, -1.0)
        discovery = snapshot.discovery
        return GatewayDiscoveryDiagnosticValues(
            state=discovery.state,
            pending_work=discovery.pending_work,
            discovered_source_count=discovery.discovered_source_count,
            unusable_source_count=discovery.unusable_source_count,
            age_seconds=snapshot.age_seconds(now),
        )


__all__ = ["GatewayDiscoveryDiagnosticValues", "GatewayDiscoveryDiagnostics"]
