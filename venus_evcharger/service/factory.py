# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy controller factory mixin for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from venus_evcharger.bootstrap.controller import ServiceBootstrapController
from venus_evcharger.backend.shelly_io import ShellyIoController
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

if TYPE_CHECKING:
    from venus_evcharger.backend.shelly_io import ShellyIoHost


class ServiceControllerFactoryMixin:
    """Lazy controller builders shared by the wallbox service mixins."""

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
    _dbus_publisher: Any = None
    _auto_controller: Any = None
    _shelly_io_controller: Any = None
    _state_controller: Any = None
    _write_controller: Any = None
    _auto_input_supervisor: Any = None
    _runtime_support_controller: Any = None
    _dbus_input_controller: Any = None
    _bootstrap_controller: Any = None
    _update_controller: Any = None
    _companion_dbus_bridge: Any = None

    def _ensure_dbus_publisher(self) -> None:
        if not hasattr(self, "_dbus_publisher") or self._dbus_publisher is None:
            self._dbus_publisher = DbusPublishController(self, self._age_seconds_func)

    def _ensure_auto_controller(self) -> None:
        if not hasattr(self, "_auto_controller") or self._auto_controller is None:
            self._auto_controller = AutoDecisionController(
                AutoDecisionPort(self),
                self._health_code_func,
                self._mode_uses_auto_logic_func,
            )

    def _ensure_shelly_io_controller(self) -> None:
        if not hasattr(self, "_shelly_io_controller") or self._shelly_io_controller is None:
            self._shelly_io_controller = ShellyIoController(cast("ShellyIoHost", self))

    def _ensure_state_controller(self) -> None:
        if not hasattr(self, "_state_controller") or self._state_controller is None:
            self._state_controller = ServiceStateController(self, self._normalize_mode_func)

    def _ensure_write_controller(self) -> None:
        if not hasattr(self, "_write_controller") or self._write_controller is None:
            self._write_controller = DbusWriteController(WriteControllerPort(self))

    def _ensure_auto_input_supervisor(self) -> None:
        if not hasattr(self, "_auto_input_supervisor") or self._auto_input_supervisor is None:
            self._auto_input_supervisor = AutoInputSupervisor(self)

    def _ensure_runtime_support_controller(self) -> None:
        if not hasattr(self, "_runtime_support_controller") or self._runtime_support_controller is None:
            self._runtime_support_controller = RuntimeSupportController(self, self._age_seconds_func, self._health_code_func)

    def _ensure_dbus_input_controller(self) -> None:
        if not hasattr(self, "_dbus_input_controller") or self._dbus_input_controller is None:
            self._dbus_input_controller = DbusInputController(DbusInputPort(self))

    def _ensure_bootstrap_controller(self) -> None:
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

    def _ensure_update_controller(self) -> None:
        if not hasattr(self, "_update_controller") or self._update_controller is None:
            self._update_controller = UpdateCycleController(
                UpdateCyclePort(self),
                self._phase_values_func,
                self._health_code_func,
            )

    def _ensure_companion_dbus_bridge(self) -> None:
        if not hasattr(self, "_companion_dbus_bridge") or self._companion_dbus_bridge is None:
            self._companion_dbus_bridge = EnergyCompanionDbusBridge(self, self._script_path_value)
