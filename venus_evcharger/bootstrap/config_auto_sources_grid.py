# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode grid source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_GRID_MISSING_STOP_KEY = "AutoGridMissingStopSeconds"


def load_auto_grid_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load core-side grid freshness policy without transport details."""
    svc.auto_grid_missing_stop_seconds = float(_config_value(defaults, AUTO_GRID_MISSING_STOP_KEY, 60))
