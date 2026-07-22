# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto helper-process, DBus gateway path, and timeout config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_auto_helper_gateway import load_gateway_config
from venus_evcharger.bootstrap.config_auto_helper_polling import load_helper_polling_config
from venus_evcharger.bootstrap.config_auto_helper_resilience import load_helper_resilience_config


def load_helper_and_timeout_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load helper-process, watchdog, gateway, and request timeout settings."""
    load_helper_polling_config(svc, defaults)
    load_gateway_config(svc, defaults)
    load_helper_resilience_config(svc, defaults)
