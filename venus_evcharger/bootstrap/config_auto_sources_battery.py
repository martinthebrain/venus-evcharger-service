# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode battery source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_BATTERY_SERVICE_KEY = "AutoBatteryService"
AUTO_BATTERY_SOC_PATH_KEY = "AutoBatterySocPath"
AUTO_BATTERY_SERVICE_PREFIX_KEY = "AutoBatteryServicePrefix"
AUTO_BATTERY_SCAN_INTERVAL_KEY = "AutoBatteryScanIntervalSeconds"
AUTO_BATTERY_CAPACITY_WH_KEY = "AutoBatteryCapacityWh"
AUTO_BATTERY_CHEMISTRY_KEY = "AutoBatteryChemistry"
AUTO_BATTERY_CAPACITY_AUTO_ESTIMATE_KEY = "AutoBatteryCapacityAutoEstimate"
AUTO_BATTERY_CAPACITY_WH_PATH_KEY = "AutoBatteryCapacityWhPath"
AUTO_BATTERY_CAPACITY_AH_PATH_KEY = "AutoBatteryCapacityAhPath"
AUTO_BATTERY_VOLTAGE_PATH_KEY = "AutoBatteryVoltagePath"
AUTO_BATTERY_CAPACITY_ESTIMATE_MIN_SOC_KEY = "AutoBatteryCapacityEstimateMinSoc"
AUTO_BATTERY_CAPACITY_STARTUP_RECHECK_KEY = "AutoBatteryCapacityStartupRecheckSeconds"
AUTO_BATTERY_POWER_PATH_KEY = "AutoBatteryPowerPath"
AUTO_BATTERY_AC_POWER_PATH_KEY = "AutoBatteryAcPowerPath"
AUTO_BATTERY_PV_POWER_PATH_KEY = "AutoBatteryPvPowerPath"
AUTO_BATTERY_GRID_INTERACTION_PATH_KEY = "AutoBatteryGridInteractionPath"
AUTO_BATTERY_OPERATING_MODE_PATH_KEY = "AutoBatteryOperatingModePath"
AUTO_ALLOW_WITHOUT_BATTERY_SOC_KEY = "AutoAllowWithoutBatterySoc"
DEFAULT_BATTERY_CHEMISTRY = "lfp"


def load_auto_battery_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load battery SOC, capacity, and auxiliary telemetry source settings."""
    svc.auto_battery_service = defaults.get(
        AUTO_BATTERY_SERVICE_KEY,
        "com.victronenergy.battery.socketcan_can1",
    ).strip()
    svc.auto_battery_soc_path = defaults.get(AUTO_BATTERY_SOC_PATH_KEY, "/Soc").strip()
    svc.auto_battery_service_prefix = defaults.get(
        AUTO_BATTERY_SERVICE_PREFIX_KEY,
        "com.victronenergy.battery",
    ).strip()
    svc.auto_battery_scan_interval_seconds = float(_config_value(defaults, AUTO_BATTERY_SCAN_INTERVAL_KEY, 60))
    svc.auto_battery_capacity_wh = float(_config_value(defaults, AUTO_BATTERY_CAPACITY_WH_KEY, 0))
    svc.auto_battery_chemistry = (
        str(_config_value(defaults, AUTO_BATTERY_CHEMISTRY_KEY, DEFAULT_BATTERY_CHEMISTRY)).strip().lower()
    )
    svc.auto_battery_capacity_auto_estimate = defaults.get(
        AUTO_BATTERY_CAPACITY_AUTO_ESTIMATE_KEY,
        "1",
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_capacity_wh_path = defaults.get(AUTO_BATTERY_CAPACITY_WH_PATH_KEY, "").strip()
    svc.auto_battery_capacity_ah_path = defaults.get(AUTO_BATTERY_CAPACITY_AH_PATH_KEY, "/InstalledCapacity").strip()
    svc.auto_battery_voltage_path = defaults.get(AUTO_BATTERY_VOLTAGE_PATH_KEY, "/Dc/0/Voltage").strip()
    svc.auto_battery_capacity_estimate_min_soc = float(_config_value(defaults, AUTO_BATTERY_CAPACITY_ESTIMATE_MIN_SOC_KEY, 95))
    svc.auto_battery_capacity_startup_recheck_seconds = float(
        _config_value(defaults, AUTO_BATTERY_CAPACITY_STARTUP_RECHECK_KEY, 300)
    )
    svc.auto_battery_power_path = defaults.get(AUTO_BATTERY_POWER_PATH_KEY, "").strip()
    svc.auto_battery_ac_power_path = defaults.get(AUTO_BATTERY_AC_POWER_PATH_KEY, "").strip()
    svc.auto_battery_pv_power_path = defaults.get(AUTO_BATTERY_PV_POWER_PATH_KEY, "").strip()
    svc.auto_battery_grid_interaction_path = defaults.get(AUTO_BATTERY_GRID_INTERACTION_PATH_KEY, "").strip()
    svc.auto_battery_operating_mode_path = defaults.get(AUTO_BATTERY_OPERATING_MODE_PATH_KEY, "").strip()
    svc.auto_allow_without_battery_soc = defaults.get(
        AUTO_ALLOW_WITHOUT_BATTERY_SOC_KEY,
        "1",
    ).strip().lower() in ("1", "true", "yes", "on")
