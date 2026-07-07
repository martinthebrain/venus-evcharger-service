# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode PV source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_PV_SERVICE_KEY = "AutoPvService"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_PV_SERVICE_PREFIX_KEY = "AutoPvServicePrefix"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_PV_PATH_KEY = "AutoPvPath"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_PV_MAX_SERVICES_KEY = "AutoPvMaxServices"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_PV_SCAN_INTERVAL_KEY = "AutoPvScanIntervalSeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_USE_DC_PV_KEY = "AutoUseDcPv"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_DC_PV_SERVICE_KEY = "AutoDcPvService"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_DC_PV_PATH_KEY = "AutoDcPvPath"  # pragma: no mutate - ConfigParser option keys are case-insensitive.


def load_auto_pv_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load AC and DC PV DBus source settings."""
    svc.auto_pv_service = defaults.get(AUTO_PV_SERVICE_KEY, "").strip()
    svc.auto_pv_service_prefix = defaults.get(
        AUTO_PV_SERVICE_PREFIX_KEY,
        "com.victronenergy.pvinverter",
    ).strip()
    svc.auto_pv_path = defaults.get(AUTO_PV_PATH_KEY, "/Ac/Power").strip()
    svc.auto_pv_max_services = int(_config_value(defaults, AUTO_PV_MAX_SERVICES_KEY, 10))
    svc.auto_pv_scan_interval_seconds = float(_config_value(defaults, AUTO_PV_SCAN_INTERVAL_KEY, 60))
    svc.auto_use_dc_pv = defaults.get(AUTO_USE_DC_PV_KEY, "1").strip().lower() in ("1", "true", "yes", "on")
    svc.auto_dc_pv_service = defaults.get(AUTO_DC_PV_SERVICE_KEY, "com.victronenergy.system").strip()
    svc.auto_dc_pv_path = defaults.get(AUTO_DC_PV_PATH_KEY, "/Dc/Pv/Power").strip()
