# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto phase-switch staging policy with explicit collaborators."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, phase_selection_count
from venus_evcharger.core.common import fresh_confirmed_relay_output
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_phase_decision import AutoPhaseTargetSelector
from venus_evcharger.update.relay_phase_publish import RelayTelemetryService
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_ports import PhaseSwitchServicePort


PHASE_SELECTION_APPLY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)
PHASE_SWITCH_STATE_SAVE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)

# Safety invariants for this policy:
# - Phase changes never bypass an active pause/staging requirement.
# - Mismatch retry state only affects upshifts; downshifts stay available for safety.
# - Lockout state is scoped to the normalized phase target that caused it.
# - Pending Auto candidates must survive their configured delay before applying.
# - Failed physical switch attempts clear pending candidates and mark Shelly health.
# - Runtime persistence is best-effort after staged or applied phase changes; coherent RAM state remains authoritative.
# - Auto mode deactivation clears any pending candidate before returning.


class PhaseSwitchRuntimeView(Protocol):
    def pending_selection(self, svc: PhaseSwitchServicePort) -> PhaseSelection | None: ...
    def state_active(self, pending_selection: PhaseSelection | None, switch_state: str) -> bool: ...


class LocalPmPublisher(Protocol):
    def publish_local_pm_status_best_effort(
        self,
        svc: RelayTelemetryService,
        relay_on: bool,
        now: float,
    ) -> None: ...


