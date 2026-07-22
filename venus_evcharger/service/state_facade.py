# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit state and publish boundary for the wallbox service."""

from __future__ import annotations

import configparser
from collections.abc import Callable, Mapping

from venus_evcharger.controllers.state import ServiceStateController

from .composition_contracts import ControllerOwnerPort
from .runtime_facade import ServiceRuntimeFacade


_CONFIG_PATH: Callable[[], str] = ServiceStateController.config_path


class ServiceStateFacade:
    """Coordinate state persistence and DBus publication without inheritance."""

    def __init__(self, controllers: ControllerOwnerPort, runtime: ServiceRuntimeFacade) -> None:
        self._controllers = controllers
        self._runtime = runtime

    @staticmethod
    def config_path() -> str:
        return _CONFIG_PATH()

    def summary(self) -> str:
        return self._controllers.state.state_summary()

    def current(self) -> dict[str, object]:
        return self._controllers.state.current_runtime_state()

    def load_runtime_state(self) -> None:
        self._controllers.state.load_runtime_state()

    def save_runtime_state(self) -> None:
        self._controllers.state.save_runtime_state()

    def save_runtime_overrides(self) -> None:
        self._controllers.state.save_runtime_overrides()

    def flush_runtime_overrides(self, now: float | None = None) -> None:
        self._controllers.state.flush_runtime_overrides(now)

    def validate_runtime_config(self) -> None:
        self._controllers.state.validate_runtime_config()

    def load_config(self) -> configparser.ConfigParser:
        return self._controllers.state.load_config()

    def ensure_publish_state(self) -> None:
        self._controllers.runtime.publisher.ensure_state()

    def publish_field(self, field: str, value: object, now: float | None, *, force: bool = False) -> bool:
        return self._controllers.runtime.publisher.publish_field(field, value, now, force=force)

    def last_accepted_field(self, field: str) -> object:
        return self._controllers.runtime.publisher.last_accepted_field(field)

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: Mapping[str, dict[str, float]],
        now: float | None,
    ) -> bool:
        return self._controllers.runtime.publisher.publish_live_measurements(
            power,
            voltage,
            total_current,
            dict(phase_data),
            now,
        )

    def publish_energy_time_measurements(
        self,
        total_energy: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        return self._controllers.runtime.publisher.publish_energy_time_measurements(
            total_energy,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )

    def publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        return self._controllers.runtime.publisher.publish_config_paths(startstop_display, now)

    def publish_diagnostic_paths(self, now: float) -> bool:
        return self._controllers.runtime.publisher.publish_diagnostic_paths(now)

    def start_companion_bridge(self) -> None:
        self._controllers.runtime.companion.start()

    def stop_companion_bridge(self) -> None:
        self._controllers.runtime.companion.stop()

    def publish_companion_bridge(self, now: float | None = None) -> bool:
        return self._controllers.runtime.companion.publish(now)
