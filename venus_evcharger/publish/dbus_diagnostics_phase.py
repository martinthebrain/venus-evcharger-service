# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-switching and contactor diagnostic values."""

from __future__ import annotations

from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_ports import PhaseRuntimeViewPort
from venus_evcharger.publish.dbus_shared import DbusPublishContext, PublishServicePort


class DbusDiagnosticsPhase:
    """Build phase-switching and contactor diagnostics."""

    def __init__(self, context: DbusPublishContext, runtime_view: PhaseRuntimeViewPort) -> None:
        self.service: PublishServicePort = context.service
        self.runtime_view = runtime_view

    def phase_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return outward phase diagnostics and supported-layout information."""
        return {
            "auto_phase_current": self.runtime_view.auto_phase_metric_text(self.service, "phase_current"),
            "auto_phase_observed": self.runtime_view.observed_phase_value(self.service),
            "auto_phase_target": self.runtime_view.auto_phase_metric_text(self.service, "phase_target"),
            "auto_phase_reason": self.runtime_view.auto_phase_metric_text(self.service, "phase_reason"),
            "auto_phase_mismatch_active": self.runtime_view.phase_switch_mismatch_active(self.service),
            "auto_phase_lockout_active": self.runtime_view.phase_switch_lockout_active(self.service, now),
            "auto_phase_lockout_target": self.runtime_view.phase_switch_lockout_target(self.service, now),
            "auto_phase_lockout_reason": self.runtime_view.phase_switch_lockout_reason(self.service, now),
            "auto_phase_supported_configured": self.runtime_view.phase_supported_configured(self.service),
            "auto_phase_supported_effective": self.runtime_view.phase_supported_effective(self.service, now),
            "auto_phase_degraded_active": self.runtime_view.phase_degraded_active(self.service, now),
            "auto_phase_threshold_watts": self.runtime_view.auto_phase_metric_float(
                self.service, "phase_threshold_watts"
            ),
            "auto_phase_candidate": self.runtime_view.auto_phase_metric_text(self.service, "phase_candidate"),
        }

    def contactor_values(self) -> dict[str, DiagnosticValue]:
        """Return switch-feedback and contactor diagnostics."""
        return {
            "auto_switch_feedback_closed": self.runtime_view.switch_feedback_closed(self.service),
            "auto_switch_interlock_ok": self.runtime_view.switch_interlock_ok(self.service),
            "auto_switch_feedback_mismatch": self.runtime_view.switch_feedback_mismatch(self.service),
            "auto_contactor_suspected_open": self.runtime_view.contactor_suspected_open(self.service),
            "auto_contactor_suspected_welded": self.runtime_view.contactor_suspected_welded(self.service),
            "auto_contactor_fault_count": self.runtime_view.contactor_fault_count(self.service),
            "auto_contactor_lockout_active": self.runtime_view.contactor_lockout_active(self.service),
            "auto_contactor_lockout_reason": self.runtime_view.contactor_lockout_reason(self.service),
            "auto_contactor_lockout_source": self.runtime_view.contactor_lockout_source(self.service),
        }
