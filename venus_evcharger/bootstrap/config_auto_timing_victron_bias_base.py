# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode Victron grid-bias endpoint and mode config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

BIAS_ENABLED_KEY = "AutoBatteryDischargeBalanceVictronBiasEnabled"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_SOURCE_ID_KEY = "AutoBatteryDischargeBalanceVictronBiasSourceId"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_SERVICE_KEY = "AutoBatteryDischargeBalanceVictronBiasService"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_PATH_KEY = "AutoBatteryDischargeBalanceVictronBiasPath"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_BASE_SETPOINT_KEY = "AutoBatteryDischargeBalanceVictronBiasBaseSetpointWatts"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_DEADBAND_KEY = "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_ACTIVATION_MODE_KEY = "AutoBatteryDischargeBalanceVictronBiasActivationMode"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_SUPPORT_MODE_KEY = "AutoBatteryDischargeBalanceVictronBiasSupportMode"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
DEFAULT_FALSE = "0"  # pragma: no mutate - any non-true token normalizes to False.
DEFAULT_ACTIVATION_MODE = "always"  # pragma: no mutate - normalized with lower() before use.
DEFAULT_SUPPORT_MODE = "allow_experimental"  # pragma: no mutate - normalized with lower() before use.


def load_victron_bias_base_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load Victron grid-bias enable flag, endpoint, and mode settings."""
    svc.auto_battery_discharge_balance_victron_bias_enabled = defaults.get(
        BIAS_ENABLED_KEY,
        DEFAULT_FALSE,
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_discharge_balance_victron_bias_source_id = str(
        _config_value(defaults, BIAS_SOURCE_ID_KEY, "")
    ).strip()
    svc.auto_battery_discharge_balance_victron_bias_service = str(
        _config_value(defaults, BIAS_SERVICE_KEY, "com.victronenergy.settings")
    ).strip()
    svc.auto_battery_discharge_balance_victron_bias_path = str(
        _config_value(defaults, BIAS_PATH_KEY, "/Settings/CGwacs/AcPowerSetPoint")
    ).strip()
    svc.auto_battery_discharge_balance_victron_bias_base_setpoint_watts = float(
        _config_value(defaults, BIAS_BASE_SETPOINT_KEY, 50.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_deadband_watts = float(
        _config_value(defaults, BIAS_DEADBAND_KEY, 100.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_activation_mode = str(
        _config_value(defaults, BIAS_ACTIVATION_MODE_KEY, DEFAULT_ACTIVATION_MODE)
    ).strip().lower()
    svc.auto_battery_discharge_balance_victron_bias_support_mode = str(
        _config_value(defaults, BIAS_SUPPORT_MODE_KEY, DEFAULT_SUPPORT_MODE)
    ).strip().lower()
