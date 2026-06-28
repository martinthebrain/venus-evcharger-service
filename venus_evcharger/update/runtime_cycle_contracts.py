# SPDX-License-Identifier: GPL-3.0-or-later
"""Type-only contracts for the update runtime-cycle orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _UpdateCycleRuntimeContractsMixin:
    """Declare sibling-mixin helpers used by the online update-cycle runner."""

    service: Any

    if TYPE_CHECKING:  # pragma: no cover

        def prepare_update_cycle(self, svc: Any, now: float) -> dict[str, Any]: ...

        def resolve_pm_status_for_update(
            self,
            svc: Any,
            worker_snapshot: dict[str, Any],
            now: float,
        ) -> dict[str, Any] | None: ...

        def publish_offline_update(self, now: float) -> bool: ...

        def resolve_auto_inputs(
            self,
            worker_snapshot: dict[str, Any],
            now: float,
            auto_mode_active: bool,
        ) -> tuple[Any, Any, Any]: ...

        def apply_victron_ess_balance_bias(self, svc: Any, now: float, auto_mode_active: bool) -> None: ...

        def apply_relay_decision(
            self,
            desired_relay: bool,
            relay_on: bool,
            pm_status: dict[str, Any],
            power: float,
            current: float,
            now: float,
            auto_mode_active: bool,
        ) -> tuple[bool, float, float, bool]: ...

        def publish_online_update(
            self,
            pm_status: dict[str, Any],
            status: int,
            energy_forward: float,
            relay_on: bool,
            power: float,
            voltage: float,
            now: float,
        ) -> bool: ...

        def update_learned_charge_power(
            self,
            relay_on: bool,
            status: int,
            power: float,
            voltage: float,
            now: float,
            pm_confirmed: bool = True,
        ) -> bool: ...

        def save_runtime_state_best_effort(self, reason: str) -> None: ...

        def orchestrate_pending_phase_switch(
            self,
            pm_status: dict[str, Any],
            relay_on: bool,
            power: float,
            current: float,
            pm_confirmed: bool,
            now: float,
            auto_mode_active: bool,
        ) -> tuple[bool, float, float, bool, bool | None]: ...

        def maybe_apply_auto_phase_selection(
            self,
            svc: Any,
            desired_relay: bool,
            relay_on: bool,
            voltage: float,
            now: float,
            auto_mode_active: bool,
        ) -> bool | None: ...

        def apply_charger_current_target(self, svc: Any, desired_relay: bool, now: float, auto_mode_active: bool) -> None: ...

        def charger_health_override(self, svc: Any, now: float | None = None) -> str | None: ...

        @classmethod
        def switch_feedback_health_override(
            cls,
            svc: Any,
            desired_relay: bool,
            relay_on: bool,
            now: float | None = None,
            *,
            power: float | None = None,
            current: float | None = None,
            pm_confirmed: bool = False,
        ) -> str | None: ...

        def _fresh_charger_power_readback(self, svc: Any, now: float | None = None) -> float | None: ...

        @classmethod
        def derive_status_code(
            cls,
            svc: Any,
            relay_on: bool,
            power: float,
            auto_mode_active: bool,
            now: float | None = None,
            health_reason: str | None = None,
        ) -> int: ...

        def extract_pm_measurements(self, svc: Any, pm_status: dict[str, Any]) -> tuple[bool, float, float, float, float]: ...

        def apply_startup_manual_target(self, pm_status: dict[str, Any], now: float) -> dict[str, Any]: ...

        def _pm_status_confirmed(self, pm_status: dict[str, Any]) -> bool: ...

        def refresh_learned_charge_power_state(self, now: float) -> bool: ...

        def reconcile_learned_charge_power_signature(
            self,
            relay_on: bool,
            power: float,
            voltage: float,
            now: float,
            pm_confirmed: bool = True,
        ) -> bool: ...

        def relay_sync_health_override(self, relay_on: bool, pm_confirmed: bool, now: float) -> str | None: ...
