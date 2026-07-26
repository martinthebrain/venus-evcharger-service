# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed DBus publishing boundary for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.bootstrap.publication import EvcsPublicationOwner
from venus_evcharger.publish.dbus_config import DbusPublishConfig
from venus_evcharger.publish.dbus_core import DbusPublishCore
from venus_evcharger.publish.dbus_diagnostics import DbusPublishDiagnostics
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticSnapshot
from venus_evcharger.publish.dbus_learned import DbusPublishLearned
from venus_evcharger.publish.dbus_measurements import DbusMeasurementPublisher
from venus_evcharger.publish.dbus_ports import DiagnosticsRuntimeViews
from venus_evcharger.publish.dbus_runtime_view import DbusRuntimeView
from venus_evcharger.publish.dbus_shared import (
    AgeSeconds,
    DbusPublishContext,
    PhaseData,
    PublishServicePort,
    PublishValue,
)
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsReader


class DbusPublishController:
    """Expose the cohesive publish use cases while delegating each responsibility."""

    def __init__(
        self,
        service: PublishServicePort,
        age_seconds_func: AgeSeconds,
        gateway_diagnostics: GatewayDiagnosticsReader,
        publication_owner: EvcsPublicationOwner,
    ) -> None:
        context = DbusPublishContext(
            service=service,
            age_seconds=age_seconds_func,
            gateway_diagnostics=gateway_diagnostics,
        )
        self.core = DbusPublishCore(context)
        self._gateway_diagnostics = gateway_diagnostics
        self._publication_owner = publication_owner
        self.learned = DbusPublishLearned(context)
        self.runtime_view = DbusRuntimeView()
        self.config = DbusPublishConfig(context, self.core, self.learned, self.runtime_view)
        self.measurements = DbusMeasurementPublisher(context, self.core)
        diagnostic_runtime_views = DiagnosticsRuntimeViews(
            sources=self.runtime_view,
            decisions=self.runtime_view,
            phases=self.runtime_view,
            summary=self.runtime_view,
        )
        self.diagnostics = DbusPublishDiagnostics(
            context,
            self.core,
            self.learned,
            diagnostic_runtime_views,
        )

    def ensure_state(self) -> None:
        self.core.ensure_state()

    def maintain_evcs_registration(self, now: float) -> bool:
        """Recover gateway-owned EVCS registration from semantic health."""
        return self._publication_owner.maintain_registration(
            self._gateway_diagnostics,
            now,
        )

    def publish_field(
        self,
        field: str,
        value: PublishValue,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        return self.core.publish_field(field, value, now, interval_seconds, force)

    def last_accepted_field(self, field: str) -> object:
        return self.core.last_accepted_field(field)

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
        now: float | None,
    ) -> bool:
        return self.measurements.publish_live_measurements(power, voltage, total_current, phase_data, now)

    def publish_energy_time_measurements(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        return self.measurements.publish_energy_time_measurements(
            energy_forward,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )

    def publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        return self.config.publish_config_paths(startstop_display, now)

    def diagnostic_snapshot(self, now: float) -> DiagnosticSnapshot:
        return self.diagnostics.snapshot(now)

    def publish_diagnostic_paths(self, now: float) -> bool:
        return self.diagnostics.publish_diagnostic_paths(now)
