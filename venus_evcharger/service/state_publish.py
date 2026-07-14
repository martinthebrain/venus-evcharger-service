# SPDX-License-Identifier: GPL-3.0-or-later
"""State and DBus-publish roles for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.return_contracts import require_bool, require_dict, require_str
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.runtime import RuntimeSupportController

from .runtime import RuntimeHelper


class StatePublish(RuntimeHelper):
    """Static state and DBus publish delegations."""

    @staticmethod
    def _config_path() -> str:
        config_path: str = ServiceStateController.config_path()
        return config_path

    @staticmethod
    def _coerce_runtime_int(value: Any, default: int = 0) -> int:
        coerced_value: int = ServiceStateController.coerce_runtime_int(value, default)
        return coerced_value

    @staticmethod
    def _coerce_runtime_float(value: Any, default: float = 0.0) -> float:
        coerced_value: float = ServiceStateController.coerce_runtime_float(value, default)
        return coerced_value

    @staticmethod
    def _empty_worker_snapshot() -> dict[str, Any]:
        return RuntimeSupportController.empty_worker_snapshot()

    @staticmethod
    def _clone_worker_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return RuntimeSupportController.clone_worker_snapshot(snapshot)

    @staticmethod
    def _observability_state_defaults() -> dict[str, Any]:
        return RuntimeSupportController.observability_state_defaults()

    def _state_summary(self) -> str:
        return require_str(self._ensure_state_controller().state_summary(), "state_summary")

    def _current_runtime_state(self) -> dict[str, Any]:
        return require_dict(self._ensure_state_controller().current_runtime_state(), "current_runtime_state")

    def _load_runtime_state(self) -> None:
        self._ensure_state_controller().load_runtime_state()

    def _save_runtime_state(self) -> None:
        self._ensure_state_controller().save_runtime_state()

    def _save_runtime_overrides(self) -> None:
        self._ensure_state_controller().save_runtime_overrides()

    def _flush_runtime_overrides(self, now: float | None = None) -> None:
        self._ensure_state_controller().flush_runtime_overrides(now)

    def _validate_runtime_config(self) -> None:
        self._ensure_state_controller().validate_runtime_config()

    def _load_config(self) -> Any:
        return self._ensure_state_controller().load_config()

    def _ensure_dbus_publish_state(self) -> None:
        self._ensure_dbus_publisher().ensure_state()

    def _publish_dbus_field(self, field: str, value: Any, current_time: float | None, force: bool = False) -> bool:
        return require_bool(
            self._ensure_dbus_publisher().publish_field(field, value, current_time, force=force),
            "publish_field",
        )

    def _bump_update_index(self, current_time: float | None) -> None:
        self._ensure_dbus_publisher().bump_update_index(current_time)

    def _publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: Mapping[str, dict[str, float]],
        now: float | None,
    ) -> bool:
        return require_bool(
            self._ensure_dbus_publisher().publish_live_measurements(
                power,
                voltage,
                total_current,
                dict(phase_data),
                now,
            ),
            "publish_live_measurements",
        )

    def _publish_energy_time_measurements(
        self,
        current_total_energy: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        return require_bool(
            self._ensure_dbus_publisher().publish_energy_time_measurements(
                current_total_energy,
                phase_energies,
                charging_time,
                session_energy,
                now,
            ),
            "publish_energy_time_measurements",
        )

    def _publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        return require_bool(
            self._ensure_dbus_publisher().publish_config_paths(startstop_display, now),
            "publish_config_paths",
        )

    def _publish_diagnostic_paths(self, now: float) -> bool:
        return require_bool(self._ensure_dbus_publisher().publish_diagnostic_paths(now), "publish_diagnostic_paths")

    def _start_companion_dbus_bridge(self) -> None:
        self._ensure_companion_dbus_bridge().start()

    def _stop_companion_dbus_bridge(self) -> None:
        self._ensure_companion_dbus_bridge().stop()

    def _publish_companion_dbus_bridge(self, now: float | None = None) -> bool:
        self._ensure_companion_dbus_bridge()
        if not self._dbus_publish_direct_allowed():
            return require_bool(self._enqueue_companion_dbus_publish(now), "_enqueue_companion_dbus_publish")
        return require_bool(self._ensure_companion_dbus_bridge().publish(now), "companion_dbus_bridge.publish")
