# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-switch mismatch retry and lockout state helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_or_none
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_charger_readback import _RelayChargerReadback


class _PhaseSwitchMismatchState(Protocol):
    _phase_switch_mismatch_active: bool
    _phase_switch_mismatch_counts: dict[str, int]
    _phase_switch_last_mismatch_selection: str | None
    _phase_switch_last_mismatch_at: float | None
    _phase_switch_lockout_selection: str | None
    _phase_switch_lockout_reason: str
    _phase_switch_lockout_at: float | None
    _phase_switch_lockout_until: float | None


class _PhaseSwitchMismatchPolicy(Protocol):
    mismatch_retry_seconds: float
    mismatch_lockout_count: int
    mismatch_lockout_seconds: float


class _RelayPhaseSwitchMismatch(_RelayChargerReadback):
    """Track phase-switch mismatch retries and scoped lockouts."""

    _PHASE_SELECTION_VALUES = {"P1", "P1_P2", "P1_P2_P3"}

    if TYPE_CHECKING:  # pragma: no cover
        @classmethod
        def _phase_selection_is_upshift(
            cls,
            current_selection: PhaseSelection,
            target_selection: PhaseSelection,
        ) -> bool: ...

        @staticmethod
        def _auto_phase_policy(svc: object) -> _PhaseSwitchMismatchPolicy | None: ...

    @classmethod
    def _phase_switch_mismatch_retry_active(
        cls,
        svc: _PhaseSwitchMismatchState,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
    ) -> bool:
        if not cls._phase_selection_is_upshift(current_selection, target_selection):
            return False
        mismatch_at = cls._phase_switch_mismatch_timestamp(svc, target_selection)
        if mismatch_at is None:
            return False
        retry_seconds = cls._phase_switch_mismatch_retry_seconds(svc)
        elapsed_seconds = max(0.0, float(now) - mismatch_at)
        return elapsed_seconds < retry_seconds

    @staticmethod
    def _phase_switch_mismatch_timestamp(
        svc: _PhaseSwitchMismatchState,
        target_selection: PhaseSelection,
    ) -> float | None:
        mismatch_selection = svc._phase_switch_last_mismatch_selection
        if mismatch_selection is None:
            return None
        if normalize_phase_selection(mismatch_selection) != target_selection:
            return None
        return finite_float_or_none(svc._phase_switch_last_mismatch_at)

    @classmethod
    def _phase_switch_mismatch_retry_seconds(cls, svc: object) -> float:
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is not None:
            return max(0.0, float(getattr(phase_policy, "mismatch_retry_seconds", 300.0)))
        return max(0.0, float(getattr(svc, "auto_phase_mismatch_retry_seconds", 300.0)))

    @classmethod
    def _phase_switch_lockout_threshold(cls, svc: object) -> int:
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is not None:
            return max(0, int(getattr(phase_policy, "mismatch_lockout_count", 3)))
        return max(0, int(getattr(svc, "auto_phase_mismatch_lockout_count", 3)))

    @classmethod
    def _phase_switch_lockout_seconds(cls, svc: object) -> float:
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is not None:
            return max(0.0, float(getattr(phase_policy, "mismatch_lockout_seconds", 1800.0)))
        return max(0.0, float(getattr(svc, "auto_phase_mismatch_lockout_seconds", 1800.0)))

    @classmethod
    def _phase_switch_mismatch_counts(cls, svc: _PhaseSwitchMismatchState) -> dict[str, int]:
        counts = getattr(svc, "_phase_switch_mismatch_counts", None)
        if isinstance(counts, dict):
            normalized = cls._normalized_phase_switch_mismatch_counts(counts)
            svc._phase_switch_mismatch_counts = normalized
            return normalized
        empty_counts: dict[str, int] = {}
        svc._phase_switch_mismatch_counts = empty_counts
        return empty_counts

    @staticmethod
    def _normalized_phase_switch_mismatch_counts(counts: Mapping[object, object]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for selection, count in counts.items():
            if selection not in _RelayPhaseSwitchMismatch._PHASE_SELECTION_VALUES:
                continue
            if not isinstance(count, int) or isinstance(count, bool):
                continue
            normalized[str(selection)] = max(0, count)
        return normalized

    @classmethod
    def _phase_switch_mismatch_count(cls, svc: _PhaseSwitchMismatchState, selection: PhaseSelection) -> int:
        return max(0, int(cls._phase_switch_mismatch_counts(svc).get(selection, 0)))

    @classmethod
    def _remember_phase_switch_mismatch(
        cls,
        svc: _PhaseSwitchMismatchState,
        selection: PhaseSelection,
        now: float,
    ) -> int:
        counts = cls._phase_switch_mismatch_counts(svc)
        next_count = max(0, int(counts.get(selection, 0))) + 1
        counts[selection] = next_count
        svc._phase_switch_mismatch_active = True
        svc._phase_switch_last_mismatch_selection = selection
        svc._phase_switch_last_mismatch_at = float(now)
        return next_count

    @classmethod
    def _clear_phase_switch_mismatch_tracking(
        cls,
        svc: _PhaseSwitchMismatchState,
        selection: PhaseSelection | None = None,
    ) -> None:
        svc._phase_switch_mismatch_active = False
        if selection is None:
            svc._phase_switch_mismatch_counts = {}
            svc._phase_switch_last_mismatch_selection = None
            svc._phase_switch_last_mismatch_at = None
            return
        counts = cls._phase_switch_mismatch_counts(svc)
        counts.pop(selection, None)
        if getattr(svc, "_phase_switch_last_mismatch_selection", None) == selection:
            svc._phase_switch_last_mismatch_selection = None
            svc._phase_switch_last_mismatch_at = None

    @staticmethod
    def _clear_phase_switch_lockout(svc: _PhaseSwitchMismatchState) -> None:
        svc._phase_switch_lockout_selection = None
        svc._phase_switch_lockout_reason = ""
        svc._phase_switch_lockout_at = None
        svc._phase_switch_lockout_until = None

    @classmethod
    def _engage_phase_switch_lockout(
        cls,
        svc: _PhaseSwitchMismatchState,
        selection: PhaseSelection,
        now: float,
    ) -> None:
        duration_seconds = cls._phase_switch_lockout_seconds(svc)
        if duration_seconds <= 0.0:
            cls._clear_phase_switch_lockout(svc)
            return
        svc._phase_switch_lockout_selection = selection
        svc._phase_switch_lockout_reason = "mismatch-threshold"
        svc._phase_switch_lockout_at = float(now)
        svc._phase_switch_lockout_until = float(now) + duration_seconds

    @classmethod
    def _phase_switch_lockout_active(
        cls,
        svc: _PhaseSwitchMismatchState,
        now: float,
        selection: PhaseSelection | None = None,
    ) -> bool:
        normalized_selection = cls._current_lockout_selection(svc, now)
        return normalized_selection is not None and (selection is None or normalized_selection == selection)

    @classmethod
    def _current_lockout_selection(
        cls,
        svc: _PhaseSwitchMismatchState,
        now: float,
    ) -> PhaseSelection | None:
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        if lockout_selection is None or lockout_until is None:
            return None
        if float(now) >= lockout_until:
            cls._clear_phase_switch_lockout(svc)
            return None
        return cls._active_lockout_selection(svc, lockout_selection)

    @classmethod
    def _active_lockout_selection(
        cls,
        svc: _PhaseSwitchMismatchState,
        lockout_selection: object,
    ) -> PhaseSelection | None:
        normalized_selection = normalize_phase_selection_or_none(lockout_selection)
        if normalized_selection is None:
            cls._clear_phase_switch_lockout(svc)
        return normalized_selection

    @classmethod
    def _phase_switch_fallback_selection(
        cls,
        svc: _PhaseSwitchMismatchState,
        observed_selection: PhaseSelection | None,
        pending_selection: PhaseSelection,
    ) -> PhaseSelection:
        if observed_selection is not None:
            return observed_selection
        active_selection = normalize_phase_selection_or_none(getattr(svc, "active_phase_selection", None))
        if active_selection is not None:
            return active_selection
        requested_selection = normalize_phase_selection_or_none(getattr(svc, "requested_phase_selection", None))
        return requested_selection if requested_selection is not None else pending_selection
