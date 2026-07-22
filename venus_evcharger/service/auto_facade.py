# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit Auto and control-command boundary for the wallbox service."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.control.models import ControlCommandName, ControlCommandSource

from .composition_contracts import ControllerOwnerPort
CommandEventPublisher = Callable[[ControlCommand, ControlResult], None]


class ServiceAutoFacade:
    """Coordinate Auto decisions and canonical control commands without MRO hooks."""

    def __init__(
        self,
        controllers: ControllerOwnerPort,
        publish_command_event: CommandEventPublisher,
    ) -> None:
        self._controllers = controllers
        self._publish_command_event = publish_command_event

    def mode_uses_auto_logic(self, mode: object) -> bool:
        return self._controllers.functions.mode_uses_auto_logic(mode)

    def normalize_mode(self, value: object) -> int:
        return self._controllers.functions.normalize_mode(value)

    def clear_samples(self) -> None:
        self._controllers.runtime.auto.clear_auto_samples()

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        self._controllers.runtime.auto.mark_relay_changed(relay_on, now)

    def set_health(self, reason: str, cached: bool = False) -> None:
        self._controllers.runtime.auto.set_health(reason, cached)

    def decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> bool:
        return self._controllers.runtime.auto.auto_decide_relay(
            relay_on,
            pv_power,
            battery_soc,
            grid_power,
        )

    def command(
        self,
        name: ControlCommandName,
        target: str,
        value: object,
        source: ControlCommandSource = "internal",
    ) -> ControlCommand:
        return self._controllers.runtime.write.build_control_command(name, target, value, source=source)

    def command_from_payload(
        self,
        payload: Mapping[str, object],
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        return self._controllers.runtime.write.build_control_command_from_payload(dict(payload), source=source)

    def handle_command(self, command: ControlCommand) -> ControlResult:
        result = self._controllers.runtime.write.handle_control_command(command)
        self._publish_command_event(command, result)
        return result
