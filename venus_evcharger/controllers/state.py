# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and RAM-only runtime-state helpers for the Venus EV charger service."""

from __future__ import annotations

import configparser
import os
import time
from typing import Any, Callable

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.controllers.state_restore import _StateRuntimeRestoreMixin
from venus_evcharger.controllers.state_runtime import _StateRuntimeMixin
from venus_evcharger.controllers.state_specs import (
    RUNTIME_OVERRIDE_BY_CONFIG_KEY,
    RUNTIME_OVERRIDE_BY_PATH,
    RUNTIME_OVERRIDE_SECTION,
    RUNTIME_OVERRIDE_SPECS,
    RuntimeOverrideSpec,
    _CasePreservingConfigParser,
)
from venus_evcharger.controllers.state_summary import _StateSummaryMixin
from venus_evcharger.controllers.state_validation import _StateValidationMixin


class ServiceStateController(
    _StateValidationMixin,
    _StateRuntimeRestoreMixin,
    _StateRuntimeMixin,
    _StateSummaryMixin,
):
    """Encapsulate config loading, config validation, and volatile runtime state."""

    def __init__(self, service: Any, normalize_mode_func: Callable[[object], int]) -> None:
        self.service = service
        self._normalize_mode = normalize_mode_func

    @staticmethod
    def config_path() -> str:
        return os.path.join(
            os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..")),
            "deploy",
            "venus",
            "config.venus_evcharger.ini",
        )

    @classmethod
    def runtime_overrides_path(cls, defaults: configparser.SectionProxy) -> str:
        device_instance = defaults.get("DeviceInstance", "60").strip() or "60"
        fallback = f"/run/dbus-venus-evcharger-overrides-{device_instance}.ini"
        return defaults.get("RuntimeOverridesPath", fallback).strip()

    def load_config(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read(self.config_path())
        if "DEFAULT" not in config or "Host" not in config["DEFAULT"]:
            raise ValueError(
                "deploy/venus/config.venus_evcharger.ini is missing or incomplete. "
                "Copy it from the documented deploy/venus/config.venus_evcharger.ini template so the required keys exist."
            )
        return self._apply_runtime_overrides_to_config(self.service, config)

