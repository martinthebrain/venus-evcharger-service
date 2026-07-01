# SPDX-License-Identifier: GPL-3.0-or-later
"""Learning-state normalization helpers used during update cycles."""

from __future__ import annotations

import math
from typing import ClassVar, Protocol, SupportsFloat, SupportsIndex, TypeAlias, TypedDict

from venus_evcharger.core.contracts import normalize_learning_phase, normalize_learning_state
from venus_evcharger.update.victron_ess_balance import _UpdateCycleVictronEssBalance

_NumericInput: TypeAlias = str | bytes | bytearray | SupportsFloat | SupportsIndex | None


class _LearningRuntimeService(Protocol):
    learned_charge_power_state: str
    learned_charge_power_watts: float | None
    learned_charge_power_updated_at: float | None
    learned_charge_power_learning_since: float | None
    learned_charge_power_sample_count: int
    learned_charge_power_phase: str | None
    learned_charge_power_voltage: float | None
    learned_charge_power_signature_mismatch_sessions: int
    learned_charge_power_signature_checked_session_started_at: float | None
    phase: str
    max_current: float
    voltage_mode: str
    _last_voltage: float | None
    auto_learn_charge_power_max_age_seconds: float


class _LearningTrackingSnapshot(TypedDict):
    state: str
    power: float | None
    updated_at: float | None
    learning_since: float | None
    sample_count: int
    phase_signature: str | None
    voltage_signature: float | None
    signature_mismatch_sessions: int
    checked_session_started_at: float | None


