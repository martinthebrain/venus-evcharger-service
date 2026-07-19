# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration loading for the service-state composition root."""

from __future__ import annotations

import configparser
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from venus_evcharger.controllers.state_contracts import RuntimeOverridePort

_REQUIRED_HOST_KEY = "Host"


def default_state_config_path() -> str:
    return os.path.join(
        os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..")),
        "deploy",
        "venus",
        "config.venus_evcharger.ini",
    )


class StateConfigLoader:
    """Load the required base config and layer validated runtime overrides."""

    def __init__(self, overrides: RuntimeOverridePort, path_provider: Callable[[], str]) -> None:
        self.overrides = overrides
        self.path_provider = path_provider

    def load(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read(self.path_provider())
        if "DEFAULT" not in config or _REQUIRED_HOST_KEY not in config["DEFAULT"]:
            raise ValueError(
                "deploy/venus/config.venus_evcharger.ini is missing or incomplete. "
                "Copy it from the documented deploy/venus/config.venus_evcharger.ini template so the required keys exist."
            )
        return self.overrides.apply_to_config(config)
