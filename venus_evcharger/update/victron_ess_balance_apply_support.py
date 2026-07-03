# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed support role for Victron ESS balance-bias application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .victron_ess_balance_apply_write import _UpdateCycleVictronEssBalanceApplyWrite


class _UpdateCycleVictronEssBalanceApplySupport(_UpdateCycleVictronEssBalanceApplyWrite):
    """Bundle source selection, PID computation, and gateway writes for the apply role."""

    if TYPE_CHECKING:  # pragma: no cover

        def _victron_ess_balance_current_topology_key(self, svc: Any, source_id: str) -> str: ...

        def _victron_ess_balance_learning_profile(
            self,
            svc: Any,
            cluster: dict[str, Any],
            source: dict[str, Any],
            source_error_w: float,
        ) -> dict[str, str]: ...

        def _set_victron_ess_balance_active_profile(self, svc: Any, learning_profile: dict[str, str]) -> None: ...

        def _clear_victron_ess_balance_active_profile(self, svc: Any) -> None: ...

        def _merge_victron_ess_balance_learning_profile_metrics(
            self,
            svc: Any,
            metrics: dict[str, Any],
            profile_key: str,
        ) -> None: ...

        def _victron_ess_balance_refresh_stable_tuning(
            self,
            svc: Any,
            metrics: dict[str, Any],
            now: float,
        ) -> None: ...

        def _victron_ess_balance_note_action_direction(self, svc: Any, action_direction: str, now: float) -> int: ...

        def _populate_victron_ess_balance_runtime_safety_metrics(
            self,
            svc: Any,
            now: float,
            metrics: dict[str, Any],
        ) -> None: ...

        def _victron_ess_balance_overshoot_cooldown_active(self, svc: Any, now: float) -> bool: ...

        def _victron_ess_balance_oscillation_lockout_active(self, svc: Any, now: float) -> bool: ...

        def _maybe_restore_victron_ess_balance_stable_tuning(
            self,
            svc: Any,
            metrics: dict[str, Any],
            reason: str,
        ) -> bool: ...

        def _update_victron_ess_balance_telemetry(
            self,
            svc: Any,
            now: float,
            cluster: dict[str, Any],
            source_error_w: float,
            metrics: dict[str, Any],
            profile_key: str,
        ) -> None: ...

        def _populate_victron_ess_balance_telemetry_metrics(self, svc: Any, metrics: dict[str, Any]) -> None: ...

        def _maybe_auto_apply_victron_ess_balance_recommendation(
            self,
            svc: Any,
            metrics: dict[str, Any],
            now: float,
        ) -> None: ...