class _UpdateCycleLearningRuntime(_UpdateCycleVictronEssBalance):
    LEARNED_POWER_STABLE_TOLERANCE_RATIO: ClassVar[float]
    LEARNED_POWER_STABLE_TOLERANCE_WATTS: ClassVar[float]
    LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS: ClassVar[float]
    service: _LearningRuntimeService

    @staticmethod
    def _normalize_learned_charge_power_state(value: object) -> str:
        """Return one supported learned-power state string."""
        return str(normalize_learning_state(value))

    @staticmethod
    def _normalize_learned_charge_power_phase(value: object) -> str | None:
        """Return one supported phase signature for a learned charging profile."""
        normalized = normalize_learning_phase(value)
        return None if normalized is None else str(normalized)

    @classmethod
    def _set_learning_tracking(
        cls,
        svc: _LearningRuntimeService,
        *,
        state: str,
        learned_power: float | None,
        updated_at: float | None,
        learning_since: float | None,
        sample_count: int,
        phase_signature: str | None,
        voltage_signature: float | None,
        signature_mismatch_sessions: int,
        checked_session_started_at: float | None,
    ) -> bool:
        """Apply one coherent learned-power snapshot and report whether it changed."""
        normalized = cls._normalized_learning_tracking_values(
            state=state,
            learned_power=learned_power,
            updated_at=updated_at,
            learning_since=learning_since,
            sample_count=sample_count,
            phase_signature=phase_signature,
            voltage_signature=voltage_signature,
            signature_mismatch_sessions=signature_mismatch_sessions,
            checked_session_started_at=checked_session_started_at,
        )
        changed = cls._learning_tracking_changed(svc, normalized)
        cls._apply_learning_tracking(svc, normalized)
        return changed

    @staticmethod
    def _learning_tracking_changed(
        svc: _LearningRuntimeService,
        normalized: _LearningTrackingSnapshot,
    ) -> bool:
        """Return whether a normalized learned-power snapshot differs from the service."""
        return any(
            (
                getattr(svc, "learned_charge_power_state", None) != normalized["state"],
                getattr(svc, "learned_charge_power_watts", None) != normalized["power"],
                getattr(svc, "learned_charge_power_updated_at", None) != normalized["updated_at"],
                getattr(svc, "learned_charge_power_learning_since", None) != normalized["learning_since"],
                getattr(svc, "learned_charge_power_sample_count", None) != normalized["sample_count"],
                getattr(svc, "learned_charge_power_phase", None) != normalized["phase_signature"],
                getattr(svc, "learned_charge_power_voltage", None) != normalized["voltage_signature"],
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", None)
                != normalized["signature_mismatch_sessions"],
                getattr(svc, "learned_charge_power_signature_checked_session_started_at", None)
                != normalized["checked_session_started_at"],
            )
        )

    @staticmethod
    def _apply_learning_tracking(
        svc: _LearningRuntimeService,
        normalized: _LearningTrackingSnapshot,
    ) -> None:
        """Store a normalized learned-power snapshot on the service."""
        svc.learned_charge_power_state = normalized["state"]
        svc.learned_charge_power_watts = normalized["power"]
        svc.learned_charge_power_updated_at = normalized["updated_at"]
        svc.learned_charge_power_learning_since = normalized["learning_since"]
        svc.learned_charge_power_sample_count = normalized["sample_count"]
        svc.learned_charge_power_phase = normalized["phase_signature"]
        svc.learned_charge_power_voltage = normalized["voltage_signature"]
        svc.learned_charge_power_signature_mismatch_sessions = normalized["signature_mismatch_sessions"]
        svc.learned_charge_power_signature_checked_session_started_at = normalized["checked_session_started_at"]

    @staticmethod
    def _normalized_learning_power_value(value: _NumericInput) -> float | None:
        """Return the normalized learned charging power in watts."""
        return None if value is None else round(float(value), 1)

    @staticmethod
    def _normalized_learning_timestamp(value: _NumericInput) -> float | None:
        """Return one normalized learned-power timestamp."""
        return None if value is None else float(value)

    @staticmethod
    def _normalized_learning_count(value: int) -> int:
        """Return one normalized non-negative learning counter."""
        return max(0, int(value))

    @classmethod
    def _normalized_learning_tracking_values(
        cls,
        *,
        state: str,
        learned_power: float | None,
        updated_at: float | None,
        learning_since: float | None,
        sample_count: int,
        phase_signature: str | None,
        voltage_signature: float | None,
        signature_mismatch_sessions: int,
        checked_session_started_at: float | None,
    ) -> _LearningTrackingSnapshot:
        """Normalize one coherent learned-power snapshot before storing it on the service."""
        return {
            "state": cls._normalize_learned_charge_power_state(state),
            "power": cls._normalized_learning_power_value(learned_power),
            "updated_at": cls._normalized_learning_timestamp(updated_at),
            "learning_since": cls._normalized_learning_timestamp(learning_since),
            "sample_count": cls._normalized_learning_count(sample_count),
            "phase_signature": cls._normalize_learned_charge_power_phase(phase_signature),
            "voltage_signature": cls._normalized_learning_power_value(voltage_signature),
            "signature_mismatch_sessions": cls._normalized_learning_count(signature_mismatch_sessions),
            "checked_session_started_at": cls._normalized_learning_timestamp(checked_session_started_at),
        }

    @classmethod
    def _learning_stability_tolerance(cls, reference_power: float) -> float:
        """Return the allowed measurement deviation before learning restarts."""
        return max(
            float(cls.LEARNED_POWER_STABLE_TOLERANCE_WATTS),
            abs(float(reference_power)) * float(cls.LEARNED_POWER_STABLE_TOLERANCE_RATIO),
        )

    @staticmethod
    def _learning_phase_count(phase: str) -> float:
        """Return the configured number of charging phases for plausibility checks."""
        return 3.0 if str(phase).strip().upper() == "3P" else 1.0

    def _current_learning_phase_signature(self) -> str | None:
        """Return the configured phase signature used for learned-power validation."""
        return self._normalize_learned_charge_power_phase(getattr(self.service, "phase", "L1"))

    def _current_learning_voltage_signature(self, voltage: float) -> float | None:
        """Return the best current voltage signature for learned-power tracking."""
        if float(voltage) > 0:
            return float(voltage)
        last_voltage = getattr(self.service, "_last_voltage", None)
        if last_voltage is None or float(last_voltage) <= 0:
            return None
        return float(last_voltage)

    @classmethod
    def _voltage_signature_tolerance(cls, reference_voltage: float) -> float:
        """Return the allowed voltage drift before a learned signature counts as changed."""
        return max(
            float(cls.LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS),
            abs(float(reference_voltage)) * float(cls.LEARNED_POWER_STABLE_TOLERANCE_RATIO),
        )

    def _plausible_learning_power_max(self, voltage: float) -> float:
        """Return a conservative upper bound for a valid charging-power sample."""
        svc = self.service
        effective_voltage = float(voltage) if float(voltage) > 0 else float(
            getattr(svc, "_last_voltage", 230.0) or 230.0
        )
        if self._learning_phase_count(getattr(svc, "phase", "L1")) == 3.0 and str(
            getattr(svc, "voltage_mode", "phase")
        ).strip().lower() != "phase":
            effective_voltage = effective_voltage / math.sqrt(3.0)
        phase_count = self._learning_phase_count(getattr(svc, "phase", "L1"))
        max_current = max(float(getattr(svc, "max_current", 16.0)), 0.0)
        return max_current * effective_voltage * phase_count * 1.1

    def _is_learned_charge_power_stale(self, now: float) -> bool:
        """Return True when the persisted learned value is too old for reuse."""
        svc = self.service
        max_age_seconds = float(getattr(svc, "auto_learn_charge_power_max_age_seconds", 21600.0))
        if max_age_seconds <= 0:
            return False
        updated_at = getattr(svc, "learned_charge_power_updated_at", None)
        if updated_at is None:
            return True
        return (float(now) - float(updated_at)) > max_age_seconds
