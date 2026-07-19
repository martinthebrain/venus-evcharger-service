# SPDX-License-Identifier: GPL-3.0-or-later
"""Observability and worker-state helpers for runtime audit support."""

from __future__ import annotations

import time
from typing import Any, TypeGuard

from venus_evcharger.backend.config_service_labels import backend_mode_for_service, backend_type_for_service
from venus_evcharger.backend.models import effective_supported_phase_selections, switch_feedback_mismatch
from venus_evcharger.core.common_auto import (
    _evse_fault_reason,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_confirmed_relay_output,
)
from venus_evcharger.core.contracts_basic import finite_float_or_none, normalized_auto_state_pair


def _normalized_optional_audit_text(value: object) -> str | None:
    """Return one stripped audit text or ``None`` when empty."""
    normalized = "" if value is None else str(value).strip()
    return normalized or None


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    """Return whether a dynamic boundary value is a dictionary."""
    return isinstance(value, dict)


def _audit_mapping(value: object) -> dict[str, object]:
    """Normalize one dynamic runtime mapping at the audit boundary."""
    if not _is_object_dict(value):
        return {}
    return {str(key): item for key, item in value.items()}


class RuntimeAuditFields:
    """Project dynamic runtime state into stable audit values."""

    @staticmethod
    def backend_value(svc: Any, attribute_name: str, default: str) -> str:
        resolved = _resolved_backend_value(svc, attribute_name, default)
        if resolved is not None:
            return resolved
        raw_value = getattr(svc, attribute_name, None)
        normalized = str(raw_value).strip() if raw_value is not None else ""
        return normalized or default

    @classmethod
    def charger_target(cls, svc: Any) -> float | None:
        normalized = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        return None if normalized is None else float(normalized)

    @staticmethod
    def charger_transport_reason(svc: Any) -> str | None:
        return _normalized_optional_audit_text(_fresh_charger_transport_reason(svc))

    @staticmethod
    def charger_transport_source(svc: Any) -> str | None:
        return _normalized_optional_audit_text(_fresh_charger_transport_source(svc))

    @staticmethod
    def charger_retry_reason(svc: Any) -> str | None:
        return _normalized_optional_audit_text(_fresh_charger_retry_reason(svc))

    @staticmethod
    def charger_retry_source(svc: Any) -> str | None:
        return _normalized_optional_audit_text(_fresh_charger_retry_source(svc))

    @staticmethod
    def observed_phase(svc: Any) -> str | None:
        confirmed_pm_status = _audit_mapping(getattr(svc, "_last_confirmed_pm_status", None))
        if confirmed_pm_status:
            observed = _normalized_optional_audit_text(confirmed_pm_status.get("_phase_selection"))
            if observed is not None:
                return observed
        return _normalized_optional_audit_text(getattr(svc, "_last_charger_state_phase_selection", None))

    @staticmethod
    def phase_mismatch_active(svc: Any) -> bool:
        return bool(getattr(svc, "_phase_switch_mismatch_active", None)) or (
            getattr(svc, "_last_health_reason", None) == "phase-switch-mismatch"
        )

    @staticmethod
    def callable_time_or_none(time_func: Any) -> float | None:
        if not callable(time_func):
            return None
        raw_value = time_func()
        if not isinstance(raw_value, (int, float)):
            return None
        return float(raw_value)

    @classmethod
    def phase_lockout_active(cls, svc: Any) -> bool:
        current_time = cls.callable_time_or_none(getattr(svc, "time_now", None))
        if current_time is None:
            current_time = time.time()
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        return lockout_selection is not None and lockout_until is not None and float(current_time) < lockout_until

    @classmethod
    def phase_lockout_target(cls, svc: Any) -> str | None:
        if not cls.phase_lockout_active(svc):
            return None
        selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if selection is None:
            return None
        normalized = str(selection).strip()
        return normalized or None

    @classmethod
    def phase_supported_effective(cls, svc: Any) -> str:
        current_time = cls.callable_time_or_none(getattr(svc, "time_now", None))
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=current_time,
        )
        return ",".join(effective_supported)

    @classmethod
    def phase_degraded_active(cls, svc: Any) -> bool:
        configured = ",".join(tuple(getattr(svc, "supported_phase_selections", ("P1",))))
        return configured != cls.phase_supported_effective(svc)

    @staticmethod
    def switch_feedback_closed(svc: Any) -> bool | None:
        value = getattr(svc, "_last_switch_feedback_closed", None)
        return None if value is None else bool(value)

    @staticmethod
    def switch_interlock_ok(svc: Any) -> bool | None:
        value = getattr(svc, "_last_switch_interlock_ok", None)
        return None if value is None else bool(value)

    @classmethod
    def switch_feedback_mismatch(cls, svc: Any) -> bool:
        current_time = cls.callable_time_or_none(getattr(svc, "time_now", None))
        relay_on = _fresh_confirmed_relay_output(svc, current_time)
        feedback_closed = cls.switch_feedback_closed(svc)
        if feedback_closed is None:
            return getattr(svc, "_last_health_reason", None) == "contactor-feedback-mismatch"
        return bool(switch_feedback_mismatch(relay_on, feedback_closed))

    @staticmethod
    def contactor_lockout_reason(svc: Any) -> str | None:
        return _normalized_optional_audit_text(getattr(svc, "_contactor_lockout_reason", None))

    @classmethod
    def contactor_lockout_active(cls, svc: Any) -> bool:
        return cls.contactor_lockout_reason(svc) is not None

    @classmethod
    def contactor_fault_count(cls, svc: Any) -> int:
        counts = _audit_mapping(getattr(svc, "_contactor_fault_counts", None))
        if not counts:
            return 0
        reason = cls.contactor_lockout_reason(svc)
        if reason is None:
            reason = _normalized_optional_audit_text(getattr(svc, "_contactor_fault_active_reason", None))
        raw_count = 0 if reason is None else counts.get(reason, 0)
        return int(raw_count) if isinstance(raw_count, (int, float)) else 0

    @staticmethod
    def evse_fault_reason(svc: Any) -> str | None:
        return _normalized_optional_audit_text(_evse_fault_reason(getattr(svc, "_last_health_reason", None)))

    @classmethod
    def evse_fault_active(cls, svc: Any) -> bool:
        return cls.evse_fault_reason(svc) is not None

    @staticmethod
    def recovery_active(svc: Any) -> bool:
        state_pair = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", "idle"),
            getattr(svc, "_last_auto_state_code", 0),
        )
        return str(state_pair[0]) == "recovery"


def _resolved_backend_value(svc: Any, attribute_name: str, default: str) -> str | None:
    """Return resolved backend metadata for known audit attribute names."""
    if attribute_name == "backend_mode":
        return str(backend_mode_for_service(svc, default))
    role = _backend_role_name(attribute_name)
    if role is None:
        return None
    return str(backend_type_for_service(svc, role, default))


def _backend_role_name(attribute_name: str) -> str | None:
    """Return the backend role implied by one audit attribute name."""
    if attribute_name == "meter_backend_type":
        return "meter"
    if attribute_name == "switch_backend_type":
        return "switch"
    if attribute_name == "charger_backend_type":
        return "charger"
    return None


__all__ = ["RuntimeAuditFields"]
