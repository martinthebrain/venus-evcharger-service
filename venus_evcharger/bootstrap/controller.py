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
"""

from __future__ import annotations

import configparser
import faulthandler
import logging
import os
import signal
import time
from collections.abc import Callable
from typing import Any

from venus_evcharger.app.bootstrap_support import (
    enable_fault_diagnostics as _enable_fault_diagnostics_impl,
    install_signal_logging as _install_signal_logging_impl,
    logging_level_from_config as _logging_level_from_config,
    request_mainloop_quit as _request_mainloop_quit_impl,
    run_service_loop as _run_service_loop_impl,
    setup_dbus_mainloop as _setup_dbus_mainloop_impl,
)
from venus_evcharger.dbus_gateway import GatewayDbusServiceProxy
from venus_evcharger.bootstrap.config import (
    MONTH_WINDOW_DEFAULTS as _MONTH_WINDOW_DEFAULTS,
    _config_value as _config_value_impl,
    _seasonal_month_windows as _seasonal_month_windows_impl,
)

MONTH_WINDOW_DEFAULTS = _MONTH_WINDOW_DEFAULTS
_config_value = _config_value_impl
_seasonal_month_windows = _seasonal_month_windows_impl


PathSpec = tuple[Any, Callable[[Any, Any], str] | None]
PathMap = dict[str, PathSpec]


def _enable_fault_diagnostics() -> None:
    """Enable crash diagnostics when available."""
    _enable_fault_diagnostics_impl(faulthandler, logging)


def _install_signal_logging(quit_callback: Callable[[], None] | None = None) -> None:
    """Install signal handlers that log and request a clean GLib-loop shutdown."""
    _install_signal_logging_impl(signal, logging, os, quit_callback)


def _setup_dbus_mainloop() -> None:
    """Prepare dbus-python and GLib to run the Venus event loop."""
    _setup_dbus_mainloop_impl(logging)


def _request_mainloop_quit(gobject_module: Any, mainloop: Any) -> None:
    """Request a clean GLib shutdown, preferring idle_add when available."""
    _request_mainloop_quit_impl(gobject_module, mainloop, logging)


def _run_service_loop(service_class: Callable[[], Any], gobject_module: Any) -> None:
    """Instantiate the service and enter the GLib main loop."""
    _run_service_loop_impl(
        service_class,
        gobject_module,
        _install_signal_logging,
        _request_mainloop_quit,
        logging,
    )


from venus_evcharger.bootstrap.config import _ServiceBootstrapConfigMixin
from venus_evcharger.bootstrap.paths import _ServiceBootstrapPathMixin
from venus_evcharger.bootstrap.runtime import _ServiceBootstrapRuntimeMixin

_TEST_PATCH_EXPORTS = (time,)


class ServiceBootstrapController(
    _ServiceBootstrapConfigMixin,
    _ServiceBootstrapRuntimeMixin,
    _ServiceBootstrapPathMixin,
):
    """Bootstrap controller for the Shelly EV charger service."""

    WRITABLE_PATHS = {
        "/MinCurrent",
        "/MaxCurrent",
        "/SetCurrent",
        "/PhaseSelection",
        "/AutoStart",
        "/Auto/StartSurplusWatts",
        "/Auto/StopSurplusWatts",
        "/Auto/MinSoc",
        "/Auto/ResumeSoc",
        "/Auto/StartDelaySeconds",
        "/Auto/StopDelaySeconds",
        "/Auto/ScheduledEnabledDays",
        "/Auto/ScheduledFallbackDelaySeconds",
        "/Auto/ScheduledLatestEndTime",
        "/Auto/ScheduledNightCurrent",
        "/Auto/DbusBackoffBaseSeconds",
        "/Auto/DbusBackoffMaxSeconds",
        "/Auto/GridRecoveryStartSeconds",
        "/Auto/StopSurplusDelaySeconds",
        "/Auto/StopSurplusVolatilityLowWatts",
        "/Auto/StopSurplusVolatilityHighWatts",
        "/Auto/ReferenceChargePowerWatts",
        "/Auto/LearnChargePowerEnabled",
        "/Auto/LearnChargePowerMinWatts",
        "/Auto/LearnChargePowerAlpha",
        "/Auto/LearnChargePowerStartDelaySeconds",
        "/Auto/LearnChargePowerWindowSeconds",
        "/Auto/LearnChargePowerMaxAgeSeconds",
        "/Auto/PhaseSwitching",
        "/Auto/PhasePreferLowestWhenIdle",
        "/Auto/PhaseUpshiftDelaySeconds",
        "/Auto/PhaseDownshiftDelaySeconds",
        "/Auto/PhaseUpshiftHeadroomWatts",
        "/Auto/PhaseDownshiftMarginWatts",
        "/Auto/PhaseMismatchRetrySeconds",
        "/Auto/PhaseMismatchLockoutCount",
        "/Auto/PhaseMismatchLockoutSeconds",
        "/Mode",
        "/StartStop",
        "/Enable",
        "/Auto/PhaseLockoutReset",
        "/Auto/ContactorLockoutReset",
        "/Auto/SoftwareUpdateRun",
    }

    def __init__(
        self,
        service: Any,
        *,
        normalize_phase_func: Callable[[Any], str],
        normalize_mode_func: Callable[[Any], int],
        mode_uses_auto_logic_func: Callable[[Any], bool],
        month_window_func: Callable[[configparser.ConfigParser, int, str, str], Any],
        age_seconds_func: Callable[[float | int | None, float | int | None], int],
        health_code_func: Callable[[str], int],
        phase_values_func: Callable[..., Any],
        read_version_func: Callable[[str], str],
        gobject_module: Any,
        script_path: str,
        formatters: dict[str, Callable[[Any, Any], str] | None],
    ) -> None:
        self.service = service
        self._normalize_phase = normalize_phase_func
        self._normalize_mode = normalize_mode_func
        self._mode_uses_auto_logic = mode_uses_auto_logic_func
        self._month_window = month_window_func
        self._age_seconds = age_seconds_func
        self._health_code = health_code_func
        self._phase_values = phase_values_func
        self._read_version = read_version_func
        self._gobject = gobject_module
        self._script_path = script_path
        self._formatters = formatters

    def initialize_service(self) -> None:
        """Fully initialize the wallbox service instance."""
        bootstrap_steps = (
            ("load-runtime-configuration", self.load_runtime_configuration),
            ("initialize-controllers", self.initialize_controllers),
            ("initialize-virtual-state", self.initialize_virtual_state),
            ("restore-runtime-state", self.restore_runtime_state),
            ("apply-device-metadata", self.apply_device_metadata),
            ("initialize-dbus-service", self.initialize_dbus_service),
            ("register-dbus-paths", self.register_paths),
            ("publish-dbus-service", self.publish_dbus_service),
            ("start-runtime-loops", self.start_runtime_loops),
        )
        for step_name, step_func in bootstrap_steps:
            logging.info("Bootstrap step start: %s", step_name)
            step_func()
            logging.info("Bootstrap step complete: %s", step_name)

    def initialize_dbus_service(self) -> None:
        """Create the EV charger DBus service proxy used by the gateway."""
        svc = self.service
        svc._dbusservice = GatewayDbusServiceProxy(f"{svc.service_name}.http_{svc.deviceinstance}")

    def publish_dbus_service(self) -> None:
        """Tell the gateway to publish the DBus service."""
        dbus_service = self.service._dbusservice
        dbus_service.register()

    def _dbus_service_owned_by_current_process(self, dbus_service: Any) -> bool:
        """Compatibility hook: the DBus adapter, not the core, owns DBus names."""
        del dbus_service
        return False


def run_service_main(service_class: Callable[[], Any], config_path: str, gobject_module: Any) -> None:
    """Entrypoint helper for running the wallbox service as a process."""
    config = configparser.ConfigParser()
    config.read(config_path)
    logging_level = _logging_level_from_config(config)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=logging_level,
    )

    try:
        logging.info("Start Venus EV charger service pid=%s", os.getpid())
        _enable_fault_diagnostics()
        _setup_dbus_mainloop()
        _run_service_loop(service_class, gobject_module)
    except Exception as error:  # pylint: disable=broad-except
        logging.critical("Error at main pid=%s", os.getpid(), exc_info=error)
        raise
