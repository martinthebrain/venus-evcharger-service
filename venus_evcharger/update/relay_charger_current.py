# SPDX-License-Identifier: GPL-3.0-or-later
"""Native charger-current and enable-target helpers for the update cycle."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.core.common import local_datetime_from_timestamp, mode_uses_scheduled_logic, scheduled_mode_snapshot
from venus_evcharger.core.contracts import finite_float_or_none, normalize_learning_phase, normalize_learning_state
from venus_evcharger.update.relay_charger_transport import _RelayChargerTransport

if TYPE_CHECKING:
    from venus_evcharger.update.relay_charger_readback import ChargerCurrentBackend


CHARGER_CURRENT_APPLY_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _RelayChargerCurrent(_RelayChargerTransport):
    """Derive and apply charger current targets from learned and scheduled policy."""

    if TYPE_CHECKING:  # pragma: no cover

        @staticmethod
        def _charger_current_backend(svc: Any) -> ChargerCurrentBackend | None: ...

        @staticmethod
        def _charger_enable_backend(svc: Any) -> Any | None: ...

        @classmethod
        def _charger_retry_active(cls, svc: Any, now: float | None = None) -> bool: ...

        @classmethod
        def _clear_charger_transport_issue(cls, svc: Any) -> None: ...

        @classmethod
        def _clear_charger_retry(cls, svc: Any) -> None: ...

        @classmethod
        def _remember_charger_transport_issue(
            cls,
            svc: Any,
            reason: str,
            source: str,
            error: BaseException,
            now: float | None = None,
        ) -> None: ...

        @classmethod
        def _remember_charger_retry(
            cls,
            svc: Any,
            reason: str,
            source: str,
            now: float | None = None,
        ) -> None: ...

    @classmethod
    def _contactor_heuristic_delay_seconds(cls, svc: Any) -> float:
        return max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 0.0)))

    @classmethod
    def _contactor_lockout_threshold(cls, svc: Any) -> int:
        return max(0, int(getattr(svc, "auto_contactor_fault_latch_count", 3)))

    @classmethod
    def _contactor_lockout_persistence_seconds(cls, svc: Any) -> float:
        return max(0.0, float(getattr(svc, "auto_contactor_fault_latch_seconds", 60.0)))

    @classmethod
    def _contactor_power_threshold_w(cls, svc: Any) -> float:
        configured = finite_float_or_none(getattr(svc, "charging_threshold_watts", None))
        return max(100.0, 0.0 if configured is None else float(configured))

    @classmethod
    def _contactor_current_threshold_a(cls, svc: Any) -> float:
        configured = finite_float_or_none(getattr(svc, "min_current", None))
        if configured is None:
            return 1.0
        return max(1.0, float(configured) / 4.0)

    @classmethod
    def _clamped_charger_current_target(cls, svc: Any, value: float | None) -> float | None:
        if value is None:
            return None
        target = float(value)
        min_current, max_current = cls._charger_current_limits(svc)
        target = cls._apply_min_current_limit(target, min_current)
        target = cls._apply_max_current_limit(target, max_current)
        return target if target > 0.0 else None

    @staticmethod
    def _charger_current_limits(svc: Any) -> tuple[float | None, float | None]:
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        max_current = finite_float_or_none(getattr(svc, "max_current", None))
        return min_current, max_current

    @staticmethod
    def _apply_min_current_limit(target: float, min_current: float | None) -> float:
        return max(target, min_current) if min_current is not None else float(target)

    @staticmethod
    def _apply_max_current_limit(target: float, max_current: float | None) -> float:
        if max_current is None or max_current <= 0.0:
            return float(target)
        return min(target, max_current)

    @classmethod
    def _stable_learned_current_inputs(
        cls,
        svc: Any,
    ) -> tuple[float, float, str, float, float | None] | None:
        if not cls._stable_learned_current_state(svc):
            return None
        return cls._validated_stable_learned_current_inputs(cls._raw_stable_learned_current_inputs(svc))

    @staticmethod
    def _stable_learned_current_state(svc: Any) -> bool:
        return normalize_learning_state(getattr(svc, "learned_charge_power_state", "unknown")) == "stable"

    @staticmethod
    def _raw_stable_learned_current_inputs(
        svc: Any,
    ) -> tuple[float | None, float | None, str | None, float | None, float | None]:
        learned_power = finite_float_or_none(getattr(svc, "learned_charge_power_watts", None))
        learned_voltage = finite_float_or_none(getattr(svc, "learned_charge_power_voltage", None))
        learned_phase = normalize_learning_phase(getattr(svc, "learned_charge_power_phase", getattr(svc, "phase", "L1")))
        updated_at = finite_float_or_none(getattr(svc, "learned_charge_power_updated_at", None))
        max_age_seconds = finite_float_or_none(getattr(svc, "auto_learn_charge_power_max_age_seconds", 21600.0))
        return learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds

    @classmethod
    def _validated_stable_learned_current_inputs(
        cls,
        learned_inputs: tuple[float | None, float | None, str | None, float | None, float | None],
    ) -> tuple[float, float, str, float, float | None] | None:
        learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds = learned_inputs
        resolved_power = cls._positive_learned_scalar(learned_power)
        resolved_voltage = cls._positive_learned_scalar(learned_voltage)
        if resolved_power is None or resolved_voltage is None:
            return None
        phase_and_timestamp = cls._learned_phase_and_timestamp(learned_phase, updated_at)
        if phase_and_timestamp is None:
            return None
        resolved_phase, resolved_updated_at = phase_and_timestamp
        return resolved_power, resolved_voltage, resolved_phase, resolved_updated_at, max_age_seconds

    @staticmethod
    def _positive_learned_scalar(value: float | None) -> float | None:
        if value is None or value <= 0.0:
            return None
        return float(value)

    @staticmethod
    def _learned_phase_and_timestamp(learned_phase: str | None, updated_at: float | None) -> tuple[str, float] | None:
        if learned_phase is None or updated_at is None:
            return None
        return str(learned_phase), float(updated_at)

    @staticmethod
    def _learned_current_target_stale(now: float, updated_at: float, max_age_seconds: float | None) -> bool:
        return bool(max_age_seconds is not None and max_age_seconds > 0.0 and (float(now) - updated_at) > max_age_seconds)

    @staticmethod
    def _learned_phase_voltage(svc: Any, learned_phase: str, learned_voltage: float) -> float:
        if learned_phase != "3P" or str(getattr(svc, "voltage_mode", "phase")).strip().lower() == "phase":
            return float(learned_voltage)
        return float(learned_voltage) / math.sqrt(3.0) if learned_voltage > 0.0 else 0.0

    @staticmethod
    def _rounded_learned_current_target(
        learned_power: float,
        phase_voltage: float,
        phase_count: float,
    ) -> float | None:
        if phase_voltage <= 0.0 or phase_count <= 0.0:
            return None
        return finite_float_or_none(round(float(learned_power) / (phase_voltage * phase_count)))

    @staticmethod
    def _scheduled_night_charge_active(svc: Any, now: float) -> bool:
        if not mode_uses_scheduled_logic(getattr(svc, "virtual_mode", 0)):
            return False
        return scheduled_mode_snapshot(
            local_datetime_from_timestamp(float(now), getattr(svc, "auto_schedule_timezone", "UTC")),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri"),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "04:30"),
        ).night_boost_active

    @staticmethod
    def _scheduled_night_current_amps(svc: Any) -> float | None:
        configured = finite_float_or_none(getattr(svc, "auto_scheduled_night_current_amps", None))
        if configured is not None and configured > 0.0:
            return configured
        return finite_float_or_none(getattr(svc, "max_current", None))

    @classmethod
    def _derived_learned_current_target(cls, svc: Any, now: float) -> float | None:
        learned_inputs = cls._stable_learned_current_inputs(svc)
        if learned_inputs is None:
            return None
        learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds = learned_inputs
        if cls._learned_current_target_stale(now, updated_at, max_age_seconds):
            return None
        phase_voltage = cls._learned_phase_voltage(svc, learned_phase, learned_voltage)
        phase_count = 3.0 if learned_phase == "3P" else 1.0
        rounded_current = cls._rounded_learned_current_target(learned_power, phase_voltage, phase_count)
        return cls._clamped_charger_current_target(svc, rounded_current)

    @classmethod
    def _charger_current_target_amps(
        cls,
        svc: Any,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        if not cls._charger_current_target_allowed(svc, desired_relay, auto_mode_active):
            return None
        if cls._scheduled_night_charge_active(svc, now):
            return cls._clamped_charger_current_target(svc, cls._scheduled_night_current_amps(svc))
        learned_target = cls._derived_learned_current_target(svc, now)
        if learned_target is not None:
            return learned_target
        fallback_target = finite_float_or_none(getattr(svc, "virtual_set_current", None))
        return cls._clamped_charger_current_target(svc, fallback_target)

    @classmethod
    def _charger_current_target_allowed(cls, svc: Any, desired_relay: bool, auto_mode_active: bool) -> bool:
        if not auto_mode_active or not bool(desired_relay):
            return False
        return cls._charger_current_backend(svc) is not None

    @classmethod
    def apply_charger_current_target(
        cls,
        svc: Any,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        backend = cls._charger_current_backend(svc)
        if backend is None:
            return None
        if cls._charger_current_reset_needed(svc, desired_relay, auto_mode_active):
            cls._reset_charger_current_target(svc)
            return None

        target_amps = cls._charger_current_target_amps(svc, desired_relay, now, auto_mode_active)
        if target_amps is None:
            return None

        last_target = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        if cls._charger_target_unchanged(last_target, target_amps):
            return cls._known_charger_current_target(last_target)
        return cls._apply_new_charger_current_target(svc, backend, target_amps, now, last_target)

    @classmethod
    def _apply_new_charger_current_target(
        cls,
        svc: Any,
        backend: ChargerCurrentBackend,
        target_amps: float,
        now: float,
        last_target: float | None,
    ) -> float | None:
        if cls._charger_retry_active(svc, now):
            return last_target
        try:
            backend.set_current(float(target_amps))
        except CHARGER_CURRENT_APPLY_ERRORS as error:
            cls._handle_charger_current_target_failure(svc, error, now)
            return last_target
        cls._clear_charger_transport_issue(svc)
        cls._clear_charger_retry(svc)
        return cls._remember_charger_current_target(svc, target_amps, now)

    @staticmethod
    def _charger_current_reset_needed(svc: Any, desired_relay: bool, auto_mode_active: bool) -> bool:
        return not auto_mode_active or not bool(desired_relay)

    @staticmethod
    def _reset_charger_current_target(svc: Any) -> None:
        svc._charger_target_current_amps = None
        svc._charger_target_current_applied_at = None

    @staticmethod
    def _charger_target_unchanged(last_target: float | None, target_amps: float) -> bool:
        return last_target is not None and abs(last_target - target_amps) < 0.01

    @classmethod
    def _handle_charger_current_target_failure(cls, svc: Any, error: Exception, now: float | None = None) -> None:
        transport_reason = modbus_transport_issue_reason(error)
        if transport_reason is not None:
            cls._remember_charger_transport_issue(svc, transport_reason, "current", error, now)
            cls._remember_charger_retry(svc, transport_reason, "current", now)
        svc._mark_failure("charger")
        svc._warning_throttled(
            "charger-current-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Charger current request failed: %s",
            error,
            exc_info=error,
        )

    @staticmethod
    def _remember_charger_current_target(svc: Any, target_amps: float, now: float) -> float:
        svc._charger_target_current_amps = float(target_amps)
        svc._charger_target_current_applied_at = float(now)
        svc._mark_recovery("charger", "Charger current writes recovered")
        return float(target_amps)

    @classmethod
    def _apply_enabled_target(cls, svc: Any, enabled: bool, now: float) -> bool:
        backend = cls._charger_enable_backend(svc)
        if backend is not None:
            if cls._charger_retry_active(svc, now):
                return False
            backend.set_enabled(bool(enabled))
            cls._clear_charger_transport_issue(svc)
            cls._clear_charger_retry(svc)
            svc._mark_recovery("charger", "Charger enable writes recovered")
            return True
        svc._queue_relay_command(bool(enabled), now)
        return True

    @staticmethod
    def _known_charger_current_target(last_target: float | None) -> float:
        if last_target is None:
            raise TypeError("last charger current target must be available when unchanged")
        return float(last_target)
