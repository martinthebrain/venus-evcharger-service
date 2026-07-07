# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode Victron grid-bias config orchestration."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_auto_timing_victron_bias_apply import load_victron_bias_auto_apply_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_base import load_victron_bias_base_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_pid import load_victron_bias_pid_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_safety import load_victron_bias_safety_config


def load_victron_bias_policy(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load Victron grid-bias battery discharge balance settings."""
    load_victron_bias_base_config(svc, defaults)
    load_victron_bias_pid_config(svc, defaults)
    load_victron_bias_auto_apply_config(svc, defaults)
    load_victron_bias_safety_config(svc, defaults)
