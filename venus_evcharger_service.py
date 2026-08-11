#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Expose a Shelly relay meter as a Venus OS EV charger tile.

This is the public composition root of the wallbox service. Runtime behavior
is owned by explicit components; the service itself has no role inheritance.
"""

from __future__ import annotations

import os
import sys
import time

from gi.repository import GLib as gobject  # pylint: disable=import-error

sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)

from venus_evcharger.bootstrap.controller import run_service_main
from venus_evcharger.core.common import (
    _age_seconds,
    _health_code,
    mode_uses_auto_logic,
    month_in_ranges,
    month_window,
    normalize_mode,
    normalize_phase,
    parse_hhmm,
    phase_values,
    read_version,
)
from venus_evcharger.service.auto_facade import ServiceAutoFacade
from venus_evcharger.service.control import ServiceControlFacade
from venus_evcharger.service.controller_owner import ServiceControllerOwner, ServiceFunctionBundle
from venus_evcharger.service.runtime_facade import ServiceRuntimeFacade
from venus_evcharger.service.state_facade import ServiceStateFacade
from venus_evcharger.service.update_facade import ServiceUpdateFacade

__all__ = [
    "ShellyWallboxService",
    "main",
    "gobject",
    "mode_uses_auto_logic",
    "month_in_ranges",
    "month_window",
    "normalize_mode",
    "normalize_phase",
    "parse_hhmm",
    "phase_values",
]


class ShellyWallboxService:
    """Expose a Shelly relay meter as a Venus OS EV charger tile."""
    controllers: ServiceControllerOwner
    runtime: ServiceRuntimeFacade
    state: ServiceStateFacade
    update: ServiceUpdateFacade
    control: ServiceControlFacade
    auto: ServiceAutoFacade
    _control_command_async_enabled: bool

    @staticmethod
    def time_now() -> float:
        """Return the current wall-clock time."""
        return time.time()

    @staticmethod
    def monotonic_now() -> float:
        """Return the process-local monotonic clock for durations and ordering."""
        return time.monotonic()

    def __init__(self) -> None:
        """Initialize configuration, DBus service, and runtime state."""
        functions = ServiceFunctionBundle(
            normalize_phase=normalize_phase,
            normalize_mode=normalize_mode,
            mode_uses_auto_logic=mode_uses_auto_logic,
            month_window=month_window,
            age_seconds=_age_seconds,
            health_code=_health_code,
            phase_values=phase_values,
            read_version=read_version,
            gobject=gobject,
            script_path=__file__,
            config_path=ServiceStateFacade.config_path(),
            auto_input_helper_path=os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                "venus_evcharger_auto_input_helper.py",
            ),
        )
        self.controllers = ServiceControllerOwner(self, functions)
        self.runtime = ServiceRuntimeFacade(self.controllers)
        self.state = ServiceStateFacade(self.controllers, self.runtime)
        self.update = ServiceUpdateFacade(self.controllers)
        self.control = ServiceControlFacade(self)
        self.auto = ServiceAutoFacade(
            self.controllers,
            self.control.publish_command_event,
        )
        self.controllers.bootstrap.initialize_service()


def main() -> None:
    """Entrypoint for running as a service."""
    run_service_main(ShellyWallboxService, ServiceStateFacade.config_path(), gobject)


if __name__ == "__main__":
    main()
