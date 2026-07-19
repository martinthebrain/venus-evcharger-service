# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure charger-current target policy."""

from __future__ import annotations

import math
from typing import ClassVar, TypeGuard

from venus_evcharger.core.common import local_datetime_from_timestamp, mode_uses_scheduled_logic, scheduled_mode_snapshot
from venus_evcharger.core.common_types import TimeWindow
from venus_evcharger.core.contracts import (
    finite_float_or_none,
    normalize_learning_phase,
    normalize_learning_state,
    timestamp_not_future,
)
from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.update.relay_charger_readback import ChargerBackendAccess


def _is_time_window(value: object) -> TypeGuard[TimeWindow]:
    if not isinstance(value, tuple):
        return False
    window = value
    if len(window) != 2:
        return False
    hour, minute = window
    return all(isinstance(part, int) and not isinstance(part, bool) for part in (hour, minute))


def _is_month_window_pair(value: object) -> TypeGuard[tuple[TimeWindow, TimeWindow]]:
    if not isinstance(value, tuple):
        return False
    windows = value
    if len(windows) != 2:
        return False
    return _is_time_window(windows[0]) and _is_time_window(windows[1])


def _is_month_window_item(month: object, windows: object) -> bool:
    return isinstance(month, int) and not isinstance(month, bool) and _is_month_window_pair(windows)


def _is_month_windows(value: object) -> TypeGuard[dict[int, tuple[TimeWindow, TimeWindow]]]:
    if not isinstance(value, dict):
        return False
    return all(_is_month_window_item(month, windows) for month, windows in value.items())


