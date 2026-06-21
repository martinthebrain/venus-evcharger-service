# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-switching and contactor diagnostic values."""

from __future__ import annotations

from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue, _DbusDiagnosticsContractsMixin


class _DbusDiagnosticsPhaseMixin(_DbusDiagnosticsContractsMixin):
    def _phase_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return outward phase diagnostics and supported-layout information."""
        return {
            "/Auto/PhaseCurrent": self._auto_phase_metric_text(self.service, "phase_current"),
            "/Auto/PhaseObserved": self._observed_phase_value(self.service),
            "/Auto/PhaseTarget": self._auto_phase_metric_text(self.service, "phase_target"),
            "/Auto/PhaseReason": self._auto_phase_metric_text(self.service, "phase_reason"),
            "/Auto/PhaseMismatchActive": self._phase_switch_mismatch_active(self.service),
            "/Auto/PhaseLockoutActive": self._phase_switch_lockout_active(self.service, now),
            "/Auto/PhaseLockoutTarget": self._phase_switch_lockout_target(self.service, now),
            "/Auto/PhaseLockoutReason": self._phase_switch_lockout_reason(self.service, now),
            "/Auto/PhaseSupportedConfigured": self._phase_supported_configured(self.service),
            "/Auto/PhaseSupportedEffective": self._phase_supported_effective(self.service, now),
            "/Auto/PhaseDegradedActive": self._phase_degraded_active(self.service, now),
            "/Auto/PhaseThresholdWatts": self._auto_phase_metric_float(self.service, "phase_threshold_watts"),
            "/Auto/PhaseCandidate": self._auto_phase_metric_text(self.service, "phase_candidate"),
        }

    def _contactor_counter_values(self) -> dict[str, DiagnosticValue]:
        """Return switch-feedback and contactor diagnostics."""
        return {
            "/Auto/SwitchFeedbackClosed": self._switch_feedback_closed(self.service),
            "/Auto/SwitchInterlockOk": self._switch_interlock_ok(self.service),
            "/Auto/SwitchFeedbackMismatch": self._switch_feedback_mismatch(self.service),
            "/Auto/ContactorSuspectedOpen": self._contactor_suspected_open(self.service),
            "/Auto/ContactorSuspectedWelded": self._contactor_suspected_welded(self.service),
            "/Auto/ContactorFaultCount": self._contactor_fault_count(self.service),
            "/Auto/ContactorLockoutActive": self._contactor_lockout_active(self.service),
            "/Auto/ContactorLockoutReason": self._contactor_lockout_reason(self.service),
            "/Auto/ContactorLockoutSource": self._contactor_lockout_source(self.service),
        }