class AutoPhaseSwitchController:
    """Handle phase-switch cooldowns, lockouts, and pending Auto candidates."""

    def __init__(
        self,
        selector: AutoPhaseTargetSelector,
        mismatch: PhaseSwitchMismatchMonitor,
        runtime: PhaseSwitchRuntimeView,
        local_pm: LocalPmPublisher,
        *,
        waiting_state: str,
    ) -> None:
        self._selector = selector
        self._mismatch = mismatch
        self._runtime = runtime
        self._local_pm = local_pm
        self._waiting_state = waiting_state

    @staticmethod
    def _clear_auto_phase_candidate(svc: PhaseSwitchServicePort) -> None:
        svc._auto_phase_target_candidate = None
        svc._auto_phase_target_since = None

    def _auto_phase_switch_delay_seconds(
        self,
        svc: PhaseSwitchServicePort,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
    ) -> float:
        phase_policy = self._selector._auto_phase_policy(svc)
        if phase_selection_count(target_selection) > phase_selection_count(current_selection):
            return max(0.0, float(phase_policy.upshift_delay_seconds))
        return max(0.0, float(phase_policy.downshift_delay_seconds))

    def _auto_phase_candidate_ready(
        self,
        svc: PhaseSwitchServicePort,
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
        return (float(now) - float(candidate_since)) >= self._auto_phase_switch_delay_seconds(
            svc,
            current_selection,
            target_selection,
        )

    def _stage_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        requested_selection: PhaseSelection,
        current_time: float,
        *,
        resume_relay: bool,
    ) -> None:
        svc.requested_phase_selection = requested_selection
        svc._phase_switch_pending_selection = requested_selection
        svc._phase_switch_state = self._waiting_state
        svc._phase_switch_requested_at = current_time
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = bool(resume_relay)

    def _phase_change_requires_staging(
        self,
        svc: PhaseSwitchServicePort,
        relay_on: bool,
        now: float,
    ) -> bool:
        if not svc.runtime.phase_selection_requires_pause():
            return False
        pending_relay_state, _requested_at = svc.runtime.pending_relay_command()
        if pending_relay_state is not None:
            return True
        confirmed_output = fresh_confirmed_relay_output(svc, now)
        if confirmed_output is not None:
            return bool(confirmed_output)
        return bool(relay_on)

    @staticmethod
    def _persist_phase_switch_state_best_effort(
        svc: PhaseSwitchServicePort,
        transition: str,
    ) -> None:
        """Persist a completed transition without invalidating its in-memory state.

        Persistence is advisory after the policy has established a coherent RAM
        state. A later regular runtime-state save therefore provides the retry
        path, while the live phase-switch cycle can continue deterministically.
        """
        try:
            svc.state.save_runtime_state()
        except PHASE_SWITCH_STATE_SAVE_ERRORS as error:
            svc.runtime.warning_throttled(
                f"phase-switch-state-save-failed-{transition}",
                svc.auto_shelly_soft_fail_seconds,
                "Unable to persist phase-switch state after %s; "
                "keeping the completed in-memory transition for a later runtime-state save: %s",
                transition,
                error,
                exc_info=error,
            )
            return

    def _apply_auto_phase_target(
        self,
        svc: PhaseSwitchServicePort,
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
            self._clear_auto_phase_candidate(svc)
            self._persist_phase_switch_state_best_effort(svc, "staging")
            self._local_pm.publish_local_pm_status_best_effort(svc, False, now)
            return False
        try:
            applied_selection = svc.runtime.apply_phase_selection(target_selection)
        except PHASE_SELECTION_APPLY_ERRORS as error:
            svc.runtime.mark_failure("shelly")
            svc.runtime.warning_throttled(
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
        self._mismatch._clear_phase_switch_mismatch_tracking(svc, applied_selection)
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_selection is not None and normalize_phase_selection(lockout_selection) == applied_selection:
            self._mismatch._clear_phase_switch_lockout(svc)
        self._clear_auto_phase_candidate(svc)
        self._persist_phase_switch_state_best_effort(svc, "physical-apply")
        return None

    def maybe_apply_auto_phase_selection(
        self,
        svc: PhaseSwitchServicePort,
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

    def _auto_phase_selection_blocked(self, svc: PhaseSwitchServicePort, auto_mode_active: bool) -> bool:
        return any(
            (
                self._auto_phase_selection_inactive(svc, auto_mode_active),
                self._auto_phase_switch_already_active(svc),
            )
        )

    def _auto_phase_selection_decision(
        self,
        svc: PhaseSwitchServicePort,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection, PhaseSelection | None, str, float | None]:
        supported = self._selector._ordered_auto_phase_selections(svc)
        current_selection = self._selector._current_phase_selection(svc, supported)
        target_selection, phase_reason, threshold_watts = self._selector._auto_phase_target_selection(
            svc,
            supported,
            current_selection,
            desired_relay,
            relay_on,
            voltage,
            now,
        )
        self._selector._record_auto_phase_metrics(
            svc,
            current_selection=current_selection,
            target_selection=target_selection,
            phase_reason=phase_reason,
            threshold_watts=threshold_watts,
        )
        return current_selection, target_selection, phase_reason, threshold_watts

    def _auto_phase_selection_inactive(self, svc: PhaseSwitchServicePort, auto_mode_active: bool) -> bool:
        if auto_mode_active:
            return False
        self._clear_auto_phase_candidate(svc)
        return True

    def _auto_phase_switch_already_active(self, svc: PhaseSwitchServicePort) -> bool:
        pending_selection = self._runtime.pending_selection(svc)
        raw_switch_state = svc._phase_switch_state if hasattr(svc, "_phase_switch_state") else None
        switch_state = "" if raw_switch_state is None else str(raw_switch_state)
        return bool(self._runtime.state_active(pending_selection, switch_state))

    def _pending_auto_phase_target_ready(
        self,
        svc: PhaseSwitchServicePort,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
        phase_reason: str,
        threshold_watts: float | None,
    ) -> bool:
        if self._auto_phase_candidate_ready(svc, current_selection, target_selection, now):
            return True
        self._selector._record_auto_phase_metrics(
            svc,
            current_selection=current_selection,
            target_selection=target_selection,
            phase_reason=f"{phase_reason}-pending",
            threshold_watts=threshold_watts,
        )
        return False


__all__ = ["AutoPhaseSwitchController", "LocalPmPublisher", "PhaseSwitchRuntimeView"]
