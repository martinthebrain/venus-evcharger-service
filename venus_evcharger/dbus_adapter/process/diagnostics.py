#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the public semantic diagnostics document inside the adapter."""

from __future__ import annotations

import time
from collections.abc import Mapping

from venus_evcharger.dbus_adapter.process.diagnostics_summary import (
    diagnostic_freshness_deadline,
    discovery_summary,
    health_summary,
    publication_summary,
)
from venus_evcharger.dbus_adapter.process.diagnostics_values import evcs_samples
from venus_evcharger.dbus_adapter.process.protocols.io import DbusAdapterDiagnosticsContext
from venus_evcharger.ipc.energy import EnergyTopologySnapshot
from venus_evcharger.ipc.gateway_diagnostics import (
    gateway_diagnostics_path,
    gateway_diagnostics_payload,
)
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsSnapshot


class DbusAdapterDiagnostics:
    """Translate adapter-private state into the public diagnostics contract."""

    def __init__(self, context: DbusAdapterDiagnosticsContext) -> None:
        self._context = context

    def write_gateway_diagnostics(
        self,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
        captured_monotonic: float | None = None,
    ) -> None:
        snapshot = self.gateway_diagnostics_snapshot(
            health=health,
            topology=topology,
            captured_at=captured_at,
            captured_monotonic=captured_monotonic,
        )
        path = gateway_diagnostics_path(self._context.paths.run_dir)
        self._context.json_writer.write(path, gateway_diagnostics_payload(snapshot))

    def gateway_diagnostics_snapshot(
        self,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
        captured_monotonic: float | None = None,
    ) -> GatewayDiagnosticsSnapshot:
        context = self._context
        monotonic_at = (
            time.monotonic()
            if captured_monotonic is None
            else max(0.0, captured_monotonic)
        )
        freshness_deadline = diagnostic_freshness_deadline(
            health,
            context.slo_gui_max_age_seconds,
        )
        return GatewayDiagnosticsSnapshot(
            sequence=context.cache.sequence,
            captured_at=captured_at,
            captured_monotonic=monotonic_at,
            health=health_summary(health, max_tick_seconds=context.max_tick_seconds),
            discovery=discovery_summary(
                health,
                topology,
                pending_work=context._introspection_queue_depth,
            ),
            publication=publication_summary(
                context.publication_registry,
                captured_at=captured_at,
                captured_monotonic=monotonic_at,
                stale_after_seconds=freshness_deadline,
            ),
            ev_charger=evcs_samples(
                context.publication_registry,
                captured_at=captured_at,
                captured_monotonic=monotonic_at,
                stale_after_seconds=freshness_deadline,
            ),
        )


__all__ = ["DbusAdapterDiagnostics"]
