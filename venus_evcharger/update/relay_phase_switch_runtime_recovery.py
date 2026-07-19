# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery helpers for staged phase-switch runtime orchestration."""

from __future__ import annotations

from venus_evcharger.backend.models import PhaseSelection
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_charger_current import ChargerTargetController
from venus_evcharger.update.relay_charger_health import ChargerHealthMonitor
from venus_evcharger.update.relay_phase_publish import RelayTelemetry
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_ports import PhaseSwitchServicePort


PHASE_SWITCH_RUNTIME_APPLY_ERRORS = (
    AttributeError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
PHASE_SWITCH_FALLBACK_SELECTION: PhaseSelection = "P1"


def _non_negative_seconds_attr(svc: object, attribute_name: str, default: float) -> float:
    configured = finite_float_or_none(getattr(svc, attribute_name, None))
    if configured is None:
        return default
    return max(0.0, configured)


class PhaseSwitchRecovery:
    """Recover phase-switch runtime after mismatches, aborts, and resume failures."""

    def __init__(
        self,
        mismatch: PhaseSwitchMismatchMonitor,
        targets: ChargerTargetController,
        health: ChargerHealthMonitor,
        telemetry: RelayTelemetry,
    ) -> None:
        self._mismatch = mismatch
        self._targets = targets
        self._health = health
        self._telemetry = telemetry

    @staticmethod
    def _clear_auto_phase_candidate(svc: PhaseSwitchServicePort) -> None:
        svc._auto_phase_target_candidate = None
        svc._auto_phase_target_since = None

    def _report_phase_switch_mismatch(
        self,
        svc: PhaseSwitchServicePort,
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
        mismatch_count = self._mismatch._remember_phase_switch_mismatch(svc, pending_selection, now)
        lockout_engaged = False
        lockout_threshold = self._mismatch._phase_switch_lockout_threshold(svc)
        if lockout_threshold > 0 and mismatch_count >= lockout_threshold:
            self._mismatch._engage_phase_switch_lockout(svc, pending_selection, now)
            lockout_engaged = self._mismatch._phase_switch_lockout_active(svc, now, pending_selection)
        svc.runtime.mark_failure("shelly")
        svc.auto.set_health("phase-switch-mismatch", cached=False)
        svc.runtime.warning_throttled(
            "phase-switch-mismatch",
            max(1.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))),
            "Phase selection %s did not confirm after %.1fs (observed=%s count=%s lockout=%s)",
            pending_selection,
            elapsed_seconds,
            observed_label,
            mismatch_count,
            int(lockout_engaged),
        )

    @staticmethod
    def clear_phase_switch_state(svc: PhaseSwitchServicePort) -> None:
        svc._phase_switch_pending_selection = None
        svc._phase_switch_state = None
        svc._phase_switch_requested_at = None
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = False
        svc._phase_switch_mismatch_active = False

    def _abort_phase_switch_after_mismatch(
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
    ) -> tuple[bool, float, float, bool]:
        fallback_selection = self._mismatch._phase_switch_fallback_selection(
            svc,
            observed_selection,
            pending_selection,
        )
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
        svc: PhaseSwitchServicePort,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        resume_relay = hasattr(svc, "_phase_switch_resume_relay") and bool(svc._phase_switch_resume_relay)
        self.clear_phase_switch_state(svc)
        if not resume_relay:
            svc.state.save_runtime_state()
            return relay_on, power, current, pm_confirmed
        if auto_mode_active:
            svc._ignore_min_offtime_once = True
            svc.state.save_runtime_state()
            return relay_on, power, current, pm_confirmed
        return self._resume_relay_after_phase_switch(svc, relay_on, power, current, pm_confirmed, now)

    def _resume_relay_after_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
    ) -> tuple[bool, float, float, bool]:
        try:
            applied = self._targets.apply_enabled_target(svc, True, now)
        except PHASE_SWITCH_RUNTIME_APPLY_ERRORS as error:
            return self._resume_relay_after_phase_switch_failure(svc, relay_on, power, current, pm_confirmed, error)
        if not applied:
            svc.state.save_runtime_state()
            return relay_on, power, current, pm_confirmed
        relay_on = True
        power = 0.0
        current = 0.0
        pm_confirmed = False
        self._telemetry.publish_local_pm_status_best_effort(svc, True, now)
        svc.state.save_runtime_state()
        return relay_on, power, current, pm_confirmed

    def _resume_relay_after_phase_switch_failure(
        self,
        svc: PhaseSwitchServicePort,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        error: Exception,
    ) -> tuple[bool, float, float, bool]:
        source_key = self._health._enable_control_source_key(svc)
        source_label = self._health._enable_control_label(svc)
        svc.runtime.mark_failure(source_key)
        svc.runtime.warning_throttled(
            "phase-switch-resume-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Failed to resume %s after phase switch: %s",
            source_label,
            error,
            exc_info=error,
        )
        svc.state.save_runtime_state()
        return relay_on, power, current, pm_confirmed

    def _abort_pending_phase_switch(
        self,
        svc: PhaseSwitchServicePort,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
        error: Exception,
    ) -> tuple[bool, float, float, bool]:
        svc.requested_phase_selection = getattr(svc, "active_phase_selection", getattr(svc, "requested_phase_selection", "P1"))
        svc.runtime.mark_failure("shelly")
        svc.runtime.warning_throttled(
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


__all__ = [
    "PHASE_SWITCH_FALLBACK_SELECTION",
    "PHASE_SWITCH_RUNTIME_APPLY_ERRORS",
    "PhaseSwitchRecovery",
    "_non_negative_seconds_attr",
]
