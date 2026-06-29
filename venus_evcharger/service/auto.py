# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-input and Auto-logic roles for the Venus EV charger service."""

from __future__ import annotations

from typing import Any

from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.core.return_contracts import (
    require_bool,
    require_dict,
    require_float,
    require_float_or_none,
    require_instance,
    require_none,
    require_str_list,
    require_str_or_none,
)
from venus_evcharger.controllers.auto import AutoDecisionController

from .update import UpdateCycle


class DbusAutoLogic(UpdateCycle):
    """Static DBus-input, Auto-decision, and write-controller delegations."""

    @staticmethod
    def _get_available_surplus_watts(pv_power: float, grid_power: float) -> float:
        available_surplus: float = AutoDecisionController.get_available_surplus_watts(pv_power, grid_power)
        return available_surplus

    def _mode_uses_auto_logic(self, mode: int) -> bool:
        return self._mode_uses_auto_logic_func(mode)

    def _normalize_mode(self, value: Any) -> int:
        return self._normalize_mode_func(value)

    def _get_dbus_value(self, service_name: str, object_path: str) -> Any:
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.get_dbus_value(service_name, object_path)

    def _list_dbus_services(self) -> list[str]:
        self._ensure_dbus_input_controller()
        return require_str_list(self._dbus_input_controller.list_dbus_services(), "list_dbus_services")

    def _invalidate_auto_pv_services(self) -> None:
        self._ensure_dbus_input_controller()
        self._dbus_input_controller.invalidate_auto_pv_services()

    def _invalidate_auto_battery_service(self) -> None:
        self._ensure_dbus_input_controller()
        self._dbus_input_controller.invalidate_auto_battery_service()

    def _resolve_auto_pv_services(self) -> list[str]:
        self._ensure_dbus_input_controller()
        return require_str_list(self._dbus_input_controller.resolve_auto_pv_services(), "resolve_auto_pv_services")

    def _get_pv_power(self) -> float | None:
        self._ensure_dbus_input_controller()
        return require_float_or_none(self._dbus_input_controller.get_pv_power(), "get_pv_power")

    def _resolve_auto_battery_service(self) -> str | None:
        self._ensure_dbus_input_controller()
        return require_str_or_none(
            self._dbus_input_controller.resolve_auto_battery_service(),
            "resolve_auto_battery_service",
        )

    def _get_battery_soc(self) -> float | None:
        self._ensure_dbus_input_controller()
        return require_float_or_none(self._dbus_input_controller.get_battery_soc(), "get_battery_soc")

    def _get_grid_power(self) -> float | None:
        self._ensure_dbus_input_controller()
        return require_float_or_none(self._dbus_input_controller.get_grid_power(), "get_grid_power")

    def _add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> None:
        self._ensure_auto_controller()
        self._auto_controller.add_auto_sample(now, surplus_power, grid_power)

    def _clear_auto_samples(self) -> None:
        self._ensure_auto_controller()
        self._auto_controller.clear_auto_samples()

    def _average_auto_metric(self, index: int) -> float:
        self._ensure_auto_controller()
        return require_float(self._auto_controller.average_auto_metric(index), "average_auto_metric")

    def _mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        self._ensure_auto_controller()
        self._auto_controller.mark_relay_changed(relay_on, now)

    def _is_within_auto_daytime_window(self, current_dt: Any = None) -> bool:
        self._ensure_auto_controller()
        return require_bool(self._auto_controller.is_within_auto_daytime_window(current_dt), "is_within_auto_daytime_window")

    def _set_health(self, reason: str, cached: bool = False) -> None:
        self._ensure_auto_controller()
        return require_none(self._auto_controller.set_health(reason, cached), "set_health")

    def _auto_decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> bool:
        self._ensure_auto_controller()
        return require_bool(
            self._auto_controller.auto_decide_relay(relay_on, pv_power, battery_soc, grid_power),
            "auto_decide_relay",
        )

    def _control_command_from_write(self, path: str, value: Any, source: str = "dbus") -> ControlCommand:
        self._ensure_write_controller()
        return require_instance(
            self._write_controller.build_control_command(path, value, source=source),
            "build_control_command",
            ControlCommand,
        )

    def _handle_control_command(self, command: ControlCommand) -> ControlResult:
        self._ensure_write_controller()
        result = require_instance(
            self._write_controller.handle_control_command(command),
            "handle_control_command",
            ControlResult,
        )
        publish_event = getattr(self, "_publish_control_api_command_event", None)
        if callable(publish_event):
            publish_event(command, result)
        return result

    def _handle_write(self, path: str, value: Any) -> bool:
        command = self._control_command_from_write(path, value, source="dbus")
        enqueue_command = getattr(self, "_enqueue_control_command", None)
        if callable(enqueue_command) and bool(getattr(self, "_control_command_async_enabled", False)):
            return require_bool(enqueue_command(command), "enqueue_control_command")
        result = self._handle_control_command(command)
        return bool(result.accepted)

    def _register_paths(self) -> None:
        self._ensure_bootstrap_controller()
        self._bootstrap_controller.register_paths()

    def _fetch_device_info_with_fallback(self) -> dict[str, Any]:
        self._ensure_bootstrap_controller()
        return require_dict(self._bootstrap_controller.fetch_device_info_with_fallback(), "fetch_device_info_with_fallback")
