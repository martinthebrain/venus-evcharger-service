# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition root for service runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.bootstrap.config_auto import AutoConfigLoader
from venus_evcharger.bootstrap.config_backend import BackendConfigLoader
from venus_evcharger.bootstrap.config_identity import IdentityConfigLoader
from venus_evcharger.bootstrap.config_shared import MONTH_WINDOW_DEFAULTS, _config_value, _seasonal_month_windows
from venus_evcharger.bootstrap.contracts import require_config_state

__all__ = [
    "MONTH_WINDOW_DEFAULTS",
    "ServiceConfigLoader",
    "_config_value",
    "_seasonal_month_windows",
]


@dataclass(frozen=True)
class ServiceConfigLoader:
    """Coordinate the independent configuration components in one order."""

    service: object
    identity: IdentityConfigLoader
    backend: BackendConfigLoader
    auto: AutoConfigLoader

    def load(self) -> None:
        """Load, normalize, and validate the complete runtime configuration."""
        state = require_config_state(self.service)
        config = state.load_config()
        setattr(self.service, "config", config)
        defaults = config["DEFAULT"]
        self.identity.load(defaults)
        self.backend.load()
        self.auto.load(defaults)
        state.validate_runtime_config()
