# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy controller factory role for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from venus_evcharger.bootstrap.controller import ServiceBootstrapController
from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.backend.shelly_io_types import require_shelly_io_host
from venus_evcharger.update.controller import UpdateCycleController
from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.companion import EnergyCompanionDbusBridge
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.controllers.write import DbusWriteController
from venus_evcharger.ports import AutoDecisionPort, DbusInputPort, UpdateCyclePort, WriteControllerPort
from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.runtime import RuntimeSupportController

_ControllerT = TypeVar("_ControllerT")


def _required_controller(controller: _ControllerT | None, name: str) -> _ControllerT:
    if controller is None:
        raise RuntimeError(f"{name} was not initialized")
    return controller


class ServiceControllerFactory:
    """Lazy controller builders shared by the wallbox service roles."""

    _normalize_mode_func: Callable[[Any], int]
    _mode_uses_auto_logic_func: Callable[[int], bool]
    _normalize_phase_func: Callable[[Any], str]
    _month_window_func: Callable[..., Any]
    _age_seconds_func: Callable[[float | int | None, float | int | None], int]
    _health_code_func: Callable[[str], int]
    _phase_values_func: Callable[..., Any]
    _read_version_func: Callable[[str], str]
    _gobject_module: Any
    _script_path_value: str
    _formatter_bundle: dict[str, Callable[[Any, Any], str] | None]
    _dbus_publisher: DbusPublishController | None = None
    _auto_controller: AutoDecisionController | None = None
    _shelly_io_controller: ShellyIoController | None = None
    _state_controller: ServiceStateController | None = None
    _write_controller: DbusWriteController | None = None
    _auto_input_supervisor: AutoInputSupervisor | None = None
    _runtime_support_controller: RuntimeSupportController | None = None
    _dbus_input_controller: DbusInputController | None = None
    _bootstrap_controller: ServiceBootstrapController | None = None
    _update_controller: UpdateCycleController | None = None
    _companion_dbus_bridge: EnergyCompanionDbusBridge | None = None

    def _ensure_dbus_publisher(self) -> DbusPublishController:
        if not hasattr(self, "_dbus_publisher") or self._dbus_publisher is None:
            self._dbus_publisher = DbusPublishController(self, self._age_seconds_func)
        return _required_controller(self._dbus_publisher, "_dbus_publisher")

    def _ensure_auto_controller(self) -> AutoDecisionController:
        if not hasattr(self, "_auto_controller") or self._auto_controller is None:
            self._auto_controller = AutoDecisionController(
                AutoDecisionPort(self),
                self._health_code_func,
                self._mode_uses_auto_logic_func,
            )
        return _required_controller(self._auto_controller, "_auto_controller")

    def _ensure_shelly_io_controller(self) -> ShellyIoController:
        if not hasattr(self, "_shelly_io_controller") or self._shelly_io_controller is None:
            self._shelly_io_controller = ShellyIoController(require_shelly_io_host(self))
        return _required_controller(self._shelly_io_controller, "_shelly_io_controller")

    def _ensure_state_controller(self) -> ServiceStateController:
        if not hasattr(self, "_state_controller") or self._state_controller is None:
            self._state_controller = ServiceStateController(self, self._normalize_mode_func)
        return _required_controller(self._state_controller, "_state_controller")

    def _ensure_write_controller(self) -> DbusWriteController:
        if not hasattr(self, "_write_controller") or self._write_controller is None:
            self._write_controller = DbusWriteController(WriteControllerPort(self))
        return _required_controller(self._write_controller, "_write_controller")

    def _ensure_auto_input_supervisor(self) -> AutoInputSupervisor:
        if not hasattr(self, "_auto_input_supervisor") or self._auto_input_supervisor is None:
            self._auto_input_supervisor = AutoInputSupervisor(self)
        return _required_controller(self._auto_input_supervisor, "_auto_input_supervisor")

    def _ensure_runtime_support_controller(self) -> RuntimeSupportController:
        if not hasattr(self, "_runtime_support_controller") or self._runtime_support_controller is None:
            self._runtime_support_controller = RuntimeSupportController(self, self._age_seconds_func, self._health_code_func)
        return _required_controller(self._runtime_support_controller, "_runtime_support_controller")

    def _ensure_dbus_input_controller(self) -> DbusInputController:
        if not hasattr(self, "_dbus_input_controller") or self._dbus_input_controller is None:
            self._dbus_input_controller = DbusInputController(DbusInputPort(self))
        return _required_controller(self._dbus_input_controller, "_dbus_input_controller")

    def _ensure_bootstrap_controller(self) -> ServiceBootstrapController:
        if not hasattr(self, "_bootstrap_controller") or self._bootstrap_controller is None:
            self._bootstrap_controller = ServiceBootstrapController(
                self,
                normalize_phase_func=self._normalize_phase_func,
                normalize_mode_func=self._normalize_mode_func,
                mode_uses_auto_logic_func=self._mode_uses_auto_logic_func,
                month_window_func=self._month_window_func,
                age_seconds_func=self._age_seconds_func,
                health_code_func=self._health_code_func,
                phase_values_func=self._phase_values_func,
                read_version_func=self._read_version_func,
                gobject_module=self._gobject_module,
                script_path=self._script_path_value,
                formatters=self._formatter_bundle,
            )
        return _required_controller(self._bootstrap_controller, "_bootstrap_controller")

    def _ensure_update_controller(self) -> UpdateCycleController:
        if not hasattr(self, "_update_controller") or self._update_controller is None:
            self._update_controller = UpdateCycleController(
                UpdateCyclePort(self),
                self._phase_values_func,
                self._health_code_func,
            )
        return _required_controller(self._update_controller, "_update_controller")

    def _ensure_companion_dbus_bridge(self) -> EnergyCompanionDbusBridge:
        if not hasattr(self, "_companion_dbus_bridge") or self._companion_dbus_bridge is None:
            self._companion_dbus_bridge = EnergyCompanionDbusBridge(self, self._script_path_value)
        return _required_controller(self._companion_dbus_bridge, "_companion_dbus_bridge")
