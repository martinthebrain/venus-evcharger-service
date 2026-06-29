# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic phase-selection decision helpers for the update cycle."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection
from venus_evcharger.core.contracts import finite_float_or_none, mutable_dict_attr
from venus_evcharger.update.relay_phase_switch_policy import _RelayPhaseSwitchPolicy


class _RelayPhaseDecision(_RelayPhaseSwitchPolicy):
    """Derive Auto phase targets from policy, surplus, and supported layouts."""

    if TYPE_CHECKING:  # pragma: no cover

        @staticmethod
        def _phase_voltage(voltage: float, selection: Any, voltage_mode: Any) -> float: ...

        @classmethod
        def _downshift_auto_phase_target(
            cls,
            svc: Any,
            phase_policy: Any,
            supported: tuple[PhaseSelection, ...],
            current_selection: PhaseSelection,
            current_index: int,
            surplus_watts: float,
            voltage: float,
        ) -> tuple[PhaseSelection | None, str, float | None] | None: ...

        @classmethod
        def _phase_switch_lockout_active(
            cls,
            svc: Any,
            now: float,
            selection: PhaseSelection | None = None,
        ) -> bool: ...

        @classmethod
        def _phase_switch_mismatch_retry_active(
            cls,
            svc: Any,
            current_selection: PhaseSelection,
            target_selection: PhaseSelection,
            now: float,
        ) -> bool: ...

    @staticmethod
    def _phase_selection_count(selection: object) -> int:
        normalized = normalize_phase_selection(selection, "P1")
        if normalized == "P1_P2_P3":
            return 3
        if normalized == "P1_P2":
            return 2
        return 1

    @classmethod
    def _phase_selection_is_upshift(
        cls,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
    ) -> bool:
        return cls._phase_selection_count(target_selection) > cls._phase_selection_count(current_selection)

    @classmethod
    def _ordered_auto_phase_selections(cls, svc: Any) -> tuple[PhaseSelection, ...]:
        raw_supported = tuple(getattr(svc, "supported_phase_selections", ("P1",)))
        normalized_supported: set[PhaseSelection] = {
            normalize_phase_selection(selection, "P1") for selection in raw_supported
        }
        ordered = tuple(
            sorted(
                normalized_supported,
                key=cls._phase_selection_count,
            )
        )
        return ordered or ("P1",)

    @classmethod
    def _current_phase_selection(
        cls,
        svc: Any,
        supported: tuple[PhaseSelection, ...],
    ) -> PhaseSelection:
        default_selection = supported[0]
        requested = normalize_phase_selection(
            getattr(svc, "requested_phase_selection", default_selection),
            default_selection,
        )
        if requested in supported:
            return requested
        active = normalize_phase_selection(
            getattr(svc, "active_phase_selection", default_selection),
            default_selection,
        )
        return active if active in supported else default_selection

    @staticmethod
    def _auto_phase_policy(svc: Any) -> Any | None:
        auto_policy = getattr(svc, "auto_policy", None)
        if auto_policy is None:
            return None
        return getattr(auto_policy, "phase", None)

    @staticmethod
    def _auto_phase_metrics(svc: Any) -> dict[str, Any]:
        return mutable_dict_attr(svc, "_last_auto_metrics")

    @classmethod
    def _record_auto_phase_metrics(
        cls,
        svc: Any,
        *,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection | None,
        phase_reason: str,
        threshold_watts: float | None,
    ) -> None:
        metrics = cls._auto_phase_metrics(svc)
        metrics["phase_current"] = current_selection
        metrics["phase_target"] = target_selection
        metrics["phase_reason"] = phase_reason
        metrics["phase_threshold_watts"] = threshold_watts
        metrics["phase_candidate"] = getattr(svc, "_auto_phase_target_candidate", None)
        metrics["phase_candidate_since"] = finite_float_or_none(getattr(svc, "_auto_phase_target_since", None))

    @staticmethod
    def _auto_phase_metric_surplus_watts(svc: Any) -> float | None:
        metrics = getattr(svc, "_last_auto_metrics", None)
        if not isinstance(metrics, dict):
            return None
        return finite_float_or_none(metrics.get("surplus"))

    @classmethod
    def _phase_selection_voltage(
        cls,
        svc: Any,
        selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        phase_voltage = cls._phase_voltage(voltage, selection, getattr(svc, "voltage_mode", "phase"))
        return None if phase_voltage <= 0.0 else float(phase_voltage)

    @classmethod
    def _phase_selection_min_surplus_watts(
        cls,
        svc: Any,
        selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        phase_voltage = cls._phase_selection_voltage(svc, selection, voltage)
        if min_current is None or min_current <= 0.0 or phase_voltage is None:
            return None
        return float(min_current) * phase_voltage * float(cls._phase_selection_count(selection))

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
    ) -> tuple[PhaseSelection | None, str, float | None]:
        policy_state = cls._auto_phase_policy_state(svc, supported)
        if policy_state is not None:
            return policy_state

        phase_policy = cls._auto_phase_policy(svc)
        assert phase_policy is not None
        idle_target = cls._idle_auto_phase_target(
            phase_policy,
            supported,
            current_selection,
            desired_relay,
            relay_on,
        )
        if idle_target is not None:
            return idle_target
        return cls._surplus_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_selection,
            voltage,
            now,
        )

    @classmethod
    def _surplus_auto_phase_target(
        cls,
        svc: Any,
        phase_policy: Any,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None]:
        surplus_watts = cls._auto_phase_metric_surplus_watts(svc)
        if surplus_watts is None:
            return None, "phase-surplus-missing", None
        current_index = supported.index(current_selection)
        upshift_target = cls._upshift_auto_phase_target(
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
        downshift_target = cls._downshift_auto_phase_target(
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

    @classmethod
    def _auto_phase_policy_state(
        cls,
        svc: Any,
        supported: tuple[PhaseSelection, ...],
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is None or not bool(getattr(phase_policy, "enabled", True)):
            return None, "phase-policy-disabled", None
        if len(supported) <= 1:
            return None, "single-phase-only", None
        return None

    @staticmethod
    def _idle_auto_phase_target(
        phase_policy: Any,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        if desired_relay or relay_on:
            return None
        lowest_selection = supported[0]
        if bool(getattr(phase_policy, "prefer_lowest_phase_when_idle", True)) and current_selection != lowest_selection:
            return lowest_selection, "idle-lowest-phase", None
        return None, "idle-hold-phase", None

    @classmethod
    def _upshift_auto_phase_target(
        cls,
        svc: Any,
        phase_policy: Any,
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
        threshold = cls._phase_upshift_threshold(svc, phase_policy, next_selection, voltage)
        if threshold is None:
            return None
        if surplus_watts < threshold:
            return None
        block_reason = cls._phase_upshift_block_reason(svc, current_selection, next_selection, now)
        if block_reason is not None:
            return None, block_reason, threshold
        return next_selection, "phase-upshift", threshold

    @classmethod
    def _phase_upshift_threshold(
        cls,
        svc: Any,
        phase_policy: Any,
        next_selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        next_min_surplus = cls._phase_selection_min_surplus_watts(svc, next_selection, voltage)
        if next_min_surplus is None:
            return None
        return next_min_surplus + float(getattr(phase_policy, "upshift_headroom_watts", 250.0))

    @classmethod
    def _phase_upshift_block_reason(
        cls,
        svc: Any,
        current_selection: PhaseSelection,
        next_selection: PhaseSelection,
        now: float,
    ) -> str | None:
        if cls._phase_switch_lockout_active(svc, now, next_selection):
            return "phase-upshift-blocked-lockout"
        if cls._phase_switch_mismatch_retry_active(svc, current_selection, next_selection, now):
            return "phase-upshift-blocked-mismatch"
        return None
