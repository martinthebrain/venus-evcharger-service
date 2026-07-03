# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Venus EV charger service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop

This module is effectively the assembly line for the service. It turns the
configuration file into a ready-to-run runtime object graph and fills the
service instance with the normalized attributes the rest of the codebase
expects.
"""

from __future__ import annotations

import configparser

from venus_evcharger.bootstrap.config_auto import _ServiceBootstrapAutoConfig
from venus_evcharger.bootstrap.config_shared import MONTH_WINDOW_DEFAULTS, _config_value, _seasonal_month_windows

__all__ = [
    "MONTH_WINDOW_DEFAULTS",
    "_config_value",
    "_seasonal_month_windows",
    "_ServiceBootstrapConfig",
]


class _ServiceBootstrapConfig(_ServiceBootstrapAutoConfig):
    def load_runtime_configuration(self) -> None:
        """Load the on-disk config and map it onto service attributes.

        The loading order is important:

        1. identity and basic runtime paths
        2. backend topology
        3. DBus input sources
        4. Auto and Scheduled policy
        5. helper and timeout behavior

        That order mirrors how a person would usually think about a deployment:
        first "what is this service", then "what hardware is attached", then
        "how should it behave".
        """
        svc = self.service
        svc.config = svc._load_config()
        defaults = svc.config["DEFAULT"]
        self._load_identity_config(defaults)
        self._load_backend_config()
        self._load_auto_source_config(defaults)
        self._load_auto_policy_config(defaults)
        self._load_helper_and_timeout_config(defaults)
        svc._validate_runtime_config()
