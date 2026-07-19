# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized runtime-state views used by DBus diagnostics."""

from __future__ import annotations

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.backend.models import effective_supported_phase_selections, switch_feedback_mismatch
from venus_evcharger.core.common import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    ScheduledModeSnapshot,
    evse_fault_reason,
    local_datetime_from_timestamp,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)
from venus_evcharger.core.contracts import finite_float_or_none, non_negative_int, normalize_auto_state
from venus_evcharger.publish.dbus_shared import diagnostic_text, is_object_mapping

PHASE_SWITCH_MISMATCH_REASON = "phase-switch-mismatch"
CONTACTOR_FEEDBACK_MISMATCH_REASON = "contactor-feedback-mismatch"
CONTACTOR_SUSPECTED_OPEN_REASON = "contactor-suspected-open"
CONTACTOR_SUSPECTED_WELDED_REASON = "contactor-suspected-welded"


class DbusRuntimeView:
    """Normalize dynamic service state for outward diagnostic fields."""

    @staticmethod
    def backend_mode_value(service: object) -> str:
        return str(backend_mode_for_service(service))

    @staticmethod
    def backend_type_value(service: object, attribute_name: str, default: str = "") -> str:
        role = _backend_role_from_type_attribute(attribute_name)
        if role is None:
            if not hasattr(service, attribute_name):
                return default
            raw_value = getattr(service, attribute_name)
            normalized = str(raw_value).strip() if raw_value is not None else ""
            return normalized or default
        return str(backend_type_for_service(service, role, default))

    @staticmethod
    def charger_current_target_value(service: object) -> float:
        target_amps = finite_float_or_none(getattr(service, "_charger_target_current_amps", None))
        return -1.0 if target_amps is None else float(target_amps)

    @staticmethod
    def auto_metrics(service: object) -> dict[str, object]:
        metrics = getattr(service, "_last_auto_metrics", None)
        if not is_object_mapping(metrics):
            return {}
        return {str(key): value for key, value in metrics.items()}

    @classmethod
    def auto_phase_metric_text(cls, service: object, field_name: str) -> str:
        raw_value = cls.auto_metrics(service).get(field_name)
        return diagnostic_text(raw_value)

    @staticmethod
    def service_text_value(service: object, attribute_name: str) -> str:
        return diagnostic_text(getattr(service, attribute_name, None))

    @classmethod
    def health_reason(cls, service: object) -> str:
        return cls.service_text_value(service, "_last_health_reason")

    @classmethod
    def fault_reason(cls, service: object) -> str:
        reason = evse_fault_reason(cls.health_reason(service))
        return "" if reason is None else str(reason)

    @classmethod
    def fault_active(cls, service: object) -> int:
        return int(bool(cls.fault_reason(service)))

    @staticmethod
    def scheduled_snapshot(service: object, now: float) -> ScheduledModeSnapshot | None:
        if not mode_uses_scheduled_logic(getattr(service, "virtual_mode", None)):
            return None
        enabled_days = getattr(service, "auto_scheduled_enabled_days", None)
        latest_end_time = getattr(service, "auto_scheduled_latest_end_time", None)
        return scheduled_mode_snapshot(
            local_datetime_from_timestamp(now, getattr(service, "auto_schedule_timezone", "UTC")),
            getattr(service, "auto_month_windows", None),
            DEFAULT_SCHEDULED_ENABLED_DAYS if enabled_days is None else enabled_days,
            delay_seconds=float(getattr(service, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=latest_end_time,
        )

    @staticmethod
    def recovery_active(service: object) -> int:
        return int(normalize_auto_state(getattr(service, "_last_auto_state", None)) == "recovery")

    @classmethod
    def observed_phase_value(cls, service: object) -> str:
        pm_status = getattr(service, "_last_confirmed_pm_status", None)
        if is_object_mapping(pm_status):
            observed = diagnostic_text(pm_status.get("_phase_selection"))
            if observed:
                return observed
        return diagnostic_text(getattr(service, "_last_charger_state_phase_selection", None))

    @classmethod
    def phase_switch_mismatch_active(cls, service: object) -> int:
        if bool(getattr(service, "_phase_switch_mismatch_active", None)):
            return 1
        return int(cls.health_reason(service) == PHASE_SWITCH_MISMATCH_REASON)

    @staticmethod
    def phase_switch_lockout_active(service: object, now: float) -> int:
        lockout_selection = getattr(service, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(service, "_phase_switch_lockout_until", None))
        if lockout_selection is None or lockout_until is None:
            return 0
        return int(float(now) < float(lockout_until))

    @classmethod
    def phase_switch_lockout_target(cls, service: object, now: float) -> str:
        if cls.phase_switch_lockout_active(service, now) == 0:
            return ""
        return cls.service_text_value(service, "_phase_switch_lockout_selection")

    @classmethod
    def phase_switch_lockout_reason(cls, service: object, now: float) -> str:
        if cls.phase_switch_lockout_active(service, now) == 0:
            return ""
        return cls.service_text_value(service, "_phase_switch_lockout_reason")

    @staticmethod
    def configured_supported_phase_selections(service: object) -> object:
        return getattr(service, "supported_phase_selections", None)

    @classmethod
    def phase_supported_configured(cls, service: object) -> str:
        return ",".join(effective_supported_phase_selections(cls.configured_supported_phase_selections(service)))

    @classmethod
    def phase_supported_effective(cls, service: object, now: float) -> str:
        supported = effective_supported_phase_selections(
            cls.configured_supported_phase_selections(service),
            lockout_selection=getattr(service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(service, "_phase_switch_lockout_until", None),
            now=now,
        )
        return ",".join(supported)

    @classmethod
    def phase_degraded_active(cls, service: object, now: float) -> int:
        return int(cls.phase_supported_configured(service) != cls.phase_supported_effective(service, now))

    @staticmethod
    def switch_feedback_closed(service: object) -> int:
        feedback_closed = getattr(service, "_last_switch_feedback_closed", None)
        return -1 if feedback_closed is None else int(bool(feedback_closed))

    @staticmethod
    def switch_interlock_ok(service: object) -> int:
        interlock_ok = getattr(service, "_last_switch_interlock_ok", None)
        return -1 if interlock_ok is None else int(bool(interlock_ok))

    @classmethod
    def switch_feedback_mismatch(cls, service: object) -> int:
        feedback_closed = getattr(service, "_last_switch_feedback_closed", None)
        if feedback_closed is None:
            return int(cls.health_reason(service) == CONTACTOR_FEEDBACK_MISMATCH_REASON)
        pm_status = getattr(service, "_last_confirmed_pm_status", None)
        relay_on = False if not is_object_mapping(pm_status) else bool(pm_status.get("output"))
        return int(switch_feedback_mismatch(relay_on, feedback_closed))

    @classmethod
    def contactor_suspected_open(cls, service: object) -> int:
        return int(cls.health_reason(service) == CONTACTOR_SUSPECTED_OPEN_REASON)

    @classmethod
    def contactor_suspected_welded(cls, service: object) -> int:
        return int(cls.health_reason(service) == CONTACTOR_SUSPECTED_WELDED_REASON)

    @classmethod
    def contactor_lockout_reason(cls, service: object) -> str:
        return cls.service_text_value(service, "_contactor_lockout_reason")

    @classmethod
    def contactor_lockout_active(cls, service: object) -> int:
        return int(bool(cls.contactor_lockout_reason(service)))

    @classmethod
    def contactor_lockout_source(cls, service: object) -> str:
        return cls.service_text_value(service, "_contactor_lockout_source")

    @classmethod
    def contactor_fault_count(cls, service: object) -> int:
        counts = getattr(service, "_contactor_fault_counts", None)
        if not is_object_mapping(counts):
            return 0
        reason = cls.contactor_lockout_reason(service)
        if not reason:
            reason = cls.service_text_value(service, "_contactor_fault_active_reason")
        if not reason:
            return 0
        return int(non_negative_int(counts.get(reason, 0)))

    @classmethod
    def auto_phase_metric_float(cls, service: object, field_name: str) -> float:
        value = finite_float_or_none(cls.auto_metrics(service).get(field_name))
        return -1.0 if value is None else float(value)


def _backend_role_from_type_attribute(attribute_name: str) -> str | None:
    suffix = "_backend_type"
    if not attribute_name.endswith(suffix):
        return None
    role = attribute_name[: -len(suffix)]
    return role if role in {"meter", "switch", "charger"} else None
