# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode Victron grid-bias safety config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

BIAS_LOCKOUT_ENABLED_KEY = "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled"
BIAS_LOCKOUT_WINDOW_KEY = "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds"
BIAS_LOCKOUT_MIN_CHANGES_KEY = "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges"
BIAS_LOCKOUT_DURATION_KEY = "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds"
BIAS_ROLLBACK_ENABLED_KEY = "AutoBatteryDischargeBalanceVictronBiasRollbackEnabled"
BIAS_ROLLBACK_MIN_STABILITY_KEY = "AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore"
BIAS_REQUIRE_CLEAN_PHASES_KEY = "AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases"
DEFAULT_TRUE = "1"


def load_victron_bias_safety_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load Victron grid-bias oscillation lockout, rollback, and telemetry settings."""
    svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled = defaults.get(
        BIAS_LOCKOUT_ENABLED_KEY,
        DEFAULT_TRUE,
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds = float(
        _config_value(defaults, BIAS_LOCKOUT_WINDOW_KEY, 120.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes = int(
        _config_value(defaults, BIAS_LOCKOUT_MIN_CHANGES_KEY, 3)
    )
    svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds = float(
        _config_value(defaults, BIAS_LOCKOUT_DURATION_KEY, 180.0)
    )
    svc.auto_battery_discharge_balance_victron_bias_rollback_enabled = defaults.get(
        BIAS_ROLLBACK_ENABLED_KEY,
        DEFAULT_TRUE,
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score = float(
        _config_value(defaults, BIAS_ROLLBACK_MIN_STABILITY_KEY, 0.45)
    )
    svc.auto_battery_discharge_balance_victron_bias_require_clean_phases = defaults.get(
        BIAS_REQUIRE_CLEAN_PHASES_KEY,
        DEFAULT_TRUE,
    ).strip().lower() in ("1", "true", "yes", "on")
