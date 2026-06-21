# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic-path publishing helpers for DBus publishing."""

from __future__ import annotations

from typing import Any, cast

from venus_evcharger.core.common import _charger_retry_remaining_seconds, _fresh_charger_transport_timestamp
from venus_evcharger.core.contracts import (
    displayable_confirmed_read_timestamp,
    normalized_auto_state_pair,
    normalized_fault_state,
    normalized_status_source,
)
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_introspection import _DbusDiagnosticsIntrospectionMixin
from venus_evcharger.publish.dbus_diagnostics_phase import _DbusDiagnosticsPhaseMixin
from venus_evcharger.publish.dbus_diagnostics_runtime import _DbusDiagnosticsRuntimeMixin
from venus_evcharger.publish.dbus_diagnostics_schedule import _DbusDiagnosticsScheduleMixin
from venus_evcharger.publish.dbus_diagnostics_sources import _DbusDiagnosticsSourcesMixin


class _DbusPublishDiagnosticsMixin(
    _DbusDiagnosticsIntrospectionMixin,
    _DbusDiagnosticsPhaseMixin,
    _DbusDiagnosticsRuntimeMixin,
    _DbusDiagnosticsScheduleMixin,
    _DbusDiagnosticsSourcesMixin,
):
    def _diagnostic_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return change-driven diagnostic counters keyed by DBus path."""
        error_state = cast(dict[str, Any], self.service._error_state)
        scheduled_snapshot = self._scheduled_snapshot(self.service, now)
        auto_state, auto_state_code = normalized_auto_state_pair(
            getattr(self.service, "_last_auto_state", "idle"),
            getattr(self.service, "_last_auto_state_code", 0),
        )
        fault_reason, fault_active = normalized_fault_state(self._fault_reason(self.service))
        return {
            "/Status": int(self.service.last_status),
            "/Auto/Health": str(self.service._last_health_reason),
            "/Auto/HealthCode": int(self.service._last_health_code),
            "/Auto/State": auto_state,
            "/Auto/StateCode": auto_state_code,
            "/Auto/RecoveryActive": self._recovery_active(self.service),
            "/Auto/StatusSource": normalized_status_source(getattr(self.service, "_last_status_source", "unknown")),
            "/Auto/FaultActive": fault_active,
            "/Auto/FaultReason": fault_reason,
            "/Auto/Stale": 1 if self.service._is_update_stale(now) else 0,
            "/Auto/RecoveryAttempts": int(self.service._recovery_attempts),
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
        """Return slower-changing age-like diagnostic values keyed by DBus path."""
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
            "/Auto/LastShellyReadAge": self._age_seconds(last_shelly_read_at, now),
            "/Auto/ShellyLastOkAge": self._age_seconds(getattr(svc, "_shelly_last_ok_at", None), now),
            "/Auto/PendingRelayAge": self._age_seconds(getattr(svc, "_pending_relay_requested_at", None), now),
            "/Auto/LastPvReadAge": self._age_seconds(svc._last_pv_at, now),
            "/Auto/LastBatteryReadAge": self._age_seconds(svc._last_battery_soc_at, now),
            "/Auto/LastGridReadAge": self._age_seconds(svc._last_grid_at, now),
            "/Auto/LastDbusReadAge": self._age_seconds(svc._last_dbus_ok_at, now),
            "/Auto/ChargerCurrentTargetAge": self._age_seconds(
                getattr(svc, "_charger_target_current_applied_at", None), now
            ),
            "/Auto/PhaseCandidateAge": self._age_seconds(getattr(svc, "_auto_phase_target_since", None), now),
            "/Auto/PhaseLockoutAge": self._age_seconds(
                getattr(svc, "_phase_switch_lockout_at", None) if self._phase_switch_lockout_active(svc, now) else None,
                now,
            ),
            "/Auto/ContactorLockoutAge": self._age_seconds(
                getattr(svc, "_contactor_lockout_at", None) if self._contactor_lockout_active(svc) else None,
                now,
            ),
            "/Auto/LastSwitchFeedbackAge": self._age_seconds(getattr(svc, "_last_switch_feedback_at", None), now),
            "/Auto/LastChargerReadAge": self._age_seconds(getattr(svc, "_last_charger_state_at", None), now),
            "/Auto/LastChargerEstimateAge": self._age_seconds(
                getattr(svc, "_last_charger_estimate_at", None) if self._charger_estimate_active() else None,
                now,
            ),
            "/Auto/LastChargerTransportAge": self._age_seconds(_fresh_charger_transport_timestamp(svc, now), now),
            "/Auto/ChargerRetryRemaining": float(_charger_retry_remaining_seconds(svc, now)),
            "/Auto/LastSuccessfulUpdateAge": self._age_seconds(svc._last_successful_update_at, now),
            "/Auto/SoftwareUpdateLastCheckAge": self._age_seconds(
                getattr(svc, "_software_update_last_check_at", None),
                now,
            ),
            "/Auto/SoftwareUpdateLastRunAge": self._age_seconds(getattr(svc, "_software_update_last_run_at", None), now),
            "/Auto/StaleSeconds": self._age_seconds(stale_base, now),
            "/Auto/DbusIntrospectionSnapshotAge": self._dbus_introspection_snapshot_age(now),
        }

    def publish_diagnostic_paths(self, now: float) -> bool:
        """Publish diagnostics on change, except age-like values every five seconds."""
        self.ensure_state()
        changed = self._publish_values_transactional("diagnostic-counters", self._diagnostic_counter_values(now), now)
        changed |= self._publish_values_transactional(
            "diagnostic-ages",
            self._diagnostic_age_values(now),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
        return changed
