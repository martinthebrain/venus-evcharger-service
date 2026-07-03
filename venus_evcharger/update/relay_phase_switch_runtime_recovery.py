# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery helpers for staged phase-switch runtime orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.backend.models import PhaseSelection
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
PHASE_SWITCH_FALLBACK_SELECTION: PhaseSelection = "P1"


def _non_negative_seconds_attr(svc: Any, attribute_name: str, default: float) -> float:
    configured = finite_float_or_none(getattr(svc, attribute_name, None))
    if configured is None:
        return default
    return max(0.0, configured)


class _RelayPhaseSwitchRuntimeRecovery(_RelayStatusPublish):
    """Recover phase-switch runtime after mismatches, aborts, and resume failures."""

    if TYPE_CHECKING:  # pragma: no cover
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
        resume_relay = hasattr(svc, "_phase_switch_resume_relay") and bool(svc._phase_switch_resume_relay)
        self._clear_phase_switch_state(svc)
        if not resume_relay:
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        if auto_mode_active:
            svc._ignore_min_offtime_once = True
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        return self._resume_relay_after_phase_switch(svc, relay_on, power, current, pm_confirmed, now)

    def _resume_relay_after_phase_switch(
        self,
        svc: Any,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
    ) -> tuple[bool, float, float, bool]:
        try:
            applied = self._apply_enabled_target(svc, True, now)
        except PHASE_SWITCH_RUNTIME_APPLY_ERRORS as error:
            return self._resume_relay_after_phase_switch_failure(svc, relay_on, power, current, pm_confirmed, error)
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

    def _resume_relay_after_phase_switch_failure(
        self,
        svc: Any,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        error: Exception,
    ) -> tuple[bool, float, float, bool]:
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
