# SPDX-License-Identifier: GPL-3.0-or-later
"""Type-only contracts for split relay phase mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.backend.models import PhaseSelection


class _RelayPhaseSwitchPolicyContractsMixin:
    """Declare sibling helpers used by phase-switch policy orchestration."""

    if TYPE_CHECKING:  # pragma: no cover
        PHASE_SWITCH_WAITING_STATE: str

        @classmethod
        def _phase_selection_is_upshift(
            cls,
            current_selection: PhaseSelection,
            target_selection: PhaseSelection,
        ) -> bool: ...

        @classmethod
        def _phase_selection_count(cls, selection: object) -> int: ...

        @staticmethod
        def _auto_phase_policy(svc: Any) -> Any | None: ...

        @classmethod
        def _phase_selection_min_surplus_watts(
            cls,
            svc: Any,
            selection: PhaseSelection,
            voltage: float,
        ) -> float | None: ...

        @classmethod
        def _ordered_auto_phase_selections(cls, svc: Any) -> tuple[PhaseSelection, ...]: ...

        @classmethod
        def _current_phase_selection(
            cls,
            svc: Any,
            supported: tuple[PhaseSelection, ...],
        ) -> PhaseSelection: ...

        @classmethod
        def _auto_phase_target_selection(
            cls,
            svc: Any,
            supported: tuple[PhaseSelection, ...],
            current_selection: PhaseSelection,
            desired_relay: bool,
            relay_on: bool,
            voltage: float,
            now: float,
        ) -> tuple[PhaseSelection | None, str, float | None]: ...

        @classmethod
        def _record_auto_phase_metrics(
            cls,
            svc: Any,
            *,
            current_selection: PhaseSelection,
            target_selection: PhaseSelection | None,
            phase_reason: str,
            threshold_watts: float | None,
        ) -> None: ...

        @staticmethod
        def _pending_phase_switch_selection(svc: Any) -> PhaseSelection | None: ...

        def _phase_switch_state_active(self, pending_selection: PhaseSelection | None, switch_state: str) -> bool: ...

        def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None: ...
