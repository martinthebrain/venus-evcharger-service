# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode bootstrap configuration component."""

from __future__ import annotations

import configparser

from venus_evcharger.auto.policy import load_auto_policy_from_config
from venus_evcharger.bootstrap.config_auto_daytime import load_auto_daytime_policy
from venus_evcharger.bootstrap.config_auto_helper import load_helper_and_timeout_config
from venus_evcharger.bootstrap.config_auto_sources import load_auto_source_config
from venus_evcharger.bootstrap.config_auto_timing import load_auto_timing_policy
from venus_evcharger.bootstrap.contracts import MonthWindow


class AutoConfigLoader:
    """Load Auto sources, policy, scheduling, and helper behavior."""

    def __init__(self, service: object, month_window: MonthWindow) -> None:
        self._service = service
        self._month_window = month_window

    def load(self, defaults: configparser.SectionProxy) -> None:
        """Apply every Auto-related configuration group in dependency order."""
        load_auto_source_config(self._service, defaults)
        setattr(self._service, "auto_policy", load_auto_policy_from_config(defaults))
        load_auto_timing_policy(self._service, defaults)
        load_auto_daytime_policy(self._service, defaults, self._month_window)
        load_helper_and_timeout_config(self._service, defaults)
