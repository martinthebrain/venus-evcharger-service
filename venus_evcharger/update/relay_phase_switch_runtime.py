# SPDX-License-Identifier: GPL-3.0-or-later
"""Staged phase-switch runtime orchestration for the update cycle."""

from __future__ import annotations

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_or_none
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_phase_switch_runtime_recovery import (
    PHASE_SWITCH_FALLBACK_SELECTION,
    PHASE_SWITCH_RUNTIME_APPLY_ERRORS,
    PhaseSwitchRecovery,
    _non_negative_seconds_attr,
)
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_ports import PhaseSwitchServicePort


class PhaseSwitchCoordinator:
    """Advance waiting and stabilizing phase-switch state machines."""

    def __init__(
        self,
        recovery: PhaseSwitchRecovery,
        mismatch: PhaseSwitchMismatchMonitor,
        *,
        waiting_state: str,
        stabilizing_state: str,
    ) -> None:
        self._recovery = recovery
        self._mismatch = mismatch
        self._waiting_state = waiting_state
        self._stabilizing_state = stabilizing_state

    @staticmethod
    def _phase_switch_pause_seconds(svc: object) -> float:
        return _non_negative_seconds_attr(svc, "phase_switch_pause_seconds", 1.0)

    @staticmethod
    def _phase_switch_stabilization_seconds(svc: object) -> float:
        return _non_negative_seconds_attr(svc, "phase_switch_stabilization_seconds", 2.0)

    @staticmethod
    def pending_selection(svc: PhaseSwitchServicePort) -> PhaseSelection | None:
        pending = getattr(svc, "_phase_switch_pending_selection", None)
        if pending is None:
            return None
        return normalize_phase_selection_or_none(pending) or PHASE_SWITCH_FALLBACK_SELECTION

    @staticmethod
    def _observed_phase_selection_from_pm_status(pm_status: dict[str, object]) -> PhaseSelection | None:
        observed = pm_status.get("_phase_selection")
        if observed is None:
            return None
        return normalize_phase_selection_or_none(observed) or PHASE_SWITCH_FALLBACK_SELECTION

    def _observed_phase_selection(
        self,
        svc: PhaseSwitchServicePort,
        pm_status: dict[str, object],
        now: float,
    ) -> PhaseSelection | None:
        observed = self._observed_phase_selection_from_pm_status(pm_status)
        if observed is not None:
            return observed
        return self._observed_phase_selection_from_charger_state(svc, now)

    def _observed_phase_selection_from_charger_state(self, svc: PhaseSwitchServicePort, now: float) -> PhaseSelection | None:
        readback = svc._readback_resolver.resolve(now).charger
        if readback is None:
            return None
        raw_phase_selection = readback.state.phase_selection
        if raw_phase_selection is None:
            return None
        return normalize_phase_selection_or_none(raw_phase_selection) or PHASE_SWITCH_FALLBACK_SELECTION

    def _phase_switch_verification_deadline(self, svc: PhaseSwitchServicePort) -> float | None:
        stable_until = finite_float_or_none(getattr(svc, "_phase_switch_stable_until", None))
        soft_fail_seconds = _non_negative_seconds_attr(svc, "auto_shelly_soft_fail_seconds", 10.0)
        if stable_until is not None:
            return stable_until + soft_fail_seconds
        if not hasattr(svc, "_phase_switch_requested_at"):
            return None
        requested_at = finite_float_or_none(svc._phase_switch_requested_at)
        if requested_at is None:
            return None
        return requested_at + self._phase_switch_pause_seconds(svc) + self._phase_switch_stabilization_seconds(svc) + soft_fail_seconds

    def _phase_switch_verification_expired(self, svc: PhaseSwitchServicePort, now: float) -> bool:
        deadline = self._phase_switch_verification_deadline(svc)
        return deadline is not None and float(now) >= float(deadline)

    def orchestrate_pending_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        pm_status: dict[str, object],
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        pending_selection = self.pending_selection(svc)
        raw_switch_state = getattr(svc, "_phase_switch_state", None)
        switch_state = "" if raw_switch_state is None else str(raw_switch_state)
        if pending_selection is None or not self.state_active(pending_selection, switch_state):
            self._recovery.clear_phase_switch_state(svc)
            return relay_on, power, current, pm_confirmed, None

        if switch_state == self._waiting_state:
            return self._orchestrate_waiting_phase_switch(
                svc,
                pending_selection,
                relay_on,
                power,
                current,
                pm_confirmed,
                now,
                auto_mode_active,
            )
        return self._orchestrate_stabilizing_phase_switch(
            svc,
            pending_selection,
            pm_status,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

    def state_active(self, pending_selection: PhaseSelection | None, switch_state: str) -> bool:
        return pending_selection is not None and switch_state in {
            self._waiting_state,
            self._stabilizing_state,
        }

    def _phase_switch_waiting_ready(self, svc: PhaseSwitchServicePort, relay_on: bool, pm_confirmed: bool, now: float) -> bool:
        pending_relay_state, _requested_at = svc.runtime.pending_relay_command()
        if self._phase_switch_waiting_blocked(relay_on, pending_relay_state, pm_confirmed):
            return False
        return self._phase_switch_pause_elapsed(svc, now)

    @staticmethod
    def _phase_switch_waiting_blocked(relay_on: bool, pending_relay_state: object, pm_confirmed: bool) -> bool:
        return bool(relay_on) or pending_relay_state is not None or not pm_confirmed

    def _phase_switch_pause_elapsed(self, svc: PhaseSwitchServicePort, now: float) -> bool:
        requested_at = svc._phase_switch_requested_at if hasattr(svc, "_phase_switch_requested_at") else None
        return requested_at is None or (float(now) - float(requested_at)) >= self._phase_switch_pause_seconds(svc)

    def _apply_pending_phase_selection(
        self,
        svc: PhaseSwitchServicePort,
        pending_selection: PhaseSelection,
        now: float,
    ) -> tuple[bool, float, float, bool, bool | None]:
        applied_selection = svc.runtime.apply_phase_selection(pending_selection)
        svc._phase_switch_mismatch_active = False
        svc.requested_phase_selection = applied_selection
        svc._phase_switch_state = self._stabilizing_state
        svc._phase_switch_stable_until = float(now) + self._phase_switch_stabilization_seconds(svc)
        svc.state.save_runtime_state()
        return False, 0.0, 0.0, False, False

    def _orchestrate_waiting_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        pending_selection: PhaseSelection,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        if not self._phase_switch_waiting_ready(svc, relay_on, pm_confirmed, now):
            return relay_on, power, current, pm_confirmed, False
        try:
            return self._apply_pending_phase_selection(svc, pending_selection, now)
        except PHASE_SWITCH_RUNTIME_APPLY_ERRORS as error:
            relay_on, power, current, pm_confirmed = self._recovery._abort_pending_phase_switch(
                svc,
                relay_on,
                power,
                current,
                pm_confirmed,
                now,
                auto_mode_active,
                error,
            )
            return relay_on, power, current, pm_confirmed, None

    def _orchestrate_stabilizing_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        pending_selection: PhaseSelection,
        pm_status: dict[str, object],
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        observed_selection = self._remember_observed_phase_selection(svc, pm_status, now)
        if self._phase_switch_still_stabilizing(svc, now):
            return False, 0.0, 0.0, False, False
        mismatch_result = self._stabilizing_phase_switch_mismatch_result(
            svc,
            pending_selection,
            observed_selection,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )
        if mismatch_result is not None:
            return mismatch_result
        return self._complete_stabilized_phase_switch(
            svc,
            pending_selection,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

    def _remember_observed_phase_selection(
        self,
        svc: PhaseSwitchServicePort,
        pm_status: dict[str, object],
        now: float,
    ) -> PhaseSelection | None:
        observed_selection = self._observed_phase_selection(svc, pm_status, now)
        if observed_selection is not None:
            svc.active_phase_selection = observed_selection
        return observed_selection

    def _complete_stabilized_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        pending_selection: PhaseSelection,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        svc.active_phase_selection = pending_selection
        self._mismatch._clear_phase_switch_mismatch_tracking(svc, pending_selection)
        self._clear_matching_phase_switch_lockout(svc, pending_selection)
        relay_on, power, current, pm_confirmed = self._recovery._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )
        return relay_on, power, current, pm_confirmed, None
    def _clear_matching_phase_switch_lockout(self, svc: PhaseSwitchServicePort, pending_selection: PhaseSelection) -> None:
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_selection is not None and normalize_phase_selection(lockout_selection, "P1") == pending_selection:
            self._mismatch._clear_phase_switch_lockout(svc)

    @staticmethod
    def _phase_switch_still_stabilizing(svc: PhaseSwitchServicePort, now: float) -> bool:
        stable_until = getattr(svc, "_phase_switch_stable_until", None)
        return stable_until is not None and float(now) < float(stable_until)

    def _stabilizing_phase_switch_mismatch_result(
        self,
        svc: PhaseSwitchServicePort,
        pending_selection: PhaseSelection,
        observed_selection: PhaseSelection | None,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None] | None:
        if observed_selection == pending_selection:
            return None
        if not self._phase_switch_verification_expired(svc, now):
            return False, 0.0, 0.0, False, False
        self._recovery._report_phase_switch_mismatch(svc, pending_selection, observed_selection, now)
        relay_on, power, current, pm_confirmed = self._recovery._abort_phase_switch_after_mismatch(
            svc,
            pending_selection,
            observed_selection,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )
        return relay_on, power, current, pm_confirmed, None


__all__ = ["PhaseSwitchCoordinator"]
