# SPDX-License-Identifier: GPL-3.0-or-later
"""Observability and worker-state helpers for runtime audit support."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.backend.models import effective_supported_phase_selections, switch_feedback_mismatch
from venus_evcharger.core.common import (
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_confirmed_relay_output,
    evse_fault_reason,
)
from venus_evcharger.core.contracts import finite_float_or_none, normalized_auto_state_pair, normalized_worker_snapshot

WorkerSnapshot = dict[str, Any]


def _normalized_optional_audit_text(value: object) -> str | None:
    """Return one stripped audit text or ``None`` when empty."""
    normalized = "" if value is None else str(value).strip()
    return normalized or None


class _RuntimeAuditFields:
    service: Any
    clone_worker_snapshot: Callable[[WorkerSnapshot], WorkerSnapshot]
    ensure_missing_attributes: Any
    observability_state_defaults: Any
    worker_state_defaults: Any

    @staticmethod
    def _backend_value(svc: Any, attribute_name: str, default: str = "") -> str:
        resolved = _resolved_backend_value(svc, attribute_name, default)
        if resolved is not None:
            return resolved
        raw_value = getattr(svc, attribute_name, default)
        normalized = str(raw_value).strip() if raw_value is not None else ""
        return normalized or default

    @classmethod
    def _charger_target_for_audit(cls, svc: Any) -> float | None:
        return finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))

    @staticmethod
    def _charger_transport_reason_for_audit(svc: Any) -> str | None:
        return _fresh_charger_transport_reason(svc)

    @staticmethod
    def _charger_transport_source_for_audit(svc: Any) -> str | None:
        return _fresh_charger_transport_source(svc)

    @staticmethod
    def _charger_retry_reason_for_audit(svc: Any) -> str | None:
        return _fresh_charger_retry_reason(svc)

    @staticmethod
    def _charger_retry_source_for_audit(svc: Any) -> str | None:
        return _fresh_charger_retry_source(svc)

    @staticmethod
    def _observed_phase_for_audit(svc: Any) -> str | None:
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        if isinstance(confirmed_pm_status, dict):
            observed = _normalized_optional_audit_text(confirmed_pm_status.get("_phase_selection"))
            if observed is not None:
                return observed
        return _normalized_optional_audit_text(getattr(svc, "_last_charger_state_phase_selection", None))

    @staticmethod
    def _phase_mismatch_active_for_audit(svc: Any) -> bool:
        return bool(getattr(svc, "_phase_switch_mismatch_active", False)) or (
            str(getattr(svc, "_last_health_reason", "")) == "phase-switch-mismatch"
        )

    @staticmethod
    def _callable_time_or_none(time_func: Any) -> float | None:
        if not callable(time_func):
            return None
        raw_value = time_func()
        if not isinstance(raw_value, (int, float)):
            return None
        return float(raw_value)

    @classmethod
    def _phase_lockout_active_for_audit(cls, svc: Any) -> bool:
        current_time = cls._callable_time_or_none(getattr(svc, "_time_now", None))
        if current_time is None:
            current_time = time.time()
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        return lockout_selection is not None and lockout_until is not None and float(current_time) < lockout_until

    @classmethod
    def _phase_lockout_target_for_audit(cls, svc: Any) -> str | None:
        if not cls._phase_lockout_active_for_audit(svc):
            return None
        selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if selection is None:
            return None
        normalized = str(selection).strip()
        return normalized or None

    @classmethod
    def _phase_supported_effective_for_audit(cls, svc: Any) -> str:
        current_time = cls._callable_time_or_none(getattr(svc, "_time_now", None))
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=current_time,
        )
        return ",".join(effective_supported)

    @classmethod
    def _phase_degraded_active_for_audit(cls, svc: Any) -> bool:
        configured = ",".join(tuple(getattr(svc, "supported_phase_selections", ("P1",))))
        return configured != cls._phase_supported_effective_for_audit(svc)

    @staticmethod
    def _switch_feedback_closed_for_audit(svc: Any) -> bool | None:
        value = getattr(svc, "_last_switch_feedback_closed", None)
        return None if value is None else bool(value)

    @staticmethod
    def _switch_interlock_ok_for_audit(svc: Any) -> bool | None:
        value = getattr(svc, "_last_switch_interlock_ok", None)
        return None if value is None else bool(value)

    @classmethod
    def _switch_feedback_mismatch_for_audit(cls, svc: Any) -> bool:
        current_time = cls._callable_time_or_none(getattr(svc, "_time_now", None))
        relay_on = _fresh_confirmed_relay_output(svc, current_time)
        feedback_closed = cls._switch_feedback_closed_for_audit(svc)
        if feedback_closed is None:
            return str(getattr(svc, "_last_health_reason", "")) == "contactor-feedback-mismatch"
        return switch_feedback_mismatch(relay_on, feedback_closed)

    @staticmethod
    def _contactor_lockout_reason_for_audit(svc: Any) -> str | None:
        reason = str(getattr(svc, "_contactor_lockout_reason", "") or "").strip()
        return reason or None

    @classmethod
    def _contactor_lockout_active_for_audit(cls, svc: Any) -> bool:
        return cls._contactor_lockout_reason_for_audit(svc) is not None

    @classmethod
    def _contactor_fault_count_for_audit(cls, svc: Any) -> int:
        counts = getattr(svc, "_contactor_fault_counts", None)
        if not isinstance(counts, dict):
            return 0
        reason = cls._contactor_lockout_reason_for_audit(svc)
        if reason is None:
            reason = _normalized_optional_audit_text(getattr(svc, "_contactor_fault_active_reason", ""))
        return 0 if reason is None else int(counts.get(reason, 0))

    @staticmethod
    def _evse_fault_reason_for_audit(svc: Any) -> str | None:
        return evse_fault_reason(getattr(svc, "_last_health_reason", ""))

    @classmethod
    def _evse_fault_active_for_audit(cls, svc: Any) -> bool:
        return cls._evse_fault_reason_for_audit(svc) is not None

    @staticmethod
    def _recovery_active_for_audit(svc: Any) -> bool:
        state, _state_code = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", "idle"),
            getattr(svc, "_last_auto_state_code", 0),
        )
        return state == "recovery"

    def _normalized_worker_snapshot(self, snapshot: WorkerSnapshot) -> WorkerSnapshot:
        current = self._callable_time_or_none(getattr(self.service, "_time_now", None))
        return normalized_worker_snapshot(snapshot, now=current)

    def ensure_worker_state(self) -> None:
        self.ensure_missing_attributes(self.service, self.worker_state_defaults())

    def set_worker_snapshot(self, snapshot: WorkerSnapshot) -> None:
        svc = self.service
        svc._ensure_worker_state()
        cloned = self.clone_worker_snapshot(self._normalized_worker_snapshot(snapshot))
        with svc._worker_snapshot_lock:
            svc._worker_snapshot = cloned

    def update_worker_snapshot(self, **fields: Any) -> None:
        svc = self.service
        svc._ensure_worker_state()
        with svc._worker_snapshot_lock:
            merged = self.clone_worker_snapshot(svc._worker_snapshot)
            merged.update(fields)
            svc._worker_snapshot = self.clone_worker_snapshot(self._normalized_worker_snapshot(merged))

    def get_worker_snapshot(self) -> WorkerSnapshot:
        svc = self.service
        svc._ensure_worker_state()
        with svc._worker_snapshot_lock:
            return self.clone_worker_snapshot(svc._worker_snapshot)

    def ensure_observability_state(self) -> None:
        self.ensure_missing_attributes(self.service, self.observability_state_defaults())


def _resolved_backend_value(svc: Any, attribute_name: str, default: str) -> str | None:
    """Return resolved backend metadata for known audit attribute names."""
    if attribute_name == "backend_mode":
        return backend_mode_for_service(svc, default)
    role = _backend_role_name(attribute_name)
    if role is None:
        return None
    return backend_type_for_service(svc, role, default)


def _backend_role_name(attribute_name: str) -> str | None:
    """Return the backend role implied by one audit attribute name."""
    if attribute_name == "meter_backend_type":
        return "meter"
    if attribute_name == "switch_backend_type":
        return "switch"
    if attribute_name == "charger_backend_type":
        return "charger"
    return None
