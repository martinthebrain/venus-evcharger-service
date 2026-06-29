# SPDX-License-Identifier: GPL-3.0-or-later
"""Staged phase-switch runtime orchestration for the update cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_status_publish import _RelayStatusPublish


PHASE_SWITCH_RUNTIME_APPLY_ERRORS = (
    AttributeError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class _RelayPhaseSwitchRuntime(_RelayStatusPublish):
    """Advance waiting and stabilizing phase-switch state machines."""

    if TYPE_CHECKING:  # pragma: no cover
        PHASE_SWITCH_WAITING_STATE: str
        PHASE_SWITCH_STABILIZING_STATE: str

        @classmethod
        def _fresh_charger_state_timestamp(cls, svc: Any, now: float | None = None) -> float | None: ...

        @classmethod
        def _remember_phase_switch_mismatch(cls, svc: Any, selection: PhaseSelection, now: float) -> int: ...

        @classmethod
        def _phase_switch_lockout_threshold(cls, svc: Any) -> int: ...

        @classmethod
        def _engage_phase_switch_lockout(cls, svc: Any, selection: PhaseSelection, now: float) -> None: ...

        @classmethod
        def _phase_switch_lockout_active(
            cls,
            svc: Any,
            now: float,
            selection: PhaseSelection | None = None,
        ) -> bool: ...

        @classmethod
        def _phase_switch_fallback_selection(
            cls,
            svc: Any,
            observed_selection: PhaseSelection | None,
            pending_selection: PhaseSelection,
        ) -> PhaseSelection: ...

        @staticmethod
        def _clear_auto_phase_candidate(svc: Any) -> None: ...

        @classmethod
        def _apply_enabled_target(cls, svc: Any, enabled: bool, now: float) -> bool: ...

        @classmethod
        def _enable_control_source_key(cls, svc: Any) -> str: ...

        @classmethod
        def _enable_control_label(cls, svc: Any) -> str: ...

        def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None: ...

        @classmethod
        def _clear_phase_switch_mismatch_tracking(
            cls,
            svc: Any,
            selection: PhaseSelection | None = None,
        ) -> None: ...

        @staticmethod
        def _clear_phase_switch_lockout(svc: Any) -> None: ...

    @staticmethod
    def _phase_switch_pause_seconds(svc: Any) -> float:
        return max(0.0, float(getattr(svc, "phase_switch_pause_seconds", 1.0) or 1.0))

    @staticmethod
    def _phase_switch_stabilization_seconds(svc: Any) -> float:
        return max(0.0, float(getattr(svc, "phase_switch_stabilization_seconds", 2.0) or 2.0))

    @staticmethod
    def _pending_phase_switch_selection(svc: Any) -> PhaseSelection | None:
        pending = getattr(svc, "_phase_switch_pending_selection", None)
        if pending is None:
            return None
        return normalize_phase_selection(pending, normalize_phase_selection("P1"))

    @staticmethod
    def _observed_phase_selection_from_pm_status(pm_status: dict[str, Any]) -> PhaseSelection | None:
        observed = pm_status.get("_phase_selection")
        if observed is None:
            return None
        return normalize_phase_selection(observed, "P1")

    @classmethod
    def _observed_phase_selection(
        cls,
        svc: Any,
        pm_status: dict[str, Any],
        now: float,
    ) -> PhaseSelection | None:
        observed = cls._observed_phase_selection_from_pm_status(pm_status)
        if observed is not None:
            return observed
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        raw_phase_selection = getattr(svc, "_last_charger_state_phase_selection", None)
        if raw_phase_selection is None:
            return None
        return normalize_phase_selection(raw_phase_selection, "P1")

    @classmethod
    def _phase_switch_verification_deadline(cls, svc: Any) -> float | None:
        stable_until = finite_float_or_none(getattr(svc, "_phase_switch_stable_until", None))
        soft_fail_seconds = max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0)))
        if stable_until is not None:
            return stable_until + soft_fail_seconds
        requested_at = finite_float_or_none(getattr(svc, "_phase_switch_requested_at", None))
        if requested_at is None:
            return None
        return requested_at + cls._phase_switch_pause_seconds(svc) + cls._phase_switch_stabilization_seconds(svc) + soft_fail_seconds

    @classmethod
    def _phase_switch_verification_expired(cls, svc: Any, now: float) -> bool:
        deadline = cls._phase_switch_verification_deadline(svc)
        return deadline is not None and float(now) >= float(deadline)

    def _report_phase_switch_mismatch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        observed_selection: PhaseSelection | None,
        now: float,
    ) -> None:
        requested_at = finite_float_or_none(getattr(svc, "_phase_switch_requested_at", None))
        elapsed_seconds = (
            max(0.0, float(now) - requested_at)
            if requested_at is not None
            else max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0)))
        )
        observed_label = observed_selection if observed_selection is not None else "unknown"
        mismatch_count = self._remember_phase_switch_mismatch(svc, pending_selection, now)
        lockout_engaged = False
        lockout_threshold = self._phase_switch_lockout_threshold(svc)
        if lockout_threshold > 0 and mismatch_count >= lockout_threshold:
            self._engage_phase_switch_lockout(svc, pending_selection, now)
            lockout_engaged = self._phase_switch_lockout_active(svc, now, pending_selection)
        svc._mark_failure("shelly")
        svc._set_health("phase-switch-mismatch", cached=False)
        svc._warning_throttled(
            "phase-switch-mismatch",
            max(1.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))),
            "Phase selection %s did not confirm after %.1fs (observed=%s count=%s lockout=%s)",
            pending_selection,
            elapsed_seconds,
            observed_label,
            mismatch_count,
            int(lockout_engaged),
        )

    @classmethod
    def _clear_phase_switch_state(cls, svc: Any) -> None:
        svc._phase_switch_pending_selection = None
        svc._phase_switch_state = None
        svc._phase_switch_requested_at = None
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = False
        svc._phase_switch_mismatch_active = False

    def _abort_phase_switch_after_mismatch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        observed_selection: PhaseSelection | None,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        fallback_selection = self._phase_switch_fallback_selection(svc, observed_selection, pending_selection)
        svc.requested_phase_selection = fallback_selection
        svc.active_phase_selection = fallback_selection
        self._clear_auto_phase_candidate(svc)
        return self._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

    def _resume_after_phase_switch_pause(
        self,
        svc: Any,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        resume_relay = bool(getattr(svc, "_phase_switch_resume_relay", False))
        self._clear_phase_switch_state(svc)
        if not resume_relay:
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        if auto_mode_active:
            svc._ignore_min_offtime_once = True
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        try:
            applied = self._apply_enabled_target(svc, True, now)
        except PHASE_SWITCH_RUNTIME_APPLY_ERRORS as error:
            source_key = self._enable_control_source_key(svc)
            source_label = self._enable_control_label(svc)
            svc._mark_failure(source_key)
            svc._warning_throttled(
                "phase-switch-resume-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to resume %s after phase switch: %s",
                source_label,
                error,
                exc_info=error,
            )
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        if not applied:
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        relay_on = True
        power = 0.0
        current = 0.0
        pm_confirmed = False
        self._publish_local_pm_status_best_effort(True, now)
        svc._save_runtime_state()
        return relay_on, power, current, pm_confirmed

    def _abort_pending_phase_switch(
        self,
        svc: Any,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
        error: Exception,
    ) -> tuple[bool, float, float, bool]:
        svc.requested_phase_selection = getattr(svc, "active_phase_selection", getattr(svc, "requested_phase_selection", "P1"))
        svc._mark_failure("shelly")
        svc._warning_throttled(
            "phase-switch-apply-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Failed to apply phase selection %s: %s",
            getattr(svc, "_phase_switch_pending_selection", None),
            error,
            exc_info=error,
        )
        return self._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

    def orchestrate_pending_phase_switch(
        self,
        pm_status: dict[str, Any],
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        svc = self.service
        pending_selection = self._pending_phase_switch_selection(svc)
        switch_state = str(getattr(svc, "_phase_switch_state", "") or "")
        if not self._phase_switch_state_active(pending_selection, switch_state):
            self._clear_phase_switch_state(svc)
            return relay_on, power, current, pm_confirmed, None
        assert pending_selection is not None

        if switch_state == self.PHASE_SWITCH_WAITING_STATE:
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

    def _phase_switch_state_active(self, pending_selection: PhaseSelection | None, switch_state: str) -> bool:
        return pending_selection is not None and switch_state in {
            self.PHASE_SWITCH_WAITING_STATE,
            self.PHASE_SWITCH_STABILIZING_STATE,
        }

    def _phase_switch_waiting_ready(self, svc: Any, relay_on: bool, pm_confirmed: bool, now: float) -> bool:
        pending_relay_state, _requested_at = svc._peek_pending_relay_command()
        if bool(relay_on) or pending_relay_state is not None or not pm_confirmed:
            return False
        requested_at = getattr(svc, "_phase_switch_requested_at", None)
        return requested_at is None or (float(now) - float(requested_at)) >= self._phase_switch_pause_seconds(svc)

    def _apply_pending_phase_selection(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        now: float,
    ) -> tuple[bool, float, float, bool, bool | None]:
        applied_selection = svc._apply_phase_selection(pending_selection)
        svc._phase_switch_mismatch_active = False
        svc.requested_phase_selection = applied_selection
        svc._phase_switch_state = self.PHASE_SWITCH_STABILIZING_STATE
        svc._phase_switch_stable_until = float(now) + self._phase_switch_stabilization_seconds(svc)
        svc._save_runtime_state()
        return False, 0.0, 0.0, False, False

    def _orchestrate_waiting_phase_switch(
        self,
        svc: Any,
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
            relay_on, power, current, pm_confirmed = self._abort_pending_phase_switch(
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
        svc: Any,
        pending_selection: PhaseSelection,
        pm_status: dict[str, Any],
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
        svc: Any,
        pm_status: dict[str, Any],
        now: float,
    ) -> PhaseSelection | None:
        observed_selection = self._observed_phase_selection(svc, pm_status, now)
        if observed_selection is not None:
            svc.active_phase_selection = observed_selection
        return observed_selection

    def _complete_stabilized_phase_switch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        svc.active_phase_selection = pending_selection
        self._clear_phase_switch_mismatch_tracking(svc, pending_selection)
        self._clear_matching_phase_switch_lockout(svc, pending_selection)
        relay_on, power, current, pm_confirmed = self._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )
        return relay_on, power, current, pm_confirmed, None

    def _clear_matching_phase_switch_lockout(self, svc: Any, pending_selection: PhaseSelection) -> None:
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_selection is not None and normalize_phase_selection(lockout_selection, "P1") == pending_selection:
            self._clear_phase_switch_lockout(svc)

    @staticmethod
    def _phase_switch_still_stabilizing(svc: Any, now: float) -> bool:
        stable_until = getattr(svc, "_phase_switch_stable_until", None)
        return stable_until is not None and float(now) < float(stable_until)

    def _stabilizing_phase_switch_mismatch_result(
        self,
        svc: Any,
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
        self._report_phase_switch_mismatch(svc, pending_selection, observed_selection, now)
        relay_on, power, current, pm_confirmed = self._abort_phase_switch_after_mismatch(
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
