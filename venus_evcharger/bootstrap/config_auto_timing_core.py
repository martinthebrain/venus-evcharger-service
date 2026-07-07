# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode core timing config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_AVERAGE_WINDOW_KEY = "AutoAverageWindowSeconds"
AUTO_MIN_RUNTIME_KEY = "AutoMinRuntimeSeconds"
AUTO_MIN_OFFTIME_KEY = "AutoMinOfftimeSeconds"
AUTO_START_DELAY_KEY = "AutoStartDelaySeconds"
AUTO_STOP_DELAY_KEY = "AutoStopDelaySeconds"
AUTO_INPUT_CACHE_KEY = "AutoInputCacheSeconds"


def load_auto_timing_core_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load averaging, runtime, delay, and input-cache timing settings."""
    svc.auto_average_window_seconds = float(_config_value(defaults, AUTO_AVERAGE_WINDOW_KEY, 30))
    svc.auto_min_runtime_seconds = float(_config_value(defaults, AUTO_MIN_RUNTIME_KEY, 300))
    svc.auto_min_offtime_seconds = float(_config_value(defaults, AUTO_MIN_OFFTIME_KEY, 120))
    svc.auto_start_delay_seconds = float(_config_value(defaults, AUTO_START_DELAY_KEY, 10))
    svc.auto_stop_delay_seconds = float(_config_value(defaults, AUTO_STOP_DELAY_KEY, 10))
    svc.auto_input_cache_seconds = float(_config_value(defaults, AUTO_INPUT_CACHE_KEY, 120))
