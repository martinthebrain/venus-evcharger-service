# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode combined energy-source config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value
from venus_evcharger.energy import load_energy_source_settings

AUTO_DBUS_BACKOFF_BASE_KEY = "AutoDbusBackoffBaseSeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_DBUS_BACKOFF_MAX_KEY = "AutoDbusBackoffMaxSeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.


def load_auto_energy_source_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load multi-source battery/PV inventory settings."""
    svc.auto_energy_sources, svc.auto_use_combined_battery_soc = load_energy_source_settings(defaults)
    svc.auto_energy_source_ids = tuple(source.source_id for source in svc.auto_energy_sources)
    svc.auto_dbus_backoff_base_seconds = float(_config_value(defaults, AUTO_DBUS_BACKOFF_BASE_KEY, 5))
    svc.auto_dbus_backoff_max_seconds = float(_config_value(defaults, AUTO_DBUS_BACKOFF_MAX_KEY, 60))
