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

import logging
import platform
import time
from datetime import datetime
from typing import Any

from venus_evcharger.bootstrap.errors import BOOTSTRAP_DBUS_REGISTRATION_ERRORS
from venus_evcharger.bootstrap.path_groups import control_paths, management_paths, measurement_paths
from venus_evcharger.bootstrap.path_defaults import (
    age_counter_diagnostic_defaults,
    backend_diagnostic_defaults,
    decision_diagnostic_defaults,
    phase_diagnostic_defaults,
    scheduled_diagnostic_defaults,
    software_update_diagnostic_defaults,
    runtime_timing_diagnostic_defaults,
)
from venus_evcharger.bootstrap.runtime import _ServiceBootstrapRuntime
from venus_evcharger.core.common import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)
from venus_evcharger.bootstrap.path_defaults import PathMap


class _ServiceBootstrapPath(_ServiceBootstrapRuntime):
    def register_paths(self) -> None:
        """Register all DBus paths exposed by the emulated EV charger."""
        svc = self.service
        self._register_management_paths()
        for path, (initial, formatter) in self._all_service_paths().items():
            logging.debug("Registering path: %s initial=%r formatter=%r", path, initial, formatter)
            try:
                svc._dbusservice.add_path(
                    path,
                    initial,
                    gettextcallback=formatter,
                    onchangecallback=svc._handle_write,
                )
            except BOOTSTRAP_DBUS_REGISTRATION_ERRORS as error:
                logging.error("Failed to register path %s: %s", path, error, exc_info=error)
                raise

    def _register_management_paths(self) -> None:
        """Register immutable management and identity DBus paths."""
        svc = self.service
        for path, initial in management_paths(svc, self._script_path, platform.python_version()).items():
            svc._dbusservice.add_path(path, initial)

    def _measurement_paths(self) -> PathMap:
        """Return measurement and energy paths shown on the EV charger tile."""
        return measurement_paths(self._formatters)

    def _control_paths(self) -> PathMap:
        """Return writable and status-like EV charger control paths."""
        return control_paths(self.service, self._formatters)

    def _diagnostic_paths(self) -> PathMap:
        """Return Auto-diagnostic DBus paths published by the service."""
        svc = self.service
        scheduled_snapshot = self._scheduled_snapshot()
        return {
            "/Auto/Health": (svc._last_health_reason, None),
            "/Auto/HealthCode": (svc._last_health_code, None),
            "/Auto/State": (getattr(svc, "_last_auto_state", "idle"), None),
            "/Auto/StateCode": (getattr(svc, "_last_auto_state_code", 0), None),
            "/Auto/RecoveryActive": (0, None),
            "/Auto/StatusSource": (str(getattr(svc, "_last_status_source", "unknown")), None),
            "/Auto/FaultActive": (0, None),
            "/Auto/FaultReason": ("", None),
            **scheduled_diagnostic_defaults(scheduled_snapshot),
            **backend_diagnostic_defaults(svc),
            **decision_diagnostic_defaults(svc),
            **software_update_diagnostic_defaults(svc),
            **phase_diagnostic_defaults(svc),
            **age_counter_diagnostic_defaults(),
            **runtime_timing_diagnostic_defaults(),
        }

    def _all_service_paths(self) -> PathMap:
        """Return the complete dynamic EV charger DBus path map."""
        return {
            **self._measurement_paths(),
            **self._control_paths(),
            **self._diagnostic_paths(),
        }

    def _scheduled_snapshot(self) -> Any | None:
        """Return one initial scheduled-mode diagnostic snapshot for path registration."""
        svc = self.service
        if not mode_uses_scheduled_logic(svc.virtual_mode):
            return None
        return scheduled_mode_snapshot(
            datetime.fromtimestamp(time.time()),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "06:30"),
        )
