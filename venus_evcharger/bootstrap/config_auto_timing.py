# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode timing, audit, and battery-balance config orchestration."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_auto_timing_audit import load_auto_audit_config
from venus_evcharger.bootstrap.config_auto_timing_balance import load_discharge_balance_policy
from venus_evcharger.bootstrap.config_auto_timing_core import load_auto_timing_core_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias import load_victron_bias_policy


def load_auto_timing_policy(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load averaging, runtime, audit, and battery-discharge balancing settings."""
    load_auto_timing_core_config(svc, defaults)
    load_auto_audit_config(svc, defaults)
    load_discharge_balance_policy(svc, defaults)
    load_victron_bias_policy(svc, defaults)
