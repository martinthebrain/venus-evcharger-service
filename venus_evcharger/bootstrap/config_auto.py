# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode bootstrap config orchestration."""

from __future__ import annotations

import configparser

from venus_evcharger.auto.policy import load_auto_policy_from_config
from venus_evcharger.bootstrap.config_auto_daytime import load_auto_daytime_policy
from venus_evcharger.bootstrap.config_auto_helper import load_helper_and_timeout_config
from venus_evcharger.bootstrap.config_auto_sources import load_auto_source_config
from venus_evcharger.bootstrap.config_auto_timing import load_auto_timing_policy
from venus_evcharger.bootstrap.config_backend import _ServiceBootstrapBackendConfig


class _ServiceBootstrapAutoConfig(_ServiceBootstrapBackendConfig):
    def _load_auto_source_config(self, defaults: configparser.SectionProxy) -> None:
        """Load PV, battery, and grid source configuration for Auto mode."""
        load_auto_source_config(self.service, defaults)

    def _load_auto_policy_config(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds, seasonal windows, and timing policy."""
        self._load_auto_surplus_thresholds(defaults)
        self._load_auto_timing_policy(defaults)
        self._load_auto_daytime_policy(defaults)

    def _load_auto_surplus_thresholds(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds around surplus, SOC, and grid import."""
        load_auto_policy_from_config(defaults, self.service)

    def _load_auto_timing_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load averaging, runtime, and delay settings for Auto mode."""
        load_auto_timing_policy(self.service, defaults)

    def _load_auto_daytime_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load seasonal day-window behavior plus Scheduled-mode settings."""
        load_auto_daytime_policy(self.service, defaults, self._month_window)

    def _load_helper_and_timeout_config(self, defaults: configparser.SectionProxy) -> None:
        """Load helper-process, watchdog, and request timeout settings."""
        load_helper_and_timeout_config(self.service, defaults)
