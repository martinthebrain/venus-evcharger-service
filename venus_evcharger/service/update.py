# SPDX-License-Identifier: GPL-3.0-or-later
"""Update-cycle role for the Venus EV charger service."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.core.return_contracts import (
    require_bool,
    require_dict,
    require_int,
    require_tuple2,
    require_tuple3,
    require_tuple4,
    require_tuple5,
)

from .factory import ServiceControllerFactory


class UpdateCycle(ServiceControllerFactory):
    """Static update-cycle delegations."""

    def _ensure_virtual_state_defaults(self) -> None:
        self._ensure_update_controller().ensure_virtual_state_defaults()

    def _session_state_from_status(
        self,
        status: int,
        current_total_energy: float,
        relay_on: bool,
        now: float,
    ) -> tuple[int, float]:
        return require_tuple2(
            self._ensure_update_controller().session_state_from_status(self, status, current_total_energy, relay_on, now),
            "session_state_from_status",
        )

    def _startstop_display_for_state(self, relay_on: bool) -> int:
        now_func = getattr(self, "_time_now", None)
        raw_now = now_func() if callable(now_func) else time.time()
        now = float(raw_now) if isinstance(raw_now, (int, float)) else time.time()
        return require_int(
            self._ensure_update_controller().startstop_display_for_state(self, relay_on, now),
            "startstop_display_for_state",
        )

    def _phase_energies_for_total(self, current_total_energy: float) -> dict[str, float]:
        return require_dict(
            self._ensure_update_controller().phase_energies_for_total(self, current_total_energy),
            "phase_energies_for_total",
        )

    def _publish_virtual_state_paths(
        self,
        current_total_energy: float,
        charging_time: int,
        session_energy: float,
        startstop_display: int,
        now: float,
    ) -> bool:
        return require_bool(
            self._ensure_update_controller().publish_virtual_state_paths(
                current_total_energy,
                charging_time,
                session_energy,
                startstop_display,
                now,
            ),
            "publish_virtual_state_paths",
        )

    def _update_virtual_state(self, status: int, current_total_energy: float, relay_on: bool) -> bool:
        return require_bool(
            self._ensure_update_controller().update_virtual_state(status, current_total_energy, relay_on),
            "update_virtual_state",
        )

    def _prepare_update_cycle(self, now: float) -> Any:
        return self._ensure_update_controller().prepare_update_cycle(self, now)

    def _resolve_pm_status_for_update(self, worker_snapshot: dict[str, Any], now: float) -> Any:
        return self._ensure_update_controller().resolve_pm_status_for_update(self, worker_snapshot, now)

    def _publish_offline_update(self, now: float) -> bool:
        return require_bool(self._ensure_update_controller().publish_offline_update(now), "publish_offline_update")

    def _extract_pm_measurements(self, pm_status: dict[str, Any]) -> tuple[bool, float, float, float, float]:
        return require_tuple5(
            self._ensure_update_controller().extract_pm_measurements(self, pm_status),
            "extract_pm_measurements",
        )

    def _resolve_cached_input_value(
        self,
        value: float | None,
        snapshot_at: float | None,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        max_age_seconds: float | None = None,
    ) -> tuple[float | None, bool]:
        return require_tuple2(
            self._ensure_update_controller().resolve_cached_input_value(
                self,
                value,
                snapshot_at,
                last_value_attr,
                last_at_attr,
                now,
                max_age_seconds=max_age_seconds,
            ),
            "resolve_cached_input_value",
        )

    def _resolve_auto_inputs(
        self,
        worker_snapshot: dict[str, Any],
        now: float,
        auto_mode_active: bool,
    ) -> tuple[float | None, float | None, float | None]:
        return require_tuple3(
            self._ensure_update_controller().resolve_auto_inputs(worker_snapshot, now, auto_mode_active),
            "resolve_auto_inputs",
        )

    def _log_auto_relay_change(self, desired_relay: bool) -> None:
        self._ensure_update_controller().log_auto_relay_change(self, desired_relay)

    def _apply_relay_decision(
        self,
        desired_relay: bool,
        relay_on: bool,
        pm_status: dict[str, Any],
        power: float,
        current: float,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        return require_tuple4(
            self._ensure_update_controller().apply_relay_decision(
                desired_relay,
                relay_on,
                pm_status,
                power,
                current,
                now,
                auto_mode_active,
            ),
            "apply_relay_decision",
        )

    def _derive_status_code(self, relay_on: bool, power: float, auto_mode_active: bool) -> int:
        return require_int(
            self._ensure_update_controller().derive_status_code(self, relay_on, power, auto_mode_active),
            "derive_status_code",
        )

    def _publish_online_update(
        self,
        pm_status: dict[str, Any],
        status: int,
        energy_forward: float,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
    ) -> None:
        self._ensure_update_controller().publish_online_update(pm_status, status, energy_forward, relay_on, power, voltage, now)

    def _complete_update_cycle(
        self,
        changed: bool,
        now: float,
        relay_on: bool,
        power: float,
        current: float,
        status: int,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> bool:
        self._ensure_update_controller().complete_update_cycle(
            self,
            changed,
            now,
            relay_on,
            power,
            current,
            status,
            pv_power,
            battery_soc,
            grid_power,
        )
        return bool(changed)

    def _sign_of_life(self) -> bool:
        return require_bool(self._ensure_update_controller().sign_of_life(), "sign_of_life")

    def _update(self) -> bool:
        return require_bool(self._ensure_update_controller().update(), "update")
