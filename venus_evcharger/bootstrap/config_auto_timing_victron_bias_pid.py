# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode Victron grid-bias PID config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

BIAS_KP_KEY = "AutoBatteryDischargeBalanceVictronBiasKp"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_KI_KEY = "AutoBatteryDischargeBalanceVictronBiasKi"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_KD_KEY = "AutoBatteryDischargeBalanceVictronBiasKd"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_INTEGRAL_LIMIT_KEY = "AutoBatteryDischargeBalanceVictronBiasIntegralLimitWatts"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_MAX_ABS_KEY = "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_RAMP_RATE_KEY = "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_MIN_UPDATE_KEY = "AutoBatteryDischargeBalanceVictronBiasMinUpdateSeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.


def load_victron_bias_pid_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load Victron grid-bias PID and rate-limit settings."""
    svc.auto_battery_discharge_balance_victron_bias_kp = float(
        _config_value(defaults, BIAS_KP_KEY, 0.2)
    )
    svc.auto_battery_discharge_balance_victron_bias_ki = float(
        _config_value(defaults, BIAS_KI_KEY, 0.02)
    )
    svc.auto_battery_discharge_balance_victron_bias_kd = float(
        _config_value(defaults, BIAS_KD_KEY, 0.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_integral_limit_watts = float(
        _config_value(defaults, BIAS_INTEGRAL_LIMIT_KEY, 250.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_max_abs_watts = float(
        _config_value(defaults, BIAS_MAX_ABS_KEY, 500.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = float(
        _config_value(defaults, BIAS_RAMP_RATE_KEY, 50.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_min_update_seconds = float(
        _config_value(defaults, BIAS_MIN_UPDATE_KEY, 2.0)
    )
