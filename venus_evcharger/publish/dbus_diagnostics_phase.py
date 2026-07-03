# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-switching and contactor diagnostic values."""

from __future__ import annotations

from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_runtime import _DbusDiagnosticsRuntime


class _DbusDiagnosticsPhase(_DbusDiagnosticsRuntime):
    def _phase_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return outward phase diagnostics and supported-layout information."""
        return {
            "auto_phase_current": self._auto_phase_metric_text(self.service, "phase_current"),
            "auto_phase_observed": self._observed_phase_value(self.service),
            "auto_phase_target": self._auto_phase_metric_text(self.service, "phase_target"),
            "auto_phase_reason": self._auto_phase_metric_text(self.service, "phase_reason"),
            "auto_phase_mismatch_active": self._phase_switch_mismatch_active(self.service),
            "auto_phase_lockout_active": self._phase_switch_lockout_active(self.service, now),
            "auto_phase_lockout_target": self._phase_switch_lockout_target(self.service, now),
            "auto_phase_lockout_reason": self._phase_switch_lockout_reason(self.service, now),
            "auto_phase_supported_configured": self._phase_supported_configured(self.service),
            "auto_phase_supported_effective": self._phase_supported_effective(self.service, now),
            "auto_phase_degraded_active": self._phase_degraded_active(self.service, now),
            "auto_phase_threshold_watts": self._auto_phase_metric_float(self.service, "phase_threshold_watts"),
            "auto_phase_candidate": self._auto_phase_metric_text(self.service, "phase_candidate"),
        }

    def _contactor_counter_values(self) -> dict[str, DiagnosticValue]:
        """Return switch-feedback and contactor diagnostics."""
        return {
            "auto_switch_feedback_closed": self._switch_feedback_closed(self.service),
            "auto_switch_interlock_ok": self._switch_interlock_ok(self.service),
            "auto_switch_feedback_mismatch": self._switch_feedback_mismatch(self.service),
            "auto_contactor_suspected_open": self._contactor_suspected_open(self.service),
            "auto_contactor_suspected_welded": self._contactor_suspected_welded(self.service),
            "auto_contactor_fault_count": self._contactor_fault_count(self.service),
            "auto_contactor_lockout_active": self._contactor_lockout_active(self.service),
            "auto_contactor_lockout_reason": self._contactor_lockout_reason(self.service),
            "auto_contactor_lockout_source": self._contactor_lockout_source(self.service),
        }
