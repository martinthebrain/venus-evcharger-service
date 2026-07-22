# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode PV source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_PV_MAX_SERVICES_KEY = "AutoPvMaxServices"
AUTO_PV_SCAN_INTERVAL_KEY = "AutoPvScanIntervalSeconds"


def load_auto_pv_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load core-side PV polling policy without transport details."""
    svc.auto_pv_max_services = int(_config_value(defaults, AUTO_PV_MAX_SERVICES_KEY, 10))
    svc.auto_pv_scan_interval_seconds = float(_config_value(defaults, AUTO_PV_SCAN_INTERVAL_KEY, 60))
