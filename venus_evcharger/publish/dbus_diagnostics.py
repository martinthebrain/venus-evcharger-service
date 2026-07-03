# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic-path publishing helpers for DBus publishing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.common import _charger_retry_remaining_seconds, _fresh_charger_transport_timestamp
from venus_evcharger.core.contracts import (
    displayable_confirmed_read_timestamp,
    normalized_auto_state_pair,
    normalized_fault_state,
    normalized_status_source,
)
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_introspection import _DbusDiagnosticsIntrospection


class _DbusPublishDiagnostics(_DbusDiagnosticsIntrospection):
    @staticmethod
    def _runtime_error_state(service: Any) -> Mapping[str, Any]:
        error_state = getattr(service, "_error_state", {})
        return error_state if isinstance(error_state, Mapping) else {}

    def _diagnostic_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return change-driven diagnostic counters keyed by semantic EVCS field."""
        error_state = self._runtime_error_state(self.service)
        scheduled_snapshot = self._scheduled_snapshot(self.service, now)
        auto_state, auto_state_code = normalized_auto_state_pair(
            getattr(self.service, "_last_auto_state", "idle"),
            getattr(self.service, "_last_auto_state_code", 0),
        )
        fault_reason, fault_active = normalized_fault_state(self._fault_reason(self.service))
        return {
            "status": int(self.service.last_status),
            "auto_health": str(self.service._last_health_reason),
            "auto_health_code": int(self.service._last_health_code),
            "auto_state": auto_state,
            "auto_state_code": auto_state_code,
            "auto_recovery_active": self._recovery_active(self.service),
            "auto_status_source": normalized_status_source(getattr(self.service, "_last_status_source", "unknown")),
            "auto_fault_active": fault_active,
            "auto_fault_reason": fault_reason,
            "auto_stale": 1 if self.service._is_update_stale(now) else 0,
            "auto_recovery_attempts": int(self.service._recovery_attempts),
            **self._auto_decision_counter_values(auto_state, auto_state_code),
            **self._scheduled_counter_values_from_snapshot(scheduled_snapshot),
            **self._backend_counter_values(),
            **self._software_update_counter_values(),
            **self._charger_counter_values(now),
            **self._error_counter_values(error_state),
            **self._shelly_counter_values(now),
            **self._phase_counter_values(now),
            **self._contactor_counter_values(),
            **self._runtime_timing_values(now),
            **self._dbus_introspection_counter_values(now),
        }

    def _diagnostic_age_values(self, now: float) -> dict[str, float]:
        """Return slower-changing age-like diagnostic values keyed by semantic EVCS field."""
        svc = self.service
        stale_base = (
            svc._last_successful_update_at
            if svc._last_successful_update_at is not None
            else svc.started_at
        )
        last_shelly_read_at = displayable_confirmed_read_timestamp(
            last_confirmed_at=getattr(svc, "_last_confirmed_pm_status_at", None),
            last_pm_at=getattr(svc, "_last_pm_status_at", None),
            last_pm_confirmed=bool(getattr(svc, "_last_pm_status_confirmed", False)),
            now=now,
        )
        return {
            "auto_last_shelly_read_age": self._age_seconds(last_shelly_read_at, now),
            "auto_shelly_last_ok_age": self._age_seconds(getattr(svc, "_shelly_last_ok_at", None), now),
            "auto_pending_relay_age": self._age_seconds(getattr(svc, "_pending_relay_requested_at", None), now),
            "auto_last_pv_read_age": self._age_seconds(svc._last_pv_at, now),
            "auto_last_battery_read_age": self._age_seconds(svc._last_battery_soc_at, now),
            "auto_last_grid_read_age": self._age_seconds(svc._last_grid_at, now),
            "auto_last_dbus_read_age": self._age_seconds(svc._last_dbus_ok_at, now),
            "auto_charger_current_target_age": self._age_seconds(
                getattr(svc, "_charger_target_current_applied_at", None), now
            ),
            "auto_phase_candidate_age": self._age_seconds(getattr(svc, "_auto_phase_target_since", None), now),
            "auto_phase_lockout_age": self._age_seconds(
                getattr(svc, "_phase_switch_lockout_at", None) if self._phase_switch_lockout_active(svc, now) else None,
                now,
            ),
            "auto_contactor_lockout_age": self._age_seconds(
                getattr(svc, "_contactor_lockout_at", None) if self._contactor_lockout_active(svc) else None,
                now,
            ),
            "auto_last_switch_feedback_age": self._age_seconds(getattr(svc, "_last_switch_feedback_at", None), now),
            "auto_last_charger_read_age": self._age_seconds(getattr(svc, "_last_charger_state_at", None), now),
            "auto_last_charger_estimate_age": self._age_seconds(
                getattr(svc, "_last_charger_estimate_at", None) if self._charger_estimate_active() else None,
                now,
            ),
            "auto_last_charger_transport_age": self._age_seconds(_fresh_charger_transport_timestamp(svc, now), now),
            "auto_charger_retry_remaining": float(_charger_retry_remaining_seconds(svc, now)),
            "auto_last_successful_update_age": self._age_seconds(svc._last_successful_update_at, now),
            "auto_software_update_last_check_age": self._age_seconds(
                getattr(svc, "_software_update_last_check_at", None),
                now,
            ),
            "auto_software_update_last_run_age": self._age_seconds(getattr(svc, "_software_update_last_run_at", None), now),
            "auto_stale_seconds": self._age_seconds(stale_base, now),
            "auto_dbus_introspection_snapshot_age": self._dbus_introspection_snapshot_age(now),
        }

    def publish_diagnostic_paths(self, now: float) -> bool:
        """Publish diagnostics on change, except age-like values every five seconds."""
        self.ensure_state()
        changed = self._publish_fields_transactional("diagnostic-counters", self._diagnostic_counter_values(now), now)
        changed |= self._publish_fields_transactional(
            "diagnostic-ages",
            self._diagnostic_age_values(now),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
        return changed