class ChargerCurrentTargetPolicy:
    """Derive charger current targets from schedule, learned power, and fallback state."""

    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS: ClassVar[float] = 1.0

    def __init__(self, backends: ChargerBackendAccess) -> None:
        self._backends = backends

    @classmethod
    def clamped_target(cls, svc: object, value: float | None) -> float | None:
        if value is None:
            return None
        target = float(value)
        min_current, max_current = cls.current_limits(svc)
        target = cls.apply_minimum(target, min_current)
        target = cls.apply_maximum(target, max_current)
        return target if target > 0.0 else None

    @staticmethod
    def current_limits(svc: object) -> tuple[float | None, float | None]:
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        max_current = finite_float_or_none(getattr(svc, "max_current", None))
        return min_current, max_current

    @staticmethod
    def apply_minimum(target: float, min_current: float | None) -> float:
        return max(target, min_current) if min_current is not None else float(target)

    @staticmethod
    def apply_maximum(target: float, max_current: float | None) -> float:
        if max_current is None or max_current <= 0.0:
            return float(target)
        return min(target, max_current)

    @classmethod
    def stable_learned_inputs(
        cls,
        svc: object,
    ) -> tuple[float, float, str, float, float | None] | None:
        if not cls.stable_learning_state(svc):
            return None
        return cls.validated_learned_inputs(cls.raw_learned_inputs(svc))

    @staticmethod
    def stable_learning_state(svc: object) -> bool:
        if not hasattr(svc, "learned_charge_power_state"):
            return False
        return normalize_learning_state(getattr(svc, "learned_charge_power_state")) == "stable"

    @staticmethod
    def raw_learned_inputs(
        svc: object,
    ) -> tuple[float | None, float | None, str | None, float | None, float | None]:
        learned_power = finite_float_or_none(getattr(svc, "learned_charge_power_watts", None))
        learned_voltage = finite_float_or_none(getattr(svc, "learned_charge_power_voltage", None))
        raw_phase = getattr(svc, "learned_charge_power_phase", None)
        if raw_phase is None:
            raw_phase = getattr(svc, "phase", None)
        learned_phase = normalize_learning_phase(raw_phase) if raw_phase is not None else "L1"
        updated_at = finite_float_or_none(getattr(svc, "learned_charge_power_updated_at", None))
        policy = getattr(svc, "auto_policy", AutoPolicy())
        if not isinstance(policy, AutoPolicy):
            raise TypeError("auto_policy must be AutoPolicy")
        max_age_seconds = finite_float_or_none(policy.learn_charge_power.max_age_seconds)
        return learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds

    @classmethod
    def validated_learned_inputs(
        cls,
        learned_inputs: tuple[float | None, float | None, str | None, float | None, float | None],
    ) -> tuple[float, float, str, float, float | None] | None:
        learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds = learned_inputs
        resolved_power = cls.positive_scalar(learned_power)
        resolved_voltage = cls.positive_scalar(learned_voltage)
        if resolved_power is None or resolved_voltage is None:
            return None
        phase_and_timestamp = cls.phase_and_timestamp(learned_phase, updated_at)
        if phase_and_timestamp is None:
            return None
        resolved_phase, resolved_updated_at = phase_and_timestamp
        return resolved_power, resolved_voltage, resolved_phase, resolved_updated_at, max_age_seconds

    @staticmethod
    def positive_scalar(value: float | None) -> float | None:
        if value is None or value <= 0.0:
            return None
        return float(value)

    @staticmethod
    def phase_and_timestamp(learned_phase: str | None, updated_at: float | None) -> tuple[str, float] | None:
        if learned_phase is None or updated_at is None:
            return None
        return str(learned_phase), float(updated_at)

    @classmethod
    def learned_target_stale(cls, now: float, updated_at: float, max_age_seconds: float | None) -> bool:
        if not timestamp_not_future(updated_at, now, cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS):
            return True
        return bool(max_age_seconds is not None and max_age_seconds > 0.0 and (float(now) - updated_at) > max_age_seconds)

    @staticmethod
    def learned_phase_voltage(svc: object, learned_phase: str, learned_voltage: float) -> float:
        raw_voltage_mode = getattr(svc, "voltage_mode", None)
        voltage_mode = "phase" if raw_voltage_mode is None else str(raw_voltage_mode).strip().lower()
        if learned_phase != "3P" or voltage_mode == "phase":
            return float(learned_voltage)
        return max(0.0, float(learned_voltage)) / math.sqrt(3.0)

    @staticmethod
    def rounded_learned_target(
        learned_power: float,
        phase_voltage: float,
        phase_count: float,
    ) -> float | None:
        if phase_voltage <= 0.0 or phase_count <= 0.0:
            return None
        return finite_float_or_none(round(float(learned_power) / (phase_voltage * phase_count)))

    @staticmethod
    def scheduled_night_active(svc: object, now: float) -> bool:
        virtual_mode = getattr(svc, "virtual_mode", None)
        if virtual_mode is None or not mode_uses_scheduled_logic(virtual_mode):
            return False
        local_dt = local_datetime_from_timestamp(float(now), getattr(svc, "auto_schedule_timezone", "UTC"))
        raw_month_windows = getattr(svc, "auto_month_windows")
        month_windows = raw_month_windows if _is_month_windows(raw_month_windows) else {}
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
    def scheduled_night_current(svc: object) -> float | None:
        configured = (
            finite_float_or_none(getattr(svc, "auto_scheduled_night_current_amps"))
            if hasattr(svc, "auto_scheduled_night_current_amps")
            else None
        )
        if configured is not None and configured > 0.0:
            return configured
        if not hasattr(svc, "max_current"):
            return None
        return finite_float_or_none(getattr(svc, "max_current"))

    @classmethod
    def derived_learned_target(cls, svc: object, now: float) -> float | None:
        learned_inputs = cls.stable_learned_inputs(svc)
        if learned_inputs is None:
            return None
        learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds = learned_inputs
        if cls.learned_target_stale(now, updated_at, max_age_seconds):
            return None
        phase_voltage = cls.learned_phase_voltage(svc, learned_phase, learned_voltage)
        phase_count = 3.0 if learned_phase == "3P" else 1.0
        rounded_current = cls.rounded_learned_target(learned_power, phase_voltage, phase_count)
        return cls.clamped_target(svc, rounded_current)

    def current_target(
        self,
        svc: object,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        if not self.target_allowed(svc, desired_relay, auto_mode_active):
            return None
        if self.scheduled_night_active(svc, now):
            return self.clamped_target(svc, self.scheduled_night_current(svc))
        learned_target = self.derived_learned_target(svc, now)
        if learned_target is not None:
            return learned_target
        fallback_target = finite_float_or_none(getattr(svc, "virtual_set_current", None))
        return self.clamped_target(svc, fallback_target)

    def target_allowed(self, svc: object, desired_relay: bool, auto_mode_active: bool) -> bool:
        if not auto_mode_active or not bool(desired_relay):
            return False
        return self._backends.current_backend(svc) is not None


__all__ = ["ChargerCurrentTargetPolicy"]
