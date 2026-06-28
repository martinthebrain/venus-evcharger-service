# SPDX-License-Identifier: GPL-3.0-or-later
"""PID helpers for Victron ESS balance-bias application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _UpdateCycleVictronEssBalanceApplyPidMixin:
    """Compute bounded and ramped Victron ESS balance-bias setpoint offsets."""

    if TYPE_CHECKING:  # pragma: no cover
        def _optional_float(self, value: Any) -> float | None: ...

    @staticmethod
    def _victron_ess_balance_pid_gain_config(svc: Any) -> dict[str, float]:
        return {
            "kp": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kp", 0.0) or 0.0),
            "ki": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_ki", 0.0) or 0.0),
            "kd": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kd", 0.0) or 0.0),
        }

    @staticmethod
    def _victron_ess_balance_pid_limit_config(svc: Any) -> dict[str, float]:
        return {
            "deadband_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
            ),
            "integral_limit_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_integral_limit_watts", 0.0) or 0.0),
            ),
            "max_abs_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0) or 0.0),
            ),
            "ramp_rate_w_per_second": max(
                0.0,
                float(
                    getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0) or 0.0
                ),
            ),
        }

    @classmethod
    def _victron_ess_balance_pid_config(cls, svc: Any) -> dict[str, float]:
        return {
            **cls._victron_ess_balance_pid_gain_config(svc),
            **cls._victron_ess_balance_pid_limit_config(svc),
        }

    @staticmethod
    def _victron_ess_balance_effective_error(raw_error_w: float, deadband_w: float) -> float:
        return 0.0 if abs(raw_error_w) < deadband_w else raw_error_w

    def _victron_ess_balance_pid_timing(self, svc: Any, now: float) -> tuple[float, float]:
        last_at = self._optional_float(getattr(svc, "_victron_ess_balance_pid_last_at", None))
        dt = 0.0 if last_at is None else max(0.0, float(now) - float(last_at))
        last_error_w = float(getattr(svc, "_victron_ess_balance_pid_last_error_w", 0.0) or 0.0)
        return dt, last_error_w

    @staticmethod
    def _victron_ess_balance_pid_integral_output(
        current_integral_output_w: float,
        effective_error_w: float,
        dt: float,
        ki: float,
        integral_limit_w: float,
    ) -> float:
        integral_output_w = float(current_integral_output_w)
        if dt > 0.0 and ki > 0.0:
            integral_output_w += ki * effective_error_w * dt
        if integral_limit_w > 0.0:
            integral_output_w = max(-integral_limit_w, min(integral_output_w, integral_limit_w))
        return integral_output_w

    @staticmethod
    def _victron_ess_balance_pid_derivative_w(effective_error_w: float, last_error_w: float, dt: float) -> float:
        return 0.0 if dt <= 0.0 else (effective_error_w - last_error_w) / dt

    @staticmethod
    def _victron_ess_balance_pid_target_output_w(
        effective_error_w: float,
        kp: float,
        integral_output_w: float,
        kd: float,
        derivative_w: float,
    ) -> float:
        return (kp * effective_error_w) + integral_output_w + (kd * derivative_w)

    @staticmethod
    def _victron_ess_balance_pid_clamped_output_w(target_output_w: float, max_abs_w: float) -> float:
        if max_abs_w <= 0.0:
            return float(target_output_w)
        return max(-max_abs_w, min(target_output_w, max_abs_w))

    @staticmethod
    def _victron_ess_balance_pid_ramped_output_w(
        last_output_w: float,
        target_output_w: float,
        dt: float,
        ramp_rate_w_per_second: float,
    ) -> float:
        if ramp_rate_w_per_second <= 0.0 or dt <= 0.0:
            return float(target_output_w)
        max_step_w = ramp_rate_w_per_second * dt
        delta_w = max(-max_step_w, min(target_output_w - last_output_w, max_step_w))
        return float(last_output_w + delta_w)

    @staticmethod
    def _victron_ess_balance_pid_store_state(
        svc: Any,
        effective_error_w: float,
        now: float,
        integral_output_w: float,
    ) -> None:
        svc._victron_ess_balance_pid_last_error_w = float(effective_error_w)
        svc._victron_ess_balance_pid_last_at = float(now)
        svc._victron_ess_balance_pid_integral_output_w = float(integral_output_w)

    def _victron_ess_balance_pid_output(self, svc: Any, error_w: float, now: float) -> float:
        config = self._victron_ess_balance_pid_config(svc)
        raw_error_w = float(error_w)
        effective_error_w = self._victron_ess_balance_effective_error(raw_error_w, config["deadband_w"])
        dt, last_error_w = self._victron_ess_balance_pid_timing(svc, now)
        integral_output_w = float(getattr(svc, "_victron_ess_balance_pid_integral_output_w", 0.0) or 0.0)
        if effective_error_w == 0.0:
            integral_output_w = 0.0
            target_output_w = 0.0
        else:
            integral_output_w = self._victron_ess_balance_pid_integral_output(
                integral_output_w,
                effective_error_w,
                dt,
                config["ki"],
                config["integral_limit_w"],
            )
            derivative_w = self._victron_ess_balance_pid_derivative_w(effective_error_w, last_error_w, dt)
            target_output_w = self._victron_ess_balance_pid_target_output_w(
                effective_error_w,
                config["kp"],
                integral_output_w,
                config["kd"],
                derivative_w,
            )
        target_output_w = self._victron_ess_balance_pid_clamped_output_w(target_output_w, config["max_abs_w"])
        last_output_w = float(getattr(svc, "_victron_ess_balance_pid_last_output_w", 0.0) or 0.0)
        output_w = self._victron_ess_balance_pid_ramped_output_w(
            last_output_w,
            target_output_w,
            dt,
            config["ramp_rate_w_per_second"],
        )
        self._victron_ess_balance_pid_store_state(svc, effective_error_w, now, integral_output_w)
        return float(output_w)
