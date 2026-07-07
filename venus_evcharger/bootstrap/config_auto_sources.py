# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode PV, battery, grid, and energy-source config orchestration."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_auto_sources_battery import load_auto_battery_source_config
from venus_evcharger.bootstrap.config_auto_sources_energy import load_auto_energy_source_config
from venus_evcharger.bootstrap.config_auto_sources_grid import load_auto_grid_source_config
from venus_evcharger.bootstrap.config_auto_sources_pv import load_auto_pv_source_config


def load_auto_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load all Auto input source configuration groups."""
    load_auto_pv_source_config(svc, defaults)
    load_auto_battery_source_config(svc, defaults)
    load_auto_energy_source_config(svc, defaults)
    load_auto_grid_source_config(svc, defaults)
