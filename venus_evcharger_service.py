#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Expose a Shelly relay meter as a Venus OS EV charger tile.

This is the public entry point of the wallbox service. The module mainly
assembles the service class from smaller controllers/mixins and then delegates
 startup and main-loop setup to the bootstrap helpers.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from os import stat_result
from typing import TYPE_CHECKING, Any

import dbus

if sys.version_info.major == 2:
    import gobject  # pylint: disable=import-error
else:
    from gi.repository import GLib as gobject  # pylint: disable=import-error

sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)

from venus_evcharger.bootstrap.controller import ServiceBootstrapController, run_service_main
from venus_evcharger.core.common import (
    _a,
    _age_seconds,
    _health_code,
    _kwh,
    _status_label,
    _v,
    _w,
    mode_uses_auto_logic,
    month_in_ranges,
    month_window,
    normalize_mode,
    normalize_phase,
    parse_hhmm,
    phase_values,
    read_version,
)
from venus_evcharger.service.bindings import (
    ControlApiMixin,
    DbusAutoLogicMixin,
    RuntimeHelperMixin,
    StatePublishMixin,
    UpdateCycleMixin,
)
from venus_evcharger.controllers.state import ServiceStateController

if TYPE_CHECKING:
    FormatterMap = dict[str, Callable[[Any, Any], str] | None]

__all__ = [
    "ShellyWallboxService",
    "main",
    "dbus",
    "gobject",
    "mode_uses_auto_logic",
    "month_in_ranges",
    "month_window",
    "normalize_mode",
    "normalize_phase",
    "parse_hhmm",
    "phase_values",
]


class ShellyWallboxService(ControlApiMixin, StatePublishMixin, RuntimeHelperMixin, DbusAutoLogicMixin, UpdateCycleMixin):
    """Expose a Shelly relay meter as a Venus OS EV charger tile."""
    _normalize_mode_func = staticmethod(normalize_mode)
    _mode_uses_auto_logic_func = staticmethod(mode_uses_auto_logic)
    _normalize_phase_func = staticmethod(normalize_phase)
    _month_window_func = staticmethod(month_window)
    _age_seconds_func = staticmethod(_age_seconds)
    _health_code_func = staticmethod(_health_code)
    _phase_values_func = staticmethod(phase_values)
    _read_version_func = staticmethod(read_version)
    _gobject_module = gobject
    _script_path_value = __file__
    _formatter_bundle: "FormatterMap" = {
        "kwh": _kwh,
        "a": _a,
        "w": _w,
        "v": _v,
        "status": _status_label,
    }
    _state_controller: ServiceStateController
    _bootstrap_controller: ServiceBootstrapController
    _system_bus: Any
    _system_bus_state: threading.local
    _system_bus_generation: int

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Convert values defensively to float."""
        try:
            if value is None:
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _time_now() -> float:
        """Return the current wall-clock time.

        This wrapper keeps time-based tests stable even when helper logic is
        delegated into support modules.
        """
        return time.time()

    def __init__(self) -> None:
        """Initialize configuration, DBus service, and runtime state."""
        self._state_controller = ServiceStateController(self, normalize_mode)
        self._bootstrap_controller = ServiceBootstrapController(
            self,
            normalize_phase_func=normalize_phase,
            normalize_mode_func=normalize_mode,
            mode_uses_auto_logic_func=mode_uses_auto_logic,
            month_window_func=month_window,
            age_seconds_func=_age_seconds,
            health_code_func=_health_code,
            phase_values_func=phase_values,
            read_version_func=read_version,
            gobject_module=gobject,
            script_path=__file__,
            formatters=self._formatter_bundle,
        )
        self._bootstrap_controller.initialize_service()

    @staticmethod
    def _auto_input_helper_path() -> str:
        """Return the helper script path used for Auto input collection."""
        return os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "venus_evcharger_auto_input_helper.py",
        )

    @staticmethod
    def _dbus_introspection_worker_path() -> str:
        """Return the advisory DBus introspection worker script path."""
        return os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "venus_evcharger_dbus_introspection_worker.py",
        )

    @staticmethod
    def _stat_path(path: str) -> stat_result:
        """Return `os.stat(path)`.

        Kept as a wrapper so tests can continue patching the main module.
        """
        return os.stat(path)

    @staticmethod
    def _load_json_file(path: str) -> Any:
        """Load a JSON file using the main module's patched `open` when needed."""
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _get_system_bus(self) -> Any:
        """Get the system DBus for the current thread.

        dbus-python connections are safer when they are not shared across
        worker threads. Each thread keeps its own cached connection, while a
        generation counter lets recovery logic invalidate all thread-local
        connections lazily.
        """
        self._ensure_system_bus_state()
        current_generation = self._system_bus_generation
        cached_generation = getattr(self._system_bus_state, "generation", None)
        cached_bus = getattr(self._system_bus_state, "bus", None)
        if cached_bus is None or cached_generation != current_generation:
            cached_bus = self._create_system_bus()
            self._system_bus_state.bus = cached_bus
            self._system_bus_state.generation = current_generation
            self._system_bus = cached_bus
        return cached_bus


def main() -> None:
    """Entrypoint for running as a service."""
    run_service_main(ShellyWallboxService, ShellyWallboxService._config_path(), gobject)


if __name__ == "__main__":
    main()
