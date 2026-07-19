# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic-path publishing helpers for DBus publishing."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.core.common import _charger_retry_remaining_seconds, _fresh_charger_transport_timestamp
from venus_evcharger.core.contracts import (
    displayable_confirmed_read_timestamp,
    normalized_auto_state_pair,
    normalized_fault_state,
    normalized_status_source,
)
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticSnapshot, DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_introspection import DbusDiagnosticsIntrospection
from venus_evcharger.publish.dbus_diagnostics_phase import DbusDiagnosticsPhase
from venus_evcharger.publish.dbus_diagnostics_runtime import DbusDiagnosticsRuntime
from venus_evcharger.publish.dbus_diagnostics_schedule import DbusDiagnosticsSchedule
from venus_evcharger.publish.dbus_diagnostics_sources import DbusDiagnosticsSources
from venus_evcharger.publish.dbus_ports import (
    DiagnosticsLearnedPort,
    DiagnosticsRuntimeViews,
    FieldPublisherPort,
)
from venus_evcharger.publish.dbus_shared import DbusPublishContext, PublishServicePort, is_object_mapping


class DbusPublishDiagnostics:
    """Compose, expose, and publish the complete diagnostic snapshot."""

    def __init__(
        self,
        context: DbusPublishContext,
        core: FieldPublisherPort,
        learned: DiagnosticsLearnedPort,
        runtime_views: DiagnosticsRuntimeViews,
    ) -> None:
        self.context = context
        self.service: PublishServicePort = context.service
        self.core = core
        self.learned = learned
        self.runtime_views = runtime_views
        self.sources = DbusDiagnosticsSources(context, runtime_views.sources, learned)
        self.schedule = DbusDiagnosticsSchedule(context)
        self.runtime = DbusDiagnosticsRuntime(context, runtime_views.decisions)
        self.phase = DbusDiagnosticsPhase(context, runtime_views.phases)
        self.introspection = DbusDiagnosticsIntrospection(context)

    @staticmethod
    def _runtime_error_state(service: object) -> Mapping[str, object]:
        error_state = getattr(service, "_error_state", None)
        if not is_object_mapping(error_state):
            return {}
        return {str(key): value for key, value in error_state.items()}

    def counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return change-driven diagnostic counters keyed by semantic EVCS field."""
        error_state = self._runtime_error_state(self.service)
        scheduled_snapshot = self.runtime_views.summary.scheduled_snapshot(self.service, now)
        auto_state, auto_state_code = normalized_auto_state_pair(
            getattr(self.service, "_last_auto_state", None),
            None,
        )
        fault_reason, fault_active = normalized_fault_state(self.runtime_views.summary.fault_reason(self.service))
        return {
            "status": int(self.service.last_status),
            "auto_health": str(self.service._last_health_reason),
            "auto_health_code": int(self.service._last_health_code),
            "auto_state": auto_state,
            "auto_state_code": auto_state_code,
            "auto_recovery_active": self.runtime_views.summary.recovery_active(self.service),
            "auto_status_source": normalized_status_source(getattr(self.service, "_last_status_source", None)),
            "auto_fault_active": fault_active,
            "auto_fault_reason": fault_reason,
            "auto_stale": 1 if self.service.runtime.update_is_stale(now) else 0,
            "auto_recovery_attempts": int(self.service._recovery_attempts),
            **self.runtime.auto_decision_values(auto_state, auto_state_code),
            **self.schedule.scheduled_values(scheduled_snapshot),
            **self.sources.backend_values(),
            **self.schedule.software_update_values(),
            **self.sources.charger_values(now),
            **self.sources.error_values(error_state),
            **self.sources.shelly_values(now),
            **self.phase.phase_values(now),
            **self.phase.contactor_values(),
            **self.runtime.runtime_timing_values(now),
            **self.introspection.counter_values(now),
        }

    def age_values(self, now: float) -> dict[str, float]:
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
            last_pm_confirmed=bool(getattr(svc, "_last_pm_status_confirmed", None)),
            now=now,
        )
        return {
            "auto_last_shelly_read_age": self.context.age(last_shelly_read_at, now),
            "auto_shelly_last_ok_age": self.context.age(getattr(svc, "_shelly_last_ok_at", None), now),
            "auto_pending_relay_age": self.context.age(getattr(svc, "_pending_relay_requested_at", None), now),
            "auto_last_pv_read_age": self.context.age(svc._last_pv_at, now),
            "auto_last_battery_read_age": self.context.age(svc._last_battery_soc_at, now),
            "auto_last_grid_read_age": self.context.age(svc._last_grid_at, now),
            "auto_last_dbus_read_age": self.context.age(svc._last_dbus_ok_at, now),
            "auto_charger_current_target_age": self.context.age(
                getattr(svc, "_charger_target_current_applied_at", None), now
            ),
            "auto_phase_candidate_age": self.context.age(getattr(svc, "_auto_phase_target_since", None), now),
            "auto_phase_lockout_age": self.context.age(
                getattr(svc, "_phase_switch_lockout_at", None)
                if self.runtime_views.summary.phase_switch_lockout_active(svc, now)
                else None,
                now,
            ),
            "auto_contactor_lockout_age": self.context.age(
                getattr(svc, "_contactor_lockout_at", None)
                if self.runtime_views.summary.contactor_lockout_active(svc)
                else None,
                now,
            ),
            "auto_last_switch_feedback_age": self.context.age(getattr(svc, "_last_switch_feedback_at", None), now),
            "auto_last_charger_read_age": self.context.age(getattr(svc, "_last_charger_state_at", None), now),
            "auto_last_charger_estimate_age": self.context.age(
                getattr(svc, "_last_charger_estimate_at", None)
                if self.learned.charger_estimate_active()
                else None,
                now,
            ),
            "auto_last_charger_transport_age": self.context.age(_fresh_charger_transport_timestamp(svc, now), now),
            "auto_charger_retry_remaining": float(_charger_retry_remaining_seconds(svc, now)),
            "auto_last_successful_update_age": self.context.age(svc._last_successful_update_at, now),
            "auto_software_update_last_check_age": self.context.age(
                getattr(svc, "_software_update_last_check_at", None),
                now,
            ),
            "auto_software_update_last_run_age": self.context.age(
                getattr(svc, "_software_update_last_run_at", None), now
            ),
            "auto_stale_seconds": self.context.age(stale_base, now),
            "auto_dbus_introspection_snapshot_age": self.introspection.snapshot_age(now),
        }

    def snapshot(self, now: float) -> DiagnosticSnapshot:
        """Return one immutable-cycle view of all diagnostic values."""
        return DiagnosticSnapshot(counters=self.counter_values(now), ages=self.age_values(now))

    def publish_diagnostic_paths(self, now: float) -> bool:
        """Publish diagnostics on change, except age-like values every five seconds."""
        snapshot = self.snapshot(now)
        changed = self.core.publish_fields("diagnostic-counters", snapshot.counters, now)
        changed |= self.core.publish_fields(
            "diagnostic-ages",
            snapshot.ages,
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
        return changed
