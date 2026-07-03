# SPDX-License-Identifier: GPL-3.0-or-later
"""Charger-current target derivation helpers."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from venus_evcharger.core.common import local_datetime_from_timestamp, mode_uses_scheduled_logic, scheduled_mode_snapshot
from venus_evcharger.core.contracts import finite_float_or_none, normalize_learning_phase, normalize_learning_state
from venus_evcharger.update.relay_charger_transport import _RelayChargerTransport

if TYPE_CHECKING:
    from venus_evcharger.update.relay_charger_readback import ChargerCurrentBackend


class _RelayChargerCurrentTargets(_RelayChargerTransport):
    """Derive charger current targets from schedule, learned power, and fallback state."""

    if TYPE_CHECKING:  # pragma: no cover

        @staticmethod
        def _charger_current_backend(svc: Any) -> ChargerCurrentBackend | None: ...

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
        if not hasattr(svc, "learned_charge_power_state"):
            return False
        return normalize_learning_state(svc.learned_charge_power_state) == "stable"

    @staticmethod
    def _raw_stable_learned_current_inputs(
        svc: Any,
    ) -> tuple[float | None, float | None, str | None, float | None, float | None]:
        learned_power = finite_float_or_none(getattr(svc, "learned_charge_power_watts", None))
        learned_voltage = finite_float_or_none(getattr(svc, "learned_charge_power_voltage", None))
        raw_phase = getattr(svc, "learned_charge_power_phase", None)
        if raw_phase is None:
            raw_phase = getattr(svc, "phase", None)
        learned_phase = normalize_learning_phase(raw_phase) if raw_phase is not None else "L1"
        updated_at = finite_float_or_none(getattr(svc, "learned_charge_power_updated_at", None))
        max_age_seconds = finite_float_or_none(getattr(svc, "auto_learn_charge_power_max_age_seconds", None))
        if max_age_seconds is None:
            max_age_seconds = 21600.0
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
        voltage_mode = str(svc.voltage_mode).strip().lower() if hasattr(svc, "voltage_mode") else "phase"
        if learned_phase != "3P" or voltage_mode == "phase":
            return float(learned_voltage)
        return max(0.0, float(learned_voltage)) / math.sqrt(3.0)

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
        virtual_mode = getattr(svc, "virtual_mode", None)
        if virtual_mode is None or not mode_uses_scheduled_logic(virtual_mode):
            return False
        local_dt = local_datetime_from_timestamp(float(now), getattr(svc, "auto_schedule_timezone", "UTC"))
        month_windows = getattr(svc, "auto_month_windows", {})
        enabled_days = getattr(svc, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri")
        delay_seconds = float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0))
        latest_end_time = getattr(svc, "auto_scheduled_latest_end_time", "04:30")
        return scheduled_mode_snapshot(
            local_dt,
            month_windows,
            enabled_days,
            delay_seconds=delay_seconds,
            latest_end_time=latest_end_time,
        ).night_boost_active

    @staticmethod
    def _scheduled_night_current_amps(svc: Any) -> float | None:
        configured = (
            finite_float_or_none(svc.auto_scheduled_night_current_amps)
            if hasattr(svc, "auto_scheduled_night_current_amps")
            else None
        )
        if configured is not None and configured > 0.0:
            return configured
        if not hasattr(svc, "max_current"):
            return None
        return finite_float_or_none(svc.max_current)

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
