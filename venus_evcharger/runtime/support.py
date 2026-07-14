# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Venus EV charger service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import uuid
import time
from typing import Any

import requests

from venus_evcharger.backend.models import normalize_phase_selection, normalize_phase_selection_tuple

WorkerSnapshot = dict[str, Any]
ErrorState = dict[str, int]
FailureState = dict[str, bool]
DefaultFactory = Callable[[], Any]

from venus_evcharger.runtime.health import _RuntimeHealth


class RuntimeSupportController(_RuntimeHealth):
    """Encapsulate runtime caches, worker state, and observability/watchdog logic."""

    SOURCE_ERROR_KEYS: tuple[str, ...] = ("dbus", "shelly", "charger", "pv", "battery", "grid")
    def __init__(
        self,
        service: Any,
        age_seconds_func: Callable[[float | int | None, float | int | None], int],
        health_code_func: Callable[[str], int],
    ) -> None:
        self.service = service
        self._age_seconds = age_seconds_func
        self._health_code = health_code_func

    @classmethod
    def new_error_state(cls) -> ErrorState:
        """Return the default per-source error counters."""
        state = {key: 0 for key in cls.SOURCE_ERROR_KEYS}
        state["cache_hits"] = 0
        return state

    @classmethod
    def new_failure_state(cls) -> FailureState:
        """Return the default active/inactive failure flags."""
        return {key: False for key in cls.SOURCE_ERROR_KEYS}

    def worker_state_defaults(self) -> dict[str, DefaultFactory]:
        """Return lazy default values for worker-side runtime state."""
        svc = self.service
        return {
            "_worker_poll_interval_seconds": lambda: max(
                0.2,
                getattr(svc, "poll_interval_ms", 1000) / 1000.0,
            ),
            "_worker_snapshot_lock": threading.Lock,
            "_relay_command_lock": threading.Lock,
            "_worker_snapshot": self.empty_worker_snapshot,
            "_worker_stop_event": threading.Event,
            "_worker_session": requests.Session,
            "_worker_thread": lambda: None,
            "_pending_relay_state": lambda: None,
            "_pending_relay_requested_at": lambda: None,
            "relay_sync_timeout_seconds": lambda: max(
                2.0,
                max(0.2, getattr(svc, "poll_interval_ms", 1000) / 1000.0) * 3.0,
            ),
            "_relay_sync_expected_state": lambda: None,
            "_relay_sync_requested_at": lambda: None,
            "_relay_sync_deadline_at": lambda: None,
            "_relay_sync_failure_reported": lambda: False,
            "_auto_input_helper_process": lambda: None,
            "_auto_input_helper_generation": lambda: 0,
            "_auto_input_runtime_instance_id": lambda: uuid.uuid4().hex,
            "_auto_input_helper_last_start_at": lambda: 0.0,
            "_auto_input_helper_restart_requested_at": lambda: None,
            "_auto_input_snapshot_last_seen": lambda: None,
            "_auto_input_snapshot_seen_for_current_helper": lambda: False,
            "_auto_input_snapshot_mtime_ns": lambda: None,
            "_auto_input_snapshot_last_captured_at": lambda: None,
            "_auto_input_snapshot_version": lambda: None,
            "_auto_input_snapshot_writer_pid": lambda: None,
            "_auto_input_snapshot_generation": lambda: None,
            "_auto_input_snapshot_runtime_instance_id": lambda: None,
            "auto_input_snapshot_path": lambda: (
                f"/run/dbus-venus-evcharger-auto-{getattr(svc, 'deviceinstance', 0)}.json"
            ),
            "auto_input_helper_restart_seconds": lambda: 5.0,
            "auto_input_helper_stale_seconds": lambda: 15.0,
            "dbus_introspection_enabled": lambda: True,
            "dbus_introspection_snapshot_path": lambda: (
                f"/run/dbus-venus-evcharger-dbus-map-{getattr(svc, 'deviceinstance', 0)}.json"
            ),
            "dbus_introspection_request_path": lambda: (
                f"/run/dbus-venus-evcharger-dbus-map-requests-{getattr(svc, 'deviceinstance', 0)}.json"
            ),
            "dbus_introspection_max_age_seconds": lambda: 900.0,
            "_auto_mode_cutover_pending": lambda: False,
            "_ignore_min_offtime_once": lambda: False,
        }

    @staticmethod
    def observability_state_defaults() -> dict[str, DefaultFactory]:
        """Return lazy default values for diagnostics and watchdog state."""
        return {
            "_warning_state": dict,
            "_error_state": RuntimeSupportController.new_error_state,
            "_failure_active": RuntimeSupportController.new_failure_state,
            "_last_dbus_ok_at": lambda: None,
            "_last_successful_update_at": lambda: None,
            "_last_recovery_attempt_at": lambda: None,
            "_recovery_attempts": lambda: 0,
            "_last_auto_state": lambda: "idle",
            "_last_auto_state_code": lambda: 0,
            "_last_status_source": lambda: "unknown",
            "_last_charger_fault_active": lambda: 0,
            "_last_pm_status_at": lambda: None,
            "_last_pm_status_confirmed": lambda: False,
            "_last_confirmed_pm_status": lambda: None,
            "_last_confirmed_pm_status_at": lambda: None,
            "_shelly_state": lambda: "unknown",
            "_shelly_last_error_reason": lambda: "",
            "_shelly_last_error_detail": lambda: "",
            "_shelly_last_error_at": lambda: None,
            "_shelly_consecutive_errors": lambda: 0,
            "_shelly_last_ok_at": lambda: None,
            "_shelly_retry_after": lambda: 0.0,
            "_shelly_session_reset_count": lambda: 0,
            "_shelly_offline_since": lambda: None,
            "_last_charger_state_enabled": lambda: None,
            "_last_charger_state_current_amps": lambda: None,
            "_last_charger_state_phase_selection": lambda: None,
            "_last_charger_state_actual_current_amps": lambda: None,
            "_last_charger_state_power_w": lambda: None,
            "_last_charger_state_energy_kwh": lambda: None,
            "_last_charger_state_status": lambda: None,
            "_last_charger_state_fault": lambda: None,
            "_last_charger_state_at": lambda: None,
            "_last_charger_transport_reason": lambda: None,
            "_last_charger_transport_source": lambda: None,
            "_last_charger_transport_detail": lambda: None,
            "_last_charger_transport_at": lambda: None,
            "_charger_retry_reason": lambda: None,
            "_charger_retry_source": lambda: None,
            "_charger_retry_until": lambda: None,
            "_last_switch_feedback_closed": lambda: None,
            "_last_switch_interlock_ok": lambda: None,
            "_last_switch_feedback_at": lambda: None,
            "_contactor_suspected_open_since": lambda: None,
            "_contactor_suspected_welded_since": lambda: None,
            "_contactor_fault_counts": dict,
            "_contactor_fault_active_reason": lambda: None,
            "_contactor_fault_active_since": lambda: None,
            "_contactor_lockout_reason": lambda: "",
            "_contactor_lockout_source": lambda: "",
            "_contactor_lockout_at": lambda: None,
            "_charger_target_current_amps": lambda: None,
            "_charger_target_current_applied_at": lambda: None,
            "active_phase_selection": lambda: normalize_phase_selection("P1"),
            "requested_phase_selection": lambda: normalize_phase_selection("P1"),
            "supported_phase_selections": lambda: normalize_phase_selection_tuple(("P1",), ("P1",)),
            "_phase_switch_pending_selection": lambda: None,
            "_phase_switch_state": lambda: None,
            "_phase_switch_requested_at": lambda: None,
            "_phase_switch_stable_until": lambda: None,
            "_phase_switch_resume_relay": lambda: False,
            "_phase_switch_mismatch_active": lambda: False,
            "_phase_switch_mismatch_counts": dict,
            "_phase_switch_last_mismatch_selection": lambda: None,
            "_phase_switch_last_mismatch_at": lambda: None,
            "_phase_switch_lockout_selection": lambda: None,
            "_phase_switch_lockout_reason": lambda: "",
            "_phase_switch_lockout_at": lambda: None,
            "_phase_switch_lockout_until": lambda: None,
            "_auto_phase_target_candidate": lambda: None,
            "_auto_phase_target_since": lambda: None,
            "_last_pv_at": lambda: None,
            "_last_battery_soc_at": lambda: None,
            "_last_grid_at": lambda: None,
            "_grid_recovery_required": lambda: False,
            "_grid_recovery_since": lambda: None,
            "_source_retry_after": dict,
            "_last_auto_audit_key": lambda: None,
            "_last_auto_audit_event_at": lambda: None,
            "_last_auto_audit_cleanup_at": lambda: 0.0,
            "_auto_high_soc_profile_active": lambda: None,
            "started_at": time.time,
            "auto_watchdog_stale_seconds": lambda: 180.0,
            "auto_watchdog_recovery_seconds": lambda: 60.0,
            "auto_watchdog_restart_attempts": lambda: 5,
            "auto_audit_log": lambda: False,
            "auto_audit_log_path": lambda: "/var/volatile/log/dbus-venus-evcharger/auto-reasons.log",
            "auto_audit_log_max_age_hours": lambda: 168.0,
            "auto_audit_log_repeat_seconds": lambda: 30.0,
        }

    @staticmethod
    def ensure_missing_attributes(service: Any, defaults: dict[str, DefaultFactory]) -> None:
        """Populate any missing attributes from a lazy defaults mapping."""
        for name, factory in defaults.items():
            if hasattr(service, name):
                continue
            setattr(service, name, factory())
