# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-switch retry, lockout, and staging helpers for the update cycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection
from venus_evcharger.core.common import fresh_confirmed_relay_output
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_phase_switch_mismatch import _RelayPhaseSwitchMismatch


PHASE_SELECTION_APPLY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)

# Safety invariants for this policy:
# - Phase changes never bypass an active pause/staging requirement.
# - Mismatch retry state only affects upshifts; downshifts stay available for safety.
# - Lockout state is scoped to the normalized phase target that caused it.
# - Pending Auto candidates must survive their configured delay before applying.
# - Failed physical switch attempts clear pending candidates and mark Shelly health.
# - Runtime state is persisted after staged or applied phase target changes.
# - Auto mode deactivation clears any pending candidate before returning.


class _AutoPhasePolicyLike(Protocol):
    upshift_delay_seconds: float
    downshift_delay_seconds: float
    downshift_margin_watts: float
    mismatch_retry_seconds: float
    mismatch_lockout_count: int
    mismatch_lockout_seconds: float


class _AutoPhaseSwitchService(Protocol):
    requested_phase_selection: PhaseSelection
    active_phase_selection: PhaseSelection
    auto_shelly_soft_fail_seconds: float
    _phase_switch_mismatch_active: bool
    _phase_switch_mismatch_counts: dict[str, int]
    _phase_switch_last_mismatch_selection: str | None
    _phase_switch_last_mismatch_at: float | None
    _phase_switch_lockout_selection: str | None
    _phase_switch_lockout_reason: str
    _phase_switch_lockout_at: float | None
    _phase_switch_lockout_until: float | None
    _auto_phase_target_candidate: PhaseSelection | None
    _auto_phase_target_since: float | None
    _phase_switch_pending_selection: PhaseSelection | None
    _phase_switch_state: str | None
    _phase_switch_requested_at: float | None
    _phase_switch_stable_until: float | None
    _phase_switch_resume_relay: bool
    _peek_pending_relay_command: Callable[[], tuple[bool | None, float | None]]
    _phase_selection_requires_pause: Callable[[], bool]
    _apply_phase_selection: Callable[[PhaseSelection], PhaseSelection]
    _save_runtime_state: Callable[[], object]
    _mark_failure: Callable[[str], object]
    _warning_throttled: Callable[..., object]


