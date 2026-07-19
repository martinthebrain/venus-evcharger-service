# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus path-registration component for service bootstrap."""

from __future__ import annotations

import logging
import platform
import time
from collections.abc import Mapping
from datetime import datetime

from venus_evcharger.bootstrap.contracts import (
    DbusServicePort,
    Formatter,
    require_dbus_service,
    require_write_handler,
)
from venus_evcharger.bootstrap.errors import BOOTSTRAP_DBUS_REGISTRATION_ERRORS
from venus_evcharger.bootstrap.path_defaults import (
    PathMap,
    age_counter_diagnostic_defaults,
    backend_diagnostic_defaults,
    decision_diagnostic_defaults,
    phase_diagnostic_defaults,
    runtime_timing_diagnostic_defaults,
    scheduled_diagnostic_defaults,
    software_update_diagnostic_defaults,
)
from venus_evcharger.bootstrap.path_groups import control_paths, management_paths, measurement_paths
from venus_evcharger.core.common import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)


class ServicePathRegistrar:
    """Build and register the immutable, control, and diagnostic path sets."""

    def __init__(self, service: object, *, script_path: str, formatters: Mapping[str, Formatter]) -> None:
        self._service = service
        self._script_path = script_path
        self._formatters = formatters

    def register(self) -> None:
        """Register every DBus path exposed by the emulated EV charger."""
        dbus_service = require_dbus_service(self._service)
        write_handler = require_write_handler(self._service)
        self._register_management_paths(dbus_service)
        for path, (initial, formatter) in self.path_map().items():
            logging.debug("Registering path: %s initial=%r formatter=%r", path, initial, formatter)
            try:
                dbus_service.add_path(
                    path,
                    initial,
                    gettextcallback=formatter,
                    onchangecallback=write_handler.handle_dbus_write,
                )
            except BOOTSTRAP_DBUS_REGISTRATION_ERRORS as error:
                logging.error("Failed to register path %s: %s", path, error, exc_info=error)
                raise

    def path_map(self) -> PathMap:
        """Return the complete dynamic EV charger path map."""
        return {
            **measurement_paths(self._formatters),
            **control_paths(self._service, self._formatters),
            **self.diagnostic_paths(),
        }

    def diagnostic_paths(self) -> PathMap:
        """Return initial Auto and runtime diagnostic path values."""
        svc = self._service
        scheduled_snapshot = self._scheduled_snapshot()
        return {
            "/Auto/Health": (getattr(svc, "_last_health_reason", "init"), None),
            "/Auto/HealthCode": (getattr(svc, "_last_health_code", 0), None),
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

    def _register_management_paths(self, dbus_service: DbusServicePort) -> None:
        for path, initial in management_paths(
            self._service,
            self._script_path,
            platform.python_version(),
        ).items():
            dbus_service.add_path(path, initial)

    def _scheduled_snapshot(self) -> object | None:
        svc = self._service
        virtual_mode = getattr(svc, "virtual_mode", 0)
        if not mode_uses_scheduled_logic(virtual_mode):
            return None
        snapshot: object = scheduled_mode_snapshot(
            datetime.fromtimestamp(time.time()),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "06:30"),
        )
        return snapshot
