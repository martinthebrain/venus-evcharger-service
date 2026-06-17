# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Diagnostic-path publishing helpers for DBus publishing."""

from __future__ import annotations

from typing import Any, Mapping, cast

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.core.common import _charger_retry_remaining_seconds, _fresh_charger_transport_timestamp
from venus_evcharger.core.contracts import (
    finite_float_or_none,
    displayable_confirmed_read_timestamp,
    normalized_auto_state_pair,
    normalized_fault_state,
    normalized_scheduled_state_fields,
    normalized_software_update_state_fields,
    normalized_status_source,
    sanitized_auto_metrics,
)
from venus_evcharger.dbus_introspection import load_owner_introspection_snapshot

class _DbusPublishDiagnosticsMixin:
    @classmethod
    def _scheduled_counter_values_from_snapshot(cls, scheduled_snapshot: Any) -> dict[str, str | int]:
        """Return normalized outward scheduled-state diagnostics."""
        if scheduled_snapshot is None:
            return cls._disabled_scheduled_counter_values()
        return cls._active_scheduled_counter_values(scheduled_snapshot)

    @staticmethod
    def _disabled_scheduled_counter_values() -> dict[str, str | int]:
        """Return outward scheduled-state diagnostics when no schedule is active."""
        scheduled_state, scheduled_state_code, scheduled_reason, scheduled_reason_code, scheduled_night_boost = (
            normalized_scheduled_state_fields(False, "disabled", 0, "disabled", 0, 0)
        )
        return {
            "/Auto/ScheduledState": scheduled_state,
            "/Auto/ScheduledStateCode": scheduled_state_code,
            "/Auto/ScheduledReason": scheduled_reason,
            "/Auto/ScheduledReasonCode": scheduled_reason_code,
            "/Auto/ScheduledNightBoostActive": scheduled_night_boost,
            "/Auto/ScheduledTargetDayEnabled": 0,
            "/Auto/ScheduledTargetDay": "",
            "/Auto/ScheduledTargetDate": "",
            "/Auto/ScheduledFallbackStart": "",
            "/Auto/ScheduledBoostUntil": "",
        }

    @staticmethod
    def _active_scheduled_counter_values(scheduled_snapshot: Any) -> dict[str, str | int]:
        """Return outward scheduled-state diagnostics for one active snapshot."""
        scheduled_state, scheduled_state_code, scheduled_reason, scheduled_reason_code, scheduled_night_boost = (
            normalized_scheduled_state_fields(
                True,
                scheduled_snapshot.state,
                scheduled_snapshot.state_code,
                scheduled_snapshot.reason,
                scheduled_snapshot.reason_code,
                int(bool(scheduled_snapshot.night_boost_active)),
            )
        )
        return {
            "/Auto/ScheduledState": scheduled_state,
            "/Auto/ScheduledStateCode": scheduled_state_code,
            "/Auto/ScheduledReason": scheduled_reason,
            "/Auto/ScheduledReasonCode": scheduled_reason_code,
            "/Auto/ScheduledNightBoostActive": scheduled_night_boost,
            "/Auto/ScheduledTargetDayEnabled": int(bool(scheduled_snapshot.target_day_enabled)),
            "/Auto/ScheduledTargetDay": scheduled_snapshot.target_day_label,
            "/Auto/ScheduledTargetDate": scheduled_snapshot.target_date_text,
            "/Auto/ScheduledFallbackStart": scheduled_snapshot.fallback_start_text,
            "/Auto/ScheduledBoostUntil": scheduled_snapshot.boost_until_text,
        }

    def _software_update_counter_values(self) -> dict[str, str | int]:
        """Return normalized outward software-update diagnostics."""
        state, state_code, available, no_update = normalized_software_update_state_fields(
            getattr(self.service, "_software_update_state", "idle"),
            getattr(self.service, "_software_update_available", False),
            getattr(self.service, "_software_update_no_update_active", False),
        )
        return {
            "/Auto/SoftwareUpdateAvailable": available,
            "/Auto/SoftwareUpdateState": state,
            "/Auto/SoftwareUpdateStateCode": state_code,
            "/Auto/SoftwareUpdateDetail": str(getattr(self.service, "_software_update_detail", "") or ""),
            "/Auto/SoftwareUpdateCurrentVersion": str(getattr(self.service, "_software_update_current_version", "") or ""),
            "/Auto/SoftwareUpdateAvailableVersion": str(
                getattr(self.service, "_software_update_available_version", "") or ""
            ),
            "/Auto/SoftwareUpdateNoUpdateActive": no_update,
        }

    def _backend_counter_values(self) -> dict[str, str | int]:
        """Return backend-composition and runtime-override diagnostics."""
        return {
            "/Auto/BackendMode": backend_mode_for_service(self.service, self._backend_mode_value(self.service)),
            "/Auto/MeterBackend": backend_type_for_service(
                self.service,
                "meter",
                self._backend_type_value(self.service, "meter_backend_type", "shelly_meter"),
            ),
            "/Auto/SwitchBackend": backend_type_for_service(
                self.service,
                "switch",
                self._backend_type_value(self.service, "switch_backend_type", "shelly_contactor_switch"),
            ),
            "/Auto/ChargerBackend": backend_type_for_service(
                self.service,
                "charger",
                self._backend_type_value(self.service, "charger_backend_type"),
            ),
            "/Auto/RuntimeOverridesActive": int(bool(getattr(self.service, "_runtime_overrides_active", False))),
            "/Auto/RuntimeOverridesPath": str(getattr(self.service, "runtime_overrides_path", "") or ""),
        }

    def _charger_counter_values(self, now: float) -> dict[str, str | int | float]:
        """Return charger, transport, and retry diagnostics."""
        return {
            "/Auto/ChargerStatus": self._charger_text_observed("_last_charger_state_status"),
            "/Auto/ChargerFault": self._charger_text_observed("_last_charger_state_fault"),
            "/Auto/ChargerFaultActive": int(bool(getattr(self.service, "_last_charger_fault_active", 0))),
            "/Auto/ChargerEstimateActive": self._charger_estimate_active(),
            "/Auto/ChargerEstimateSource": self._charger_estimate_source(),
            "/Auto/ChargerTransportActive": self._charger_transport_active(now),
            "/Auto/ChargerTransportReason": self._charger_transport_reason(now),
            "/Auto/ChargerTransportSource": self._charger_transport_source(now),
            "/Auto/ChargerTransportDetail": self._charger_transport_detail(now),
            "/Auto/ChargerRetryActive": self._charger_retry_active(now),
            "/Auto/ChargerRetryReason": self._charger_retry_reason(now),
            "/Auto/ChargerRetrySource": self._charger_retry_source(now),
            "/Auto/ChargerCurrentTarget": self._charger_current_target_value(self.service),
        }

    def _error_counter_values(self, error_state: Mapping[str, Any]) -> dict[str, int]:
        """Return aggregate error counters sourced from the runtime error state."""
        error_count = int(
            error_state.get("dbus", 0)
            + error_state.get("shelly", 0)
            + error_state.get("charger", 0)
            + error_state.get("pv", 0)
            + error_state.get("battery", 0)
            + error_state.get("grid", 0)
        )
        return {
            "/Auto/ErrorCount": error_count,
            "/Auto/DbusReadErrors": int(error_state.get("dbus", 0)),
            "/Auto/ShellyReadErrors": int(error_state.get("shelly", 0)),
            "/Auto/ChargerWriteErrors": int(error_state.get("charger", 0)),
            "/Auto/PvReadErrors": int(error_state.get("pv", 0)),
            "/Auto/BatteryReadErrors": int(error_state.get("battery", 0)),
            "/Auto/GridReadErrors": int(error_state.get("grid", 0)),
            "/Auto/InputCacheHits": int(error_state.get("cache_hits", 0)),
        }

    def _shelly_counter_values(self, now: float) -> dict[str, str | int]:
        """Return Shelly transport and retry diagnostics."""
        svc = self.service
        return {
            "/Auto/ShellyState": str(getattr(svc, "_shelly_state", "unknown") or "unknown"),
            "/Auto/ShellyLastError": str(getattr(svc, "_shelly_last_error_reason", "") or ""),
            "/Auto/ShellyRetryRemaining": self._shelly_retry_remaining_value(svc, now),
            "/Auto/ShellyConsecutiveErrors": int(getattr(svc, "_shelly_consecutive_errors", 0) or 0),
        }

    @staticmethod
    def _shelly_retry_remaining_value(svc: Any, now: float) -> int:
        """Return remaining Shelly retry delay in seconds."""
        source_retry_remaining = getattr(svc, "_source_retry_remaining", None)
        if callable(source_retry_remaining):
            return int(source_retry_remaining("shelly", now))
        retry_after = getattr(svc, "_shelly_retry_after", 0.0)
        if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool):
            return 0
        return max(0, int(float(retry_after) - float(now)))

    def _phase_counter_values(self, now: float) -> dict[str, str | int | float]:
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

    def _contactor_counter_values(self) -> dict[str, str | int]:
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

    def _runtime_timing_values(self, now: float) -> dict[str, int | float]:
        """Return timing and queue diagnostics for async runtime health."""
        svc = self.service
        mainloop_heartbeat_at = getattr(svc, "_mainloop_heartbeat_at", None)
        heartbeat_age = (
            max(0.0, now - float(mainloop_heartbeat_at))
            if isinstance(mainloop_heartbeat_at, (int, float))
            else -1.0
        )
        return {
            "/Auto/UpdateWorkerDurationSeconds": float(getattr(svc, "_last_update_cycle_duration_seconds", 0.0)),
            "/Auto/UpdateWorkerPending": int(bool(getattr(svc, "_update_worker_pending", False))),
            "/Auto/UpdateWorkerSkipped": int(getattr(svc, "_update_worker_skipped_count", 0)),
            "/Auto/PublishFlushDurationSeconds": float(getattr(svc, "_last_publish_flush_duration_seconds", 0.0)),
            "/Auto/PublishQueueLagSeconds": float(getattr(svc, "_last_dbus_publish_queue_lag_seconds", 0.0)),
            "/Auto/PublishQueueDropped": int(getattr(svc, "_dbus_publish_dropped_count", 0)),
            "/Auto/WriteCommandDurationSeconds": float(getattr(svc, "_last_write_command_duration_seconds", 0.0)),
            "/Auto/WriteCommandQueueLagSeconds": float(getattr(svc, "_last_write_command_queue_lag_seconds", 0.0)),
            "/Auto/MainloopHeartbeatAge": heartbeat_age,
        }

    @staticmethod
    def _auto_decision_metric_float(metrics: Mapping[str, Any], field_name: str) -> float:
        value = finite_float_or_none(metrics.get(field_name))
        return -1.0 if value is None else float(value)

    @staticmethod
    def _auto_decision_metric_text(metrics: Mapping[str, Any], field_name: str) -> str:
        value = metrics.get(field_name)
        return "" if value is None else str(value).strip()

    @staticmethod
    def _auto_decision_relay_intent(metrics: Mapping[str, Any]) -> int:
        value = metrics.get("relay_intent")
        if value is None:
            return -1
        return int(bool(value))

    def _auto_decision_counter_values(
        self,
        auto_state: str,
        auto_state_code: int,
    ) -> dict[str, str | int | float]:
        """Return the compact 'why did it start/stop?' diagnostic surface."""
        metrics = sanitized_auto_metrics(self._auto_metrics(self.service))
        return {
            "/Auto/DecisionReason": str(self.service._last_health_reason),
            "/Auto/DecisionState": auto_state,
            "/Auto/DecisionStateCode": auto_state_code,
            "/Auto/DecisionRelayIntent": self._auto_decision_relay_intent(metrics),
            "/Auto/DecisionSurplusWatts": self._auto_decision_metric_float(metrics, "surplus"),
            "/Auto/DecisionGridWatts": self._auto_decision_metric_float(metrics, "grid"),
            "/Auto/DecisionSocPercent": self._auto_decision_metric_float(metrics, "soc"),
            "/Auto/DecisionStartThresholdWatts": self._auto_decision_metric_float(metrics, "start_threshold"),
            "/Auto/DecisionStopThresholdWatts": self._auto_decision_metric_float(metrics, "stop_threshold"),
            "/Auto/DecisionProfile": self._auto_decision_metric_text(metrics, "profile"),
            "/Auto/DecisionThresholdMode": self._auto_decision_metric_text(metrics, "threshold_mode"),
        }

    def _diagnostic_counter_values(self, now: float) -> dict[str, str | int | float]:
        """Return change-driven diagnostic counters keyed by DBus path.

        This method is the compact outward status snapshot for operators. It
        pulls together health, scheduled state, backend composition, retry,
        transport, phase, feedback, and contactor diagnostics in one place.

        The normalization helpers used here are important. They ensure the
        published DBus surface keeps one consistent story even when internal
        sources were produced by different layers and at slightly different
        times.
        """
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

    def _dbus_introspection_counter_values(self, now: float) -> dict[str, str | int | float]:
        """Return compact diagnostics for the advisory DBus introspection map."""
        snapshot = load_owner_introspection_snapshot(self.service, now=now)
        services = snapshot.get("services", {}) if isinstance(snapshot, dict) else {}
        return {
            "/Auto/DbusIntrospectionState": self._dbus_introspection_state(snapshot),
            "/Auto/DbusIntrospectionQueueDepth": self._dbus_introspection_queue_depth(snapshot),
            "/Auto/DbusIntrospectionServiceCount": self._dbus_introspection_service_count(services),
            "/Auto/DbusIntrospectionUnusablePathCount": self._dbus_introspection_unusable_count(services),
        }

    def _dbus_introspection_state(self, snapshot: object) -> str:
        """Return the introspection worker state for diagnostics."""
        return str(snapshot.get("worker_state", "missing")) if isinstance(snapshot, dict) else "missing"

    def _dbus_introspection_queue_depth(self, snapshot: object) -> int:
        """Return the pending introspection request count."""
        return int(snapshot.get("queue_depth", 0) or 0) if isinstance(snapshot, dict) else 0

    def _dbus_introspection_service_count(self, services: object) -> int:
        """Return the number of services in an introspection snapshot."""
        return len(services) if isinstance(services, dict) else 0

    def _dbus_introspection_unusable_count(self, services: object) -> int:
        """Count introspection findings that say a path should not be queried now."""
        if not isinstance(services, dict):
            return 0
        return sum(self._dbus_introspection_unusable_paths(service_payload) for service_payload in services.values())

    def _dbus_introspection_unusable_paths(self, service_payload: object) -> int:
        """Count unusable path findings in one introspection service payload."""
        paths = service_payload.get("paths", {}) if isinstance(service_payload, dict) else {}
        if not isinstance(paths, dict):
            return 0
        return sum(1 for finding in paths.values() if self._dbus_introspection_finding_unusable(finding))

    def _dbus_introspection_finding_unusable(self, finding: object) -> bool:
        """Return whether one introspection path finding is currently unusable."""
        if not isinstance(finding, dict):
            return False
        return str(finding.get("status", "") or "") in ("known-missing", "unresponsive-backoff")

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
            "/Auto/PhaseCandidateAge": self._age_seconds(
                getattr(svc, "_auto_phase_target_since", None), now
            ),
            "/Auto/PhaseLockoutAge": self._age_seconds(
                getattr(svc, "_phase_switch_lockout_at", None) if self._phase_switch_lockout_active(svc, now) else None,
                now,
            ),
            "/Auto/ContactorLockoutAge": self._age_seconds(
                getattr(svc, "_contactor_lockout_at", None) if self._contactor_lockout_active(svc) else None,
                now,
            ),
            "/Auto/LastSwitchFeedbackAge": self._age_seconds(
                getattr(svc, "_last_switch_feedback_at", None), now
            ),
            "/Auto/LastChargerReadAge": self._age_seconds(
                getattr(svc, "_last_charger_state_at", None), now
            ),
            "/Auto/LastChargerEstimateAge": self._age_seconds(
                getattr(svc, "_last_charger_estimate_at", None)
                if self._charger_estimate_active()
                else None,
                now,
            ),
            "/Auto/LastChargerTransportAge": self._age_seconds(
                _fresh_charger_transport_timestamp(svc, now), now
            ),
            "/Auto/ChargerRetryRemaining": float(_charger_retry_remaining_seconds(svc, now)),
            "/Auto/LastSuccessfulUpdateAge": self._age_seconds(svc._last_successful_update_at, now),
            "/Auto/SoftwareUpdateLastCheckAge": self._age_seconds(
                getattr(svc, "_software_update_last_check_at", None),
                now,
            ),
            "/Auto/SoftwareUpdateLastRunAge": self._age_seconds(
                getattr(svc, "_software_update_last_run_at", None),
                now,
            ),
            "/Auto/StaleSeconds": self._age_seconds(stale_base, now),
            "/Auto/DbusIntrospectionSnapshotAge": self._age_seconds(
                load_owner_introspection_snapshot(svc, now=now).get("heartbeat_at"),
                now,
            ),
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
