# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode battery source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_BATTERY_SCAN_INTERVAL_KEY = "AutoBatteryScanIntervalSeconds"
AUTO_ALLOW_WITHOUT_BATTERY_SOC_KEY = "AutoAllowWithoutBatterySoc"


def load_auto_battery_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load core-side battery polling and availability policy."""
    svc.auto_battery_scan_interval_seconds = float(_config_value(defaults, AUTO_BATTERY_SCAN_INTERVAL_KEY, 60))
    svc.auto_allow_without_battery_soc = defaults.get(
        AUTO_ALLOW_WITHOUT_BATTERY_SOC_KEY,
        "1",
    ).strip().lower() in ("1", "true", "yes", "on")
