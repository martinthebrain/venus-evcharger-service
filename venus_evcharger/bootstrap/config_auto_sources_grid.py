# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode grid source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_GRID_SERVICE_KEY = "AutoGridService"
AUTO_GRID_L1_PATH_KEY = "AutoGridL1Path"
AUTO_GRID_L2_PATH_KEY = "AutoGridL2Path"
AUTO_GRID_L3_PATH_KEY = "AutoGridL3Path"
AUTO_GRID_REQUIRE_ALL_PHASES_KEY = "AutoGridRequireAllPhases"
AUTO_GRID_MISSING_STOP_KEY = "AutoGridMissingStopSeconds"
AUTO_GRID_RECOVERY_START_KEY = "AutoGridRecoveryStartSeconds"
AUTO_START_DELAY_KEY = "AutoStartDelaySeconds"


def load_auto_grid_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load grid service, phase paths, and recovery timing settings."""
    svc.auto_grid_service = defaults.get(AUTO_GRID_SERVICE_KEY, "com.victronenergy.system").strip()
    svc.auto_grid_l1_path = defaults.get(AUTO_GRID_L1_PATH_KEY, "/Ac/Grid/L1/Power").strip()
    svc.auto_grid_l2_path = defaults.get(AUTO_GRID_L2_PATH_KEY, "/Ac/Grid/L2/Power").strip()
    svc.auto_grid_l3_path = defaults.get(AUTO_GRID_L3_PATH_KEY, "/Ac/Grid/L3/Power").strip()
    svc.auto_grid_require_all_phases = defaults.get(
        AUTO_GRID_REQUIRE_ALL_PHASES_KEY,
        "1",
    ).strip().lower() in ("1", "true", "yes", "on")
    svc.auto_grid_missing_stop_seconds = float(_config_value(defaults, AUTO_GRID_MISSING_STOP_KEY, 60))
    svc.auto_grid_recovery_start_seconds = float(
        _config_value(defaults, AUTO_GRID_RECOVERY_START_KEY, _config_value(defaults, AUTO_START_DELAY_KEY, 10))
    )
