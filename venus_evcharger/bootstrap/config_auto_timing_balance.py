# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode charger-side discharge-balance config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

BALANCE_POLICY_ENABLED_KEY = "AutoBatteryDischargeBalancePolicyEnabled"
BALANCE_WARN_ERROR_KEY = "AutoBatteryDischargeBalanceWarnErrorWatts"
BALANCE_BIAS_START_ERROR_KEY = "AutoBatteryDischargeBalanceBiasStartErrorWatts"
BALANCE_BIAS_MAX_PENALTY_KEY = "AutoBatteryDischargeBalanceBiasMaxPenaltyWatts"
BALANCE_BIAS_MODE_KEY = "AutoBatteryDischargeBalanceBiasMode"
BALANCE_BIAS_RESERVE_MARGIN_KEY = "AutoBatteryDischargeBalanceBiasReserveMarginSoc"
BALANCE_COORDINATION_ENABLED_KEY = "AutoBatteryDischargeBalanceCoordinationEnabled"
BALANCE_COORDINATION_SUPPORT_MODE_KEY = "AutoBatteryDischargeBalanceCoordinationSupportMode"
BALANCE_COORDINATION_START_ERROR_KEY = "AutoBatteryDischargeBalanceCoordinationStartErrorWatts"
BALANCE_COORDINATION_MAX_PENALTY_KEY = "AutoBatteryDischargeBalanceCoordinationMaxPenaltyWatts"
DEFAULT_BALANCE_BIAS_MODE = "always"
DEFAULT_COORDINATION_SUPPORT_MODE = "supported_only"
DEFAULT_FALSE = "0"


def load_discharge_balance_policy(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load charger-side battery discharge balance policy settings."""
    svc.auto_battery_discharge_balance_policy_enabled = defaults.get(
        BALANCE_POLICY_ENABLED_KEY,
        DEFAULT_FALSE,
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_discharge_balance_warn_error_watts = float(
        _config_value(defaults, BALANCE_WARN_ERROR_KEY, 500.0)
    )
    svc.auto_battery_discharge_balance_bias_start_error_watts = float(
        _config_value(defaults, BALANCE_BIAS_START_ERROR_KEY, 750.0)
    )
    svc.auto_battery_discharge_balance_bias_max_penalty_watts = float(
        _config_value(defaults, BALANCE_BIAS_MAX_PENALTY_KEY, 300.0)
    )
    svc.auto_battery_discharge_balance_bias_mode = str(
        _config_value(defaults, BALANCE_BIAS_MODE_KEY, DEFAULT_BALANCE_BIAS_MODE)
    ).strip().lower()
    svc.auto_battery_discharge_balance_bias_reserve_margin_soc = float(
        _config_value(defaults, BALANCE_BIAS_RESERVE_MARGIN_KEY, 5.0)
    )
    svc.auto_battery_discharge_balance_coordination_enabled = defaults.get(
        BALANCE_COORDINATION_ENABLED_KEY,
        DEFAULT_FALSE,
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_discharge_balance_coordination_support_mode = str(
        _config_value(defaults, BALANCE_COORDINATION_SUPPORT_MODE_KEY, DEFAULT_COORDINATION_SUPPORT_MODE)
    ).strip().lower()
    svc.auto_battery_discharge_balance_coordination_start_error_watts = float(
        _config_value(defaults, BALANCE_COORDINATION_START_ERROR_KEY, 1000.0)
    )
    svc.auto_battery_discharge_balance_coordination_max_penalty_watts = float(
        _config_value(defaults, BALANCE_COORDINATION_MAX_PENALTY_KEY, 200.0)
    )
