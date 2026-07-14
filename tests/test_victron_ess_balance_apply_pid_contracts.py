# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact mathematical contracts for Victron ESS balance PID control."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.update.victron_ess_balance_apply_pid import (
    _UpdateCycleVictronEssBalanceApplyPid,
)


class _PidHarness(_UpdateCycleVictronEssBalanceApplyPid):
    @staticmethod
    def _optional_float(value: object) -> float | None:
        return float(value) if isinstance(value, (str, int, float)) else None


class VictronEssBalanceApplyPidContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pid = _PidHarness()

    def test_gain_and_limit_config_preserve_names_defaults_and_bounds(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp=1.25,
            auto_battery_discharge_balance_victron_bias_ki=0.5,
            auto_battery_discharge_balance_victron_bias_kd=0.125,
            auto_battery_discharge_balance_victron_bias_deadband_watts=40,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=500,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=900,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=25,
        )
        self.assertEqual(self.pid._victron_ess_balance_pid_gain_config(svc), {"kp": 1.25, "ki": 0.5, "kd": 0.125})
        self.assertEqual(
            self.pid._victron_ess_balance_pid_limit_config(svc),
            {"deadband_w": 40.0, "integral_limit_w": 500.0, "max_abs_w": 900.0, "ramp_rate_w_per_second": 25.0},
        )
        self.assertEqual(self.pid._victron_ess_balance_pid_gain_config(SimpleNamespace()), {"kp": 0.0, "ki": 0.0, "kd": 0.0})
        self.assertEqual(
            self.pid._victron_ess_balance_pid_limit_config(SimpleNamespace()),
            {"deadband_w": 0.0, "integral_limit_w": 0.0, "max_abs_w": 0.0, "ramp_rate_w_per_second": 0.0},
        )
        negative = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=-1,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=-2,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=-3,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=-4,
        )
        self.assertEqual(
            self.pid._victron_ess_balance_pid_limit_config(negative),
            {"deadband_w": 0.0, "integral_limit_w": 0.0, "max_abs_w": 0.0, "ramp_rate_w_per_second": 0.0},
        )

    def test_combined_config_delegates_and_merges_exactly(self) -> None:
        svc = object()
        with (
            patch.object(_PidHarness, "_victron_ess_balance_pid_gain_config", return_value={"kp": 1.0}) as gains,
            patch.object(_PidHarness, "_victron_ess_balance_pid_limit_config", return_value={"deadband_w": 2.0}) as limits,
        ):
            self.assertEqual(self.pid._victron_ess_balance_pid_config(svc), {"kp": 1.0, "deadband_w": 2.0})
        gains.assert_called_once_with(svc)
        limits.assert_called_once_with(svc)

    def test_effective_error_and_timing_cover_boundaries(self) -> None:
        self.assertEqual(self.pid._victron_ess_balance_effective_error(9.9, 10.0), 0.0)
        self.assertEqual(self.pid._victron_ess_balance_effective_error(-9.9, 10.0), 0.0)
        self.assertEqual(self.pid._victron_ess_balance_effective_error(10.0, 10.0), 10.0)
        self.assertEqual(self.pid._victron_ess_balance_effective_error(-10.0, 10.0), -10.0)
        self.assertEqual(self.pid._victron_ess_balance_pid_timing(SimpleNamespace(), 10.0), (0.0, 0.0))
        self.assertEqual(
            self.pid._victron_ess_balance_pid_timing(
                SimpleNamespace(_victron_ess_balance_pid_last_at=8.0, _victron_ess_balance_pid_last_error_w=-3.0), 10.0
            ),
            (2.0, -3.0),
        )
        self.assertEqual(
            self.pid._victron_ess_balance_pid_timing(SimpleNamespace(_victron_ess_balance_pid_last_at=12.0), 10.0),
            (0.0, 0.0),
        )

    def test_integral_derivative_and_target_math(self) -> None:
        integral = self.pid._victron_ess_balance_pid_integral_output
        self.assertEqual(integral(10.0, 4.0, 2.0, 0.5, 100.0), 14.0)
        self.assertEqual(integral(10.0, 4.0, 0.0, 0.5, 100.0), 10.0)
        self.assertEqual(integral(10.0, 4.0, -1.0, 0.5, 100.0), 10.0)
        self.assertEqual(integral(10.0, 4.0, 0.5, 0.5, 100.0), 11.0)
        self.assertEqual(integral(10.0, 4.0, 2.0, 0.0, 100.0), 10.0)
        self.assertEqual(integral(10.0, 4.0, 2.0, -0.5, 100.0), 10.0)
        self.assertEqual(integral(90.0, 40.0, 2.0, 1.0, 100.0), 100.0)
        self.assertEqual(integral(-90.0, -40.0, 2.0, 1.0, 100.0), -100.0)
        self.assertEqual(integral(90.0, 40.0, 2.0, 1.0, 0.0), 170.0)
        self.assertEqual(integral(0.4, 0.4, 1.0, 1.0, 0.5), 0.5)
        derivative = self.pid._victron_ess_balance_pid_derivative_w
        self.assertEqual(derivative(20.0, 8.0, 4.0), 3.0)
        self.assertEqual(derivative(20.0, 8.0, 0.0), 0.0)
        self.assertEqual(derivative(20.0, 8.0, -1.0), 0.0)
        self.assertEqual(derivative(2.0, 1.0, 0.5), 2.0)
        self.assertEqual(self.pid._victron_ess_balance_pid_target_output_w(20.0, 2.0, 3.0, 4.0, 5.0), 63.0)

    def test_clamp_and_ramp_cover_disabled_positive_and_negative_limits(self) -> None:
        clamp = self.pid._victron_ess_balance_pid_clamped_output_w
        self.assertEqual(clamp(120.0, 0.0), 120.0)
        self.assertEqual(clamp(120.0, -1.0), 120.0)
        self.assertEqual(clamp(1.0, 0.5), 0.5)
        self.assertEqual(clamp(120.0, 100.0), 100.0)
        self.assertEqual(clamp(-120.0, 100.0), -100.0)
        self.assertEqual(clamp(80.0, 100.0), 80.0)
        ramp = self.pid._victron_ess_balance_pid_ramped_output_w
        self.assertEqual(ramp(10.0, 100.0, 2.0, 0.0), 100.0)
        self.assertEqual(ramp(10.0, 100.0, 0.0, 5.0), 100.0)
        self.assertEqual(ramp(0.0, 10.0, 2.0, 0.5), 1.0)
        self.assertEqual(ramp(0.0, 10.0, 0.5, 2.0), 1.0)
        self.assertEqual(ramp(10.0, 100.0, 2.0, 5.0), 20.0)
        self.assertEqual(ramp(10.0, -100.0, 2.0, 5.0), 0.0)
        self.assertEqual(ramp(10.0, 15.0, 2.0, 5.0), 15.0)

    def test_store_state_writes_exact_runtime_fields(self) -> None:
        svc = SimpleNamespace()
        self.assertIsNone(self.pid._victron_ess_balance_pid_store_state(svc, -2.0, 3.0, 4.0))
        self.assertEqual(
            vars(svc),
            {
                "_victron_ess_balance_pid_last_error_w": -2.0,
                "_victron_ess_balance_pid_last_at": 3.0,
                "_victron_ess_balance_pid_integral_output_w": 4.0,
            },
        )

    def test_pid_output_orchestrates_active_error_path(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_pid_integral_output_w=2.0, _victron_ess_balance_pid_last_output_w=3.0)
        config = {"deadband_w": 4.0, "ki": 0.2, "integral_limit_w": 30.0, "kp": 0.5, "kd": 0.1, "max_abs_w": 50.0, "ramp_rate_w_per_second": 6.0}
        with (
            patch.object(self.pid, "_victron_ess_balance_pid_config", return_value=config) as config_for,
            patch.object(self.pid, "_victron_ess_balance_effective_error", return_value=8.0) as effective,
            patch.object(self.pid, "_victron_ess_balance_pid_timing", return_value=(2.0, 1.0)) as timing,
            patch.object(self.pid, "_victron_ess_balance_pid_integral_output", return_value=5.0) as integral,
            patch.object(self.pid, "_victron_ess_balance_pid_derivative_w", return_value=3.5) as derivative,
            patch.object(self.pid, "_victron_ess_balance_pid_target_output_w", return_value=9.0) as target,
            patch.object(self.pid, "_victron_ess_balance_pid_clamped_output_w", return_value=8.0) as clamp,
            patch.object(self.pid, "_victron_ess_balance_pid_ramped_output_w", return_value=7.0) as ramp,
            patch.object(self.pid, "_victron_ess_balance_pid_store_state") as store,
        ):
            self.assertEqual(self.pid._victron_ess_balance_pid_output(svc, 12.0, 20.0), 7.0)
        config_for.assert_called_once_with(svc)
        effective.assert_called_once_with(12.0, 4.0)
        timing.assert_called_once_with(svc, 20.0)
        integral.assert_called_once_with(2.0, 8.0, 2.0, 0.2, 30.0)
        derivative.assert_called_once_with(8.0, 1.0, 2.0)
        target.assert_called_once_with(8.0, 0.5, 5.0, 0.1, 3.5)
        clamp.assert_called_once_with(9.0, 50.0)
        ramp.assert_called_once_with(3.0, 8.0, 2.0, 6.0)
        store.assert_called_once_with(svc, 8.0, 20.0, 5.0)

    def test_pid_output_resets_integral_inside_deadband(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_pid_integral_output_w=9.0, _victron_ess_balance_pid_last_output_w=-2.0)
        config = {"deadband_w": 5.0, "ki": 1.0, "integral_limit_w": 10.0, "kp": 1.0, "kd": 1.0, "max_abs_w": 20.0, "ramp_rate_w_per_second": 4.0}
        with (
            patch.object(self.pid, "_victron_ess_balance_pid_config", return_value=config),
            patch.object(self.pid, "_victron_ess_balance_effective_error", return_value=0.0),
            patch.object(self.pid, "_victron_ess_balance_pid_timing", return_value=(3.0, 4.0)),
            patch.object(self.pid, "_victron_ess_balance_pid_integral_output") as integral,
            patch.object(self.pid, "_victron_ess_balance_pid_derivative_w") as derivative,
            patch.object(self.pid, "_victron_ess_balance_pid_target_output_w") as target,
            patch.object(self.pid, "_victron_ess_balance_pid_clamped_output_w", return_value=0.0) as clamp,
            patch.object(self.pid, "_victron_ess_balance_pid_ramped_output_w", return_value=-1.0) as ramp,
            patch.object(self.pid, "_victron_ess_balance_pid_store_state") as store,
        ):
            self.assertEqual(self.pid._victron_ess_balance_pid_output(svc, 1.0, 30.0), -1.0)
        integral.assert_not_called()
        derivative.assert_not_called()
        target.assert_not_called()
        clamp.assert_called_once_with(0.0, 20.0)
        ramp.assert_called_once_with(-2.0, 0.0, 3.0, 4.0)
        store.assert_called_once_with(svc, 0.0, 30.0, 0.0)

    def test_pid_output_uses_zero_state_when_runtime_fields_are_absent(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp=1.0,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=0.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=0.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=2.0,
            _victron_ess_balance_pid_last_at=0.0,
        )
        self.assertEqual(self.pid._victron_ess_balance_pid_output(svc, 10.0, 1.0), 2.0)
        self.assertEqual(svc._victron_ess_balance_pid_last_error_w, 10.0)
        self.assertEqual(svc._victron_ess_balance_pid_last_at, 1.0)
        self.assertEqual(svc._victron_ess_balance_pid_integral_output_w, 0.0)


if __name__ == "__main__":
    unittest.main()
