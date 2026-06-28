# SPDX-License-Identifier: GPL-3.0-or-later
"""State-summary helpers for the Venus EV charger service."""

from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.backend.models import effective_supported_phase_selections, normalize_phase_selection_tuple, switch_feedback_mismatch
from venus_evcharger.core.common import (
    _charger_retry_remaining_seconds,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    evse_fault_reason,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _StateSummaryMixin(_ComposableControllerMixin):
    @staticmethod
    def _summary_flag(value: object) -> str:
        return str(int(bool(value)))

    @staticmethod
    def _summary_text(value: object, default: str) -> str:
        text = str(value).strip() if value is not None else ""
        return text or default

    @staticmethod
    def _summary_float(value: object, default: str = "na") -> str:
        normalized = finite_float_or_none(value)
        return default if normalized is None else f"{normalized:.1f}"

    @classmethod
    def _summary_observed_phase(cls, svc: Any) -> str:
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        if isinstance(confirmed_pm_status, dict):
            observed = cls._summary_text(confirmed_pm_status.get("_phase_selection"), "")
            if observed:
                return observed
        return cls._summary_text(getattr(svc, "_last_charger_state_phase_selection", None), "na")

    @staticmethod
    def _summary_phase_mismatch_active(svc: Any) -> str:
        active = bool(getattr(svc, "_phase_switch_mismatch_active", False))
        if active:
            return "1"
        return "1" if str(getattr(svc, "_last_health_reason", "")) == "phase-switch-mismatch" else "0"

    @classmethod
    def _summary_phase_lockout_active(cls, svc: Any) -> str:
        current_time = time.time()
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_until is None or lockout_selection is None:
            return "0"
        return "1" if lockout_until > current_time else "0"

    @classmethod
    def _summary_phase_lockout_target(cls, svc: Any) -> str:
        if cls._summary_phase_lockout_active(svc) != "1":
            return "na"
        return cls._summary_text(getattr(svc, "_phase_switch_lockout_selection", None), "na")

    @classmethod
    def _summary_phase_supported_effective(cls, svc: Any) -> str:
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=time.time(),
        )
        return ",".join(effective_supported)

    @classmethod
    def _summary_phase_degraded_active(cls, svc: Any) -> str:
        configured = normalize_phase_selection_tuple(getattr(svc, "supported_phase_selections", ("P1",)), ("P1",))
        effective = effective_supported_phase_selections(
            configured,
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=time.time(),
        )
        return "1" if configured != effective else "0"

    @staticmethod
    def _summary_switch_feedback_closed(svc: Any) -> str:
        feedback_closed = getattr(svc, "_last_switch_feedback_closed", None)
        return "na" if feedback_closed is None else str(int(bool(feedback_closed)))

    @staticmethod
    def _summary_switch_interlock_ok(svc: Any) -> str:
        interlock_ok = getattr(svc, "_last_switch_interlock_ok", None)
        return "na" if interlock_ok is None else str(int(bool(interlock_ok)))

    @classmethod
    def _summary_switch_feedback_mismatch(cls, svc: Any) -> str:
        feedback_closed = getattr(svc, "_last_switch_feedback_closed", None)
        if feedback_closed is None:
            return "1" if str(getattr(svc, "_last_health_reason", "")) == "contactor-feedback-mismatch" else "0"
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        relay_on = False if not isinstance(confirmed_pm_status, dict) else bool(confirmed_pm_status.get("output", False))
        return str(int(switch_feedback_mismatch(relay_on, feedback_closed)))

    @staticmethod
    def _summary_contactor_count_reason(svc: Any) -> str:
        lockout_reason = str(getattr(svc, "_contactor_lockout_reason", "") or "")
        return lockout_reason or str(getattr(svc, "_contactor_fault_active_reason", "") or "")

    @classmethod
    def _summary_contactor_fault_count(cls, svc: Any) -> str:
        counts = getattr(svc, "_contactor_fault_counts", None)
        if not isinstance(counts, dict):
            return "0"
        reason = cls._summary_contactor_count_reason(svc)
        if not reason:
            return "0"
        return str(int(counts.get(reason, 0)))

    @staticmethod
    def _summary_contactor_suspected_open(svc: Any) -> str:
        return "1" if str(getattr(svc, "_last_health_reason", "")) == "contactor-suspected-open" else "0"

    @staticmethod
    def _summary_contactor_suspected_welded(svc: Any) -> str:
        return "1" if str(getattr(svc, "_last_health_reason", "")) == "contactor-suspected-welded" else "0"

    @staticmethod
    def _summary_contactor_lockout_active(svc: Any) -> str:
        return "1" if str(getattr(svc, "_contactor_lockout_reason", "") or "") else "0"

    @classmethod
    def _summary_contactor_lockout_reason(cls, svc: Any) -> str:
        if cls._summary_contactor_lockout_active(svc) != "1":
            return "na"
        return cls._summary_text(getattr(svc, "_contactor_lockout_reason", None), "na")

    @staticmethod
    def _summary_fault_active(svc: Any) -> str:
        return "1" if evse_fault_reason(getattr(svc, "_last_health_reason", "")) is not None else "0"

    @staticmethod
    def _summary_fault_reason(svc: Any) -> str:
        reason = evse_fault_reason(getattr(svc, "_last_health_reason", ""))
        return "na" if reason is None else reason

    @classmethod
    def _summary_charger_transport_reason(cls, svc: Any) -> str:
        return cls._summary_text(_fresh_charger_transport_reason(svc, time.time()), "na")

    @classmethod
    def _summary_charger_transport_source(cls, svc: Any) -> str:
        return cls._summary_text(_fresh_charger_transport_source(svc, time.time()), "na")

    @classmethod
    def _summary_charger_retry_reason(cls, svc: Any) -> str:
        return cls._summary_text(_fresh_charger_retry_reason(svc, time.time()), "na")

    @classmethod
    def _summary_charger_retry_source(cls, svc: Any) -> str:
        return cls._summary_text(_fresh_charger_retry_source(svc, time.time()), "na")

    @staticmethod
    def _summary_recovery_active(svc: Any) -> str:
        return "1" if str(getattr(svc, "_last_auto_state", "idle")) == "recovery" else "0"

    @classmethod
    def _scheduled_snapshot(cls, svc: Any, current_time: float) -> Any | None:
        if not mode_uses_scheduled_logic(getattr(svc, "virtual_mode", 0)):
            return None
        return scheduled_mode_snapshot(
            datetime.fromtimestamp(current_time),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "06:30"),
        )

    @classmethod
    def _summary_mode_parts(cls, svc: Any) -> tuple[str, ...]:
        return (
            f"mode={getattr(svc, 'virtual_mode', 'na')}",
            f"enable={getattr(svc, 'virtual_enable', 'na')}",
            f"startstop={getattr(svc, 'virtual_startstop', 'na')}",
            f"autostart={getattr(svc, 'virtual_autostart', 'na')}",
            f"cutover={cls._summary_flag(getattr(svc, '_auto_mode_cutover_pending', False))}",
            f"ignore_offtime={cls._summary_flag(getattr(svc, '_ignore_min_offtime_once', False))}",
        )

    @classmethod
    def _summary_phase_parts(cls, svc: Any) -> tuple[str, ...]:
        return (
            f"phase={getattr(svc, 'active_phase_selection', 'na')}",
            f"phase_req={getattr(svc, 'requested_phase_selection', 'na')}",
            f"phase_obs={cls._summary_observed_phase(svc)}",
            f"phase_switch={cls._summary_text(getattr(svc, '_phase_switch_state', 'na'), 'idle')}",
            f"phase_mismatch={cls._summary_phase_mismatch_active(svc)}",
            f"phase_lockout={cls._summary_phase_lockout_active(svc)}",
            f"phase_lockout_target={cls._summary_phase_lockout_target(svc)}",
            f"phase_effective={cls._summary_phase_supported_effective(svc)}",
            f"phase_degraded={cls._summary_phase_degraded_active(svc)}",
        )

    @classmethod
    def _summary_contactor_parts(cls, svc: Any) -> tuple[str, ...]:
        return (
            f"switch_feedback={cls._summary_switch_feedback_closed(svc)}",
            f"switch_interlock={cls._summary_switch_interlock_ok(svc)}",
            f"switch_feedback_mismatch={cls._summary_switch_feedback_mismatch(svc)}",
            f"contactor_fault_count={cls._summary_contactor_fault_count(svc)}",
            f"contactor_suspected_open={cls._summary_contactor_suspected_open(svc)}",
            f"contactor_suspected_welded={cls._summary_contactor_suspected_welded(svc)}",
            f"contactor_lockout={cls._summary_contactor_lockout_active(svc)}",
            f"contactor_lockout_reason={cls._summary_contactor_lockout_reason(svc)}",
        )

    @classmethod
    def _summary_backend_parts(cls, svc: Any, current_time: float) -> tuple[str, ...]:
        return (
            f"backend={backend_mode_for_service(svc, 'combined')}",
            f"meter_backend={backend_type_for_service(svc, 'meter', 'shelly_meter')}",
            f"switch_backend={backend_type_for_service(svc, 'switch', 'shelly_contactor_switch')}",
            f"charger_backend={cls._summary_text(backend_type_for_service(svc, 'charger', ''), 'na')}",
            f"charger_target={cls._summary_float(getattr(svc, '_charger_target_current_amps', None))}",
            f"charger_status={cls._summary_text(getattr(svc, '_last_charger_state_status', ''), 'na')}",
            f"charger_fault={cls._summary_text(getattr(svc, '_last_charger_state_fault', ''), 'na')}",
            f"charger_transport={cls._summary_charger_transport_reason(svc)}",
            f"charger_transport_source={cls._summary_charger_transport_source(svc)}",
            f"charger_retry={cls._summary_charger_retry_reason(svc)}",
            f"charger_retry_source={cls._summary_charger_retry_source(svc)}",
            f"charger_retry_remaining={_charger_retry_remaining_seconds(svc, current_time)}",
        )

    @classmethod
    def _summary_status_parts(cls, svc: Any) -> tuple[str, ...]:
        return (
            f"status_source={cls._summary_text(getattr(svc, '_last_status_source', ''), 'unknown')}",
            f"fault={cls._summary_fault_active(svc)}",
            f"fault_reason={cls._summary_fault_reason(svc)}",
            f"auto_state={getattr(svc, '_last_auto_state', 'na')}",
        )

    @classmethod
    def _summary_scheduled_snapshot_values(cls, scheduled_snapshot: Any | None) -> tuple[str, str, str, str, str]:
        if scheduled_snapshot is None:
            return ("na", "na", "na", "0", "na")
        return (
            cls._summary_text(scheduled_snapshot.state, "na"),
            cls._summary_text(scheduled_snapshot.reason, "na"),
            cls._summary_text(scheduled_snapshot.target_day_label, "na"),
            cls._summary_flag(scheduled_snapshot.night_boost_active),
            cls._summary_text(scheduled_snapshot.boost_until_text, "na"),
        )

    @classmethod
    def _summary_scheduled_parts(cls, scheduled_snapshot: Any | None) -> tuple[str, ...]:
        scheduled_state, scheduled_reason, target_day, boost_active, boost_until = cls._summary_scheduled_snapshot_values(
            scheduled_snapshot
        )
        return (
            f"scheduled_state={scheduled_state}",
            f"scheduled_reason={scheduled_reason}",
            f"scheduled_target_day={target_day}",
            f"scheduled_boost={boost_active}",
            f"scheduled_boost_until={boost_until}",
        )

    @classmethod
    def _summary_tail_parts(cls, svc: Any) -> tuple[str, ...]:
        return (
            f"recovery={cls._summary_recovery_active(svc)}",
            f"health={getattr(svc, '_last_health_reason', 'na')}",
        )

    @classmethod
    def _summary_parts(cls, svc: Any, scheduled_snapshot: Any | None, current_time: float) -> tuple[str, ...]:
        return (
            cls._summary_mode_parts(svc)
            + cls._summary_phase_parts(svc)
            + cls._summary_contactor_parts(svc)
            + cls._summary_backend_parts(svc, current_time)
            + cls._summary_status_parts(svc)
            + cls._summary_scheduled_parts(scheduled_snapshot)
            + cls._summary_tail_parts(svc)
        )

    def state_summary(self) -> str:
        svc = self.service
        current_time = time.time()
        scheduled_snapshot = self._scheduled_snapshot(svc, current_time)
        return " ".join(self._summary_parts(svc, scheduled_snapshot, current_time))
