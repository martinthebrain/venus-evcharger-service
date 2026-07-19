# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure automatic phase-target selection component."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeGuard

from venus_evcharger.auto.policy import AutoPhasePolicy
from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection_or_none, phase_selection_count
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_ports import PhaseSwitchServicePort


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    return all(isinstance(key, str) for key in value)


class AutoPhaseTargetSelector:
    """Derive Auto phase targets from policy, surplus, and supported layouts."""

    def __init__(
        self,
        mismatch: PhaseSwitchMismatchMonitor,
        phase_voltage: Callable[[float, object, object], float],
    ) -> None:
        self._mismatch = mismatch
        self._phase_voltage = phase_voltage

    @staticmethod
    def _phase_selection_count(selection: object) -> int:
        return phase_selection_count(selection)

    def _ordered_auto_phase_selections(self, svc: PhaseSwitchServicePort) -> tuple[PhaseSelection, ...]:
        raw_supported = tuple(getattr(svc, "supported_phase_selections", ()))
        normalized_supported: set[PhaseSelection] = set()
        for selection in raw_supported:
            normalized = normalize_phase_selection_or_none(selection)
            if normalized is not None:
                normalized_supported.add(normalized)
        return tuple(sorted(normalized_supported))

    def _current_phase_selection(
        self,
        svc: PhaseSwitchServicePort,
        supported: tuple[PhaseSelection, ...],
    ) -> PhaseSelection:
        if not supported:
            return self._fallback_phase_selection(svc)
        return self._supported_phase_selection(svc, supported)

    @staticmethod
    def _fallback_phase_selection(svc: PhaseSwitchServicePort) -> PhaseSelection:
        active = normalize_phase_selection_or_none(getattr(svc, "active_phase_selection", None))
        if active is not None:
            return active
        requested = normalize_phase_selection_or_none(getattr(svc, "requested_phase_selection", None))
        return "P1" if requested is None else requested

    @staticmethod
    def _supported_phase_selection(
        svc: PhaseSwitchServicePort,
        supported: tuple[PhaseSelection, ...],
    ) -> PhaseSelection:
        requested = normalize_phase_selection_or_none(getattr(svc, "requested_phase_selection", None))
        if requested is None:
            return supported[0]
        if requested in supported:
            return requested
        active = normalize_phase_selection_or_none(getattr(svc, "active_phase_selection", None))
        if active in supported:
            return active
        return supported[0]

    @staticmethod
    def _auto_phase_policy(svc: PhaseSwitchServicePort) -> AutoPhasePolicy:
        policy: AutoPhasePolicy = svc.auto_policy.phase
        return policy

    @staticmethod
    def _auto_phase_metrics(svc: PhaseSwitchServicePort) -> dict[str, object]:
        return svc._last_auto_metrics

    def _record_auto_phase_metrics(
        self,
        svc: PhaseSwitchServicePort,
        *,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection | None,
        phase_reason: str,
        threshold_watts: float | None,
    ) -> None:
        metrics = self._auto_phase_metrics(svc)
        metrics["phase_current"] = current_selection
        metrics["phase_target"] = target_selection
        metrics["phase_reason"] = phase_reason
        metrics["phase_threshold_watts"] = threshold_watts
        metrics["phase_candidate"] = getattr(svc, "_auto_phase_target_candidate", None)
        metrics["phase_candidate_since"] = finite_float_or_none(getattr(svc, "_auto_phase_target_since", None))

    @staticmethod
    def _auto_phase_metric_surplus_watts(svc: PhaseSwitchServicePort) -> float | None:
        metrics = getattr(svc, "_last_auto_metrics", None)
        if not _is_string_object_dict(metrics):
            return None
        return finite_float_or_none(metrics.get("surplus"))

    def _phase_selection_voltage(
        self,
        svc: PhaseSwitchServicePort,
        selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        phase_voltage = self._phase_voltage(voltage, selection, getattr(svc, "voltage_mode", None))
        return None if phase_voltage <= 0.0 else float(phase_voltage)

    def _phase_selection_min_surplus_watts(
        self,
        svc: PhaseSwitchServicePort,
        selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        phase_voltage = self._phase_selection_voltage(svc, selection, voltage)
        if min_current is None or min_current <= 0.0 or phase_voltage is None:
            return None
        return float(min_current) * phase_voltage * float(self._phase_selection_count(selection))

    def _auto_phase_target_selection(
        self,
        svc: PhaseSwitchServicePort,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None]:
        policy_state = self._auto_phase_policy_state(svc, supported)
        if policy_state is not None:
            return policy_state

        phase_policy = self._auto_phase_policy(svc)
        idle_target = self._idle_auto_phase_target(
            phase_policy,
            supported,
            current_selection,
            desired_relay,
            relay_on,
        )
        if idle_target is not None:
            return idle_target
        return self._surplus_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_selection,
            voltage,
            now,
        )

    def _surplus_auto_phase_target(
        self,
        svc: PhaseSwitchServicePort,
        phase_policy: AutoPhasePolicy,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None]:
        surplus_watts = self._auto_phase_metric_surplus_watts(svc)
        if surplus_watts is None:
            return None, "phase-surplus-missing", None
        current_index = supported.index(current_selection)
        upshift_target = self._upshift_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_index,
            current_selection,
            surplus_watts,
            voltage,
            now,
        )
        if upshift_target is not None:
            return upshift_target
        downshift_target = self._downshift_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_selection,
            current_index,
            surplus_watts,
            voltage,
        )
        if downshift_target is not None:
            return downshift_target
        return None, "phase-hold", None

    def _downshift_auto_phase_target(
        self,
        svc: PhaseSwitchServicePort,
        phase_policy: AutoPhasePolicy,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        current_index: int,
        surplus_watts: float,
        voltage: float,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        if current_index <= 0:
            return None
        current_min_surplus = self._phase_selection_min_surplus_watts(
            svc,
            current_selection,
            voltage,
        )
        if current_min_surplus is None:
            return None
        threshold = max(
            0.0,
            current_min_surplus - float(phase_policy.downshift_margin_watts),
        )
        if surplus_watts >= threshold:
            return None
        return supported[current_index - 1], "phase-downshift", threshold

    def _auto_phase_policy_state(
        self,
        svc: PhaseSwitchServicePort,
        supported: tuple[PhaseSelection, ...],
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        phase_policy = self._auto_phase_policy(svc)
        if not phase_policy.enabled:
            return None, "phase-policy-disabled", None
        if not supported:
            return None, "phase-capabilities-unavailable", None
        if len(supported) <= 1:
            return None, "single-phase-only", None
        return None

    @staticmethod
    def _idle_auto_phase_target(
        phase_policy: AutoPhasePolicy,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        if desired_relay or relay_on:
            return None
        lowest_selection = supported[0]
        if phase_policy.prefer_lowest_phase_when_idle and current_selection != lowest_selection:
            return lowest_selection, "idle-lowest-phase", None
        return None, "idle-hold-phase", None

    def _upshift_auto_phase_target(
        self,
        svc: PhaseSwitchServicePort,
        phase_policy: AutoPhasePolicy,
        supported: tuple[PhaseSelection, ...],
        current_index: int,
        current_selection: PhaseSelection,
        surplus_watts: float,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        if current_index >= (len(supported) - 1):
            return None
        next_selection = supported[current_index + 1]
        threshold = self._phase_upshift_threshold(svc, phase_policy, next_selection, voltage)
        if threshold is None:
            return None
        if surplus_watts < threshold:
            return None
        block_reason = self._phase_upshift_block_reason(svc, current_selection, next_selection, now)
        if block_reason is not None:
            return None, block_reason, threshold
        return next_selection, "phase-upshift", threshold

    def _phase_upshift_threshold(
        self,
        svc: PhaseSwitchServicePort,
        phase_policy: AutoPhasePolicy,
        next_selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        next_min_surplus = self._phase_selection_min_surplus_watts(svc, next_selection, voltage)
        if next_min_surplus is None:
            return None
        return next_min_surplus + float(phase_policy.upshift_headroom_watts)

    def _phase_upshift_block_reason(
        self,
        svc: PhaseSwitchServicePort,
        current_selection: PhaseSelection,
        next_selection: PhaseSelection,
        now: float,
    ) -> str | None:
        if self._mismatch._phase_switch_lockout_active(svc, now, next_selection):
            return "phase-upshift-blocked-lockout"
        if self._mismatch._phase_switch_mismatch_retry_active(svc, current_selection, next_selection, now):
            return "phase-upshift-blocked-mismatch"
        return None


__all__ = ["AutoPhaseTargetSelector"]