class _RelayPhaseSwitchPolicy(_RelayPhaseSwitchMismatch):
    """Handle phase-switch cooldowns, lockouts, and pending Auto candidates."""

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
        def _auto_phase_policy(svc: object) -> _AutoPhasePolicyLike | None: ...

        @classmethod
        def _phase_selection_min_surplus_watts(
            cls,
            svc: object,
            selection: PhaseSelection,
            voltage: float,
        ) -> float | None: ...

        @classmethod
        def _ordered_auto_phase_selections(cls, svc: object) -> tuple[PhaseSelection, ...]: ...

        @classmethod
        def _current_phase_selection(
            cls,
            svc: object,
            supported: tuple[PhaseSelection, ...],
        ) -> PhaseSelection: ...

        @classmethod
        def _auto_phase_target_selection(
            cls,
            svc: object,
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
            svc: object,
            *,
            current_selection: PhaseSelection,
            target_selection: PhaseSelection | None,
            phase_reason: str,
            threshold_watts: float | None,
        ) -> None: ...

        @staticmethod
        def _pending_phase_switch_selection(svc: object) -> PhaseSelection | None: ...

        def _phase_switch_state_active(self, pending_selection: PhaseSelection | None, switch_state: str) -> bool: ...

        def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None: ...

    @classmethod
    def _downshift_auto_phase_target(
        cls,
        svc: object,
        phase_policy: _AutoPhasePolicyLike,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        current_index: int,
        surplus_watts: float,
        voltage: float,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        if current_index <= 0:
            return None
        current_min_surplus = cls._phase_selection_min_surplus_watts(svc, current_selection, voltage)
        if current_min_surplus is None:
            return None
        threshold = max(
            0.0,
            current_min_surplus - float(getattr(phase_policy, "downshift_margin_watts", 150.0)),
        )
        if surplus_watts >= threshold:
            return None
        return supported[current_index - 1], "phase-downshift", threshold

    @staticmethod
    def _clear_auto_phase_candidate(svc: _AutoPhaseSwitchService) -> None:
        svc._auto_phase_target_candidate = None
        svc._auto_phase_target_since = None

    @classmethod
    def _auto_phase_switch_delay_seconds(
        cls,
        svc: object,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
    ) -> float:
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is None:
            return 0.0
        if cls._phase_selection_count(target_selection) > cls._phase_selection_count(current_selection):
            return max(0.0, float(getattr(phase_policy, "upshift_delay_seconds", 120.0)))
        return max(0.0, float(getattr(phase_policy, "downshift_delay_seconds", 30.0)))

    @classmethod
    def _auto_phase_candidate_ready(
        cls,
        svc: _AutoPhaseSwitchService,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
    ) -> bool:
        candidate = getattr(svc, "_auto_phase_target_candidate", None)
        if candidate != target_selection:
            svc._auto_phase_target_candidate = target_selection
            svc._auto_phase_target_since = float(now)
            return False
        candidate_since = finite_float_or_none(getattr(svc, "_auto_phase_target_since", None))
        if candidate_since is None:
            svc._auto_phase_target_since = float(now)
            return False
        return (float(now) - candidate_since) >= cls._auto_phase_switch_delay_seconds(
            svc,
            current_selection,
            target_selection,
        )

    @classmethod
    def _stage_phase_switch(
        cls,
        svc: _AutoPhaseSwitchService,
        requested_selection: PhaseSelection,
        current_time: float,
        *,
        resume_relay: bool,
    ) -> None:
        svc.requested_phase_selection = requested_selection
        svc._phase_switch_pending_selection = requested_selection
        svc._phase_switch_state = cls.PHASE_SWITCH_WAITING_STATE
        svc._phase_switch_requested_at = current_time
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = bool(resume_relay)

    @classmethod
    def _phase_change_requires_staging(
        cls,
        svc: _AutoPhaseSwitchService,
        relay_on: bool,
        now: float,
    ) -> bool:
        requires_pause_func = getattr(svc, "_phase_selection_requires_pause", None)
        requires_pause = bool(requires_pause_func()) if requires_pause_func is not None else False
        if not requires_pause:
            return False
        pending_relay_state, _requested_at = svc._peek_pending_relay_command()
        if pending_relay_state is not None:
            return True
        confirmed_output = fresh_confirmed_relay_output(svc, now)
        if confirmed_output is not None:
            return bool(confirmed_output)
        return bool(relay_on)

    def _apply_auto_phase_target(
        self,
        svc: _AutoPhaseSwitchService,
        target_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
        now: float,
    ) -> bool | None:
        if self._phase_change_requires_staging(svc, relay_on, now):
            self._stage_phase_switch(
                svc,
                target_selection,
                now,
                resume_relay=bool(desired_relay),
            )
            svc._save_runtime_state()
            self._publish_local_pm_status_best_effort(False, now)
            self._clear_auto_phase_candidate(svc)
            return False
        try:
            applied_selection = svc._apply_phase_selection(target_selection)
        except PHASE_SELECTION_APPLY_ERRORS as error:
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "auto-phase-switch-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to apply Auto phase selection %s: %s",
                target_selection,
                error,
                exc_info=error,
            )
            self._clear_auto_phase_candidate(svc)
            return None
        svc.requested_phase_selection = applied_selection
        svc.active_phase_selection = applied_selection
        self._clear_phase_switch_mismatch_tracking(svc, applied_selection)
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_selection is not None and normalize_phase_selection(lockout_selection) == applied_selection:
            self._clear_phase_switch_lockout(svc)
        svc._save_runtime_state()
        self._clear_auto_phase_candidate(svc)
        return None

    def maybe_apply_auto_phase_selection(
        self,
        svc: _AutoPhaseSwitchService,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
        auto_mode_active: bool,
    ) -> bool | None:
        if self._auto_phase_selection_blocked(svc, auto_mode_active):
            return None
        current_selection, target_selection, phase_reason, threshold_watts = self._auto_phase_selection_decision(
            svc,
            desired_relay,
            relay_on,
            voltage,
            now,
        )
        if target_selection is None or target_selection == current_selection:
            self._clear_auto_phase_candidate(svc)
            return None
        if not self._pending_auto_phase_target_ready(
            svc,
            current_selection,
            target_selection,
            now,
            phase_reason,
            threshold_watts,
        ):
            return None
        return self._apply_auto_phase_target(
            svc,
            target_selection,
            desired_relay,
            relay_on,
            now,
        )

    def _auto_phase_selection_blocked(self, svc: _AutoPhaseSwitchService, auto_mode_active: bool) -> bool:
        return any(
            (
                self._auto_phase_selection_inactive(svc, auto_mode_active),
                self._auto_phase_switch_already_active(svc),
            )
        )

    def _auto_phase_selection_decision(
        self,
        svc: _AutoPhaseSwitchService,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection, PhaseSelection | None, str, float | None]:
        supported = self._ordered_auto_phase_selections(svc)
        current_selection = self._current_phase_selection(svc, supported)
        target_selection, phase_reason, threshold_watts = self._auto_phase_target_selection(
            svc,
            supported,
            current_selection,
            desired_relay,
            relay_on,
            voltage,
            now,
        )
        self._record_auto_phase_metrics(
            svc,
            current_selection=current_selection,
            target_selection=target_selection,
            phase_reason=phase_reason,
            threshold_watts=threshold_watts,
        )
        return current_selection, target_selection, phase_reason, threshold_watts

    def _auto_phase_selection_inactive(self, svc: _AutoPhaseSwitchService, auto_mode_active: bool) -> bool:
        if auto_mode_active:
            return False
        self._clear_auto_phase_candidate(svc)
        return True

    def _auto_phase_switch_already_active(self, svc: _AutoPhaseSwitchService) -> bool:
        pending_selection = self._pending_phase_switch_selection(svc)
        raw_switch_state = svc._phase_switch_state if hasattr(svc, "_phase_switch_state") else None
        switch_state = "" if raw_switch_state is None else str(raw_switch_state)
        return bool(self._phase_switch_state_active(pending_selection, switch_state))

    def _pending_auto_phase_target_ready(
        self,
        svc: _AutoPhaseSwitchService,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
        phase_reason: str,
        threshold_watts: float | None,
    ) -> bool:
        if self._auto_phase_candidate_ready(svc, current_selection, target_selection, now):
            return True
        self._record_auto_phase_metrics(
            svc,
            current_selection=current_selection,
            target_selection=target_selection,
            phase_reason=f"{phase_reason}-pending",
            threshold_watts=threshold_watts,
        )
        return False
