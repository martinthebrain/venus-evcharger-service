# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode Victron grid-bias auto-apply config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

BIAS_AUTO_APPLY_ENABLED_KEY = "AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_AUTO_APPLY_MIN_CONFIDENCE_KEY = "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_AUTO_APPLY_MIN_PROFILE_SAMPLES_KEY = "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_AUTO_APPLY_MIN_STABILITY_KEY = "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_AUTO_APPLY_BLEND_KEY = "AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
BIAS_OBSERVATION_WINDOW_KEY = "AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
DEFAULT_FALSE = "0"  # pragma: no mutate - any non-true token normalizes to False.


def load_victron_bias_auto_apply_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load Victron grid-bias learned-profile auto-apply settings."""
    svc.auto_battery_discharge_balance_victron_bias_auto_apply_enabled = defaults.get(
        BIAS_AUTO_APPLY_ENABLED_KEY,
        DEFAULT_FALSE,
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence = float(
        _config_value(defaults, BIAS_AUTO_APPLY_MIN_CONFIDENCE_KEY, 0.85)
    )
    svc.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples = int(
        _config_value(defaults, BIAS_AUTO_APPLY_MIN_PROFILE_SAMPLES_KEY, 3)
    )
    svc.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score = float(
        _config_value(defaults, BIAS_AUTO_APPLY_MIN_STABILITY_KEY, 0.75)
    )
    svc.auto_battery_discharge_balance_victron_bias_auto_apply_blend = float(
        _config_value(defaults, BIAS_AUTO_APPLY_BLEND_KEY, 0.25)
    )
    svc.auto_battery_discharge_balance_victron_bias_observation_window_seconds = float(
        _config_value(defaults, BIAS_OBSERVATION_WINDOW_KEY, 30.0)
    )
