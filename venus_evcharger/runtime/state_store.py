# SPDX-License-Identifier: GPL-3.0-or-later
"""RAM-only runtime defaults and atomic worker snapshot ownership."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import requests

from venus_evcharger.backend.models import normalize_phase_selection, normalize_phase_selection_tuple
from venus_evcharger.core.contracts import normalized_worker_snapshot
from venus_evcharger.runtime.contracts import DefaultFactory, WorkerSnapshot
from venus_evcharger.runtime.setup_support import (
    clone_worker_battery_sources_payload,
    clone_worker_learning_profiles_payload,
    clone_worker_status_payload,
    empty_worker_snapshot,
)

SOURCE_ERROR_KEYS: tuple[str, ...] = ("dbus", "shelly", "charger", "pv", "battery", "grid")


class RuntimeStateStore:
    """Own lazy defaults and synchronized snapshots for one service."""

    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def new_error_state() -> dict[str, int]:
        state = {key: 0 for key in SOURCE_ERROR_KEYS}
        state["cache_hits"] = 0
        return state

    @staticmethod
    def new_failure_state() -> dict[str, bool]:
        return {key: False for key in SOURCE_ERROR_KEYS}

    @staticmethod
    def ensure_missing_attributes(service: Any, defaults: dict[str, DefaultFactory]) -> None:
        for name, factory in defaults.items():
            if not hasattr(service, name):
                setattr(service, name, factory())

    def worker_defaults(self) -> dict[str, DefaultFactory]:
        svc = self.service
        return {
            "_worker_poll_interval_seconds": lambda: max(0.2, getattr(svc, "poll_interval_ms", 1000) / 1000.0),
            "_worker_snapshot_lock": threading.Lock,
            "_relay_command_lock": threading.Lock,
            "_worker_snapshot": self.empty_snapshot,
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
            "_auto_mode_cutover_pending": lambda: False,
            "_ignore_min_offtime_once": lambda: False,
        }

    @staticmethod
    def observability_defaults() -> dict[str, DefaultFactory]:
        return {
            "_warning_state": dict,
            "_error_state": RuntimeStateStore.new_error_state,
            "_failure_active": RuntimeStateStore.new_failure_state,
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
    def empty_snapshot() -> WorkerSnapshot:
        return {str(key): value for key, value in empty_worker_snapshot().items()}

    @staticmethod
    def clone_snapshot(snapshot: WorkerSnapshot) -> WorkerSnapshot:
        cloned = dict(snapshot)
        clone_worker_status_payload(cloned)
        clone_worker_battery_sources_payload(cloned)
        clone_worker_learning_profiles_payload(cloned)
        return cloned

    def initialize_worker_state(self) -> None:
        svc = self.service
        svc._worker_poll_interval_seconds = max(0.2, svc.poll_interval_ms / 1000.0)
        svc._worker_snapshot_lock = threading.Lock()
        svc._relay_command_lock = threading.Lock()
        svc._worker_snapshot = self.empty_snapshot()
        svc._worker_stop_event = threading.Event()
        svc._worker_session = requests.Session()
        svc._worker_thread = None
        svc._pending_relay_state = None
        svc._pending_relay_requested_at = None
        svc.relay_sync_timeout_seconds = max(2.0, svc._worker_poll_interval_seconds * 3.0)
        svc._relay_sync_expected_state = None
        svc._relay_sync_requested_at = None
        svc._relay_sync_deadline_at = None
        svc._relay_sync_failure_reported = False
        svc._auto_input_helper_process = None
        svc._auto_input_helper_generation = 0
        svc._auto_input_runtime_instance_id = uuid.uuid4().hex
        svc._auto_input_helper_last_start_at = 0.0
        svc._auto_input_helper_restart_requested_at = None
        svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_seen_for_current_helper = False
        svc._auto_input_snapshot_mtime_ns = None
        svc._auto_input_snapshot_last_captured_at = None
        svc._auto_input_snapshot_version = None
        svc._auto_input_snapshot_writer_pid = None
        svc._auto_input_snapshot_generation = None
        svc._auto_input_snapshot_runtime_instance_id = None

    def ensure_worker_state(self) -> None:
        self.ensure_missing_attributes(self.service, self.worker_defaults())

    def ensure_observability_state(self) -> None:
        self.ensure_missing_attributes(self.service, self.observability_defaults())

    def normalized_worker_snapshot(self, snapshot: WorkerSnapshot) -> WorkerSnapshot:
        time_func = getattr(self.service, "time_now", None)
        raw_now = time_func() if callable(time_func) else None
        now = float(raw_now) if isinstance(raw_now, (int, float)) else None
        normalized = normalized_worker_snapshot(snapshot, now=now)
        return {str(key): value for key, value in normalized.items()}

    def set_worker_snapshot(self, snapshot: WorkerSnapshot) -> None:
        svc = self.service
        self.ensure_worker_state()
        cloned = self.clone_snapshot(self.normalized_worker_snapshot(snapshot))
        with svc._worker_snapshot_lock:
            svc._worker_snapshot = cloned

    def update_worker_snapshot(self, **fields: Any) -> None:
        svc = self.service
        self.ensure_worker_state()
        with svc._worker_snapshot_lock:
            merged = self.clone_snapshot(svc._worker_snapshot)
            merged.update(fields)
            svc._worker_snapshot = self.clone_snapshot(self.normalized_worker_snapshot(merged))

    def get_worker_snapshot(self) -> WorkerSnapshot:
        svc = self.service
        self.ensure_worker_state()
        with svc._worker_snapshot_lock:
            return self.clone_snapshot(svc._worker_snapshot)


__all__ = ["RuntimeStateStore", "SOURCE_ERROR_KEYS"]
