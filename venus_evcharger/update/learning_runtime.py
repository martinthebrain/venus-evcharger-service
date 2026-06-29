# SPDX-License-Identifier: GPL-3.0-or-later
"""Learning-state normalization helpers used during update cycles."""

from __future__ import annotations

import math
from typing import Any, ClassVar

from venus_evcharger.core.contracts import normalize_learning_phase, normalize_learning_state
from venus_evcharger.update.victron_ess_balance import _UpdateCycleVictronEssBalance


class _UpdateCycleLearningRuntime(_UpdateCycleVictronEssBalance):
    LEARNED_POWER_STABLE_TOLERANCE_RATIO: ClassVar[float]
    LEARNED_POWER_STABLE_TOLERANCE_WATTS: ClassVar[float]
    LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS: ClassVar[float]
    service: Any

    @staticmethod
    def _normalize_learned_charge_power_state(value: Any) -> str:
        """Return one supported learned-power state string."""
        return str(normalize_learning_state(value))

    @staticmethod
    def _normalize_learned_charge_power_phase(value: Any) -> str | None:
        """Return one supported phase signature for a learned charging profile."""
        normalized = normalize_learning_phase(value)
        return None if normalized is None else str(normalized)

    @classmethod
    def _set_learning_tracking(
        cls,
        svc: Any,
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
        attr_map = {
            "state": "learned_charge_power_state",
            "power": "learned_charge_power_watts",
            "updated_at": "learned_charge_power_updated_at",
            "learning_since": "learned_charge_power_learning_since",
            "sample_count": "learned_charge_power_sample_count",
            "phase_signature": "learned_charge_power_phase",
            "voltage_signature": "learned_charge_power_voltage",
            "signature_mismatch_sessions": "learned_charge_power_signature_mismatch_sessions",
            "checked_session_started_at": "learned_charge_power_signature_checked_session_started_at",
        }
        changed = False
        for key, attr_name in attr_map.items():
            if getattr(svc, attr_name, None) != normalized[key]:
                changed = True
            setattr(svc, attr_name, normalized[key])
        return changed

    @staticmethod
    def _normalized_learning_power_value(value: Any) -> float | None:
        """Return the normalized learned charging power in watts."""
        return None if value is None else round(float(value), 1)

    @staticmethod
    def _normalized_learning_timestamp(value: Any) -> float | None:
        """Return one normalized learned-power timestamp."""
        return None if value is None else float(value)

    @staticmethod
    def _normalized_learning_count(value: Any) -> int:
        """Return one normalized non-negative learning counter."""
        return max(0, int(value))

    @classmethod
    def _normalized_learning_tracking_values(cls, **values: Any) -> dict[str, Any]:
        """Normalize one coherent learned-power snapshot before storing it on the service."""
        return {
            "state": cls._normalize_learned_charge_power_state(values["state"]),
            "power": cls._normalized_learning_power_value(values["learned_power"]),
            "updated_at": cls._normalized_learning_timestamp(values["updated_at"]),
            "learning_since": cls._normalized_learning_timestamp(values["learning_since"]),
            "sample_count": cls._normalized_learning_count(values["sample_count"]),
            "phase_signature": cls._normalize_learned_charge_power_phase(values["phase_signature"]),
            "voltage_signature": cls._normalized_learning_power_value(values["voltage_signature"]),
            "signature_mismatch_sessions": cls._normalized_learning_count(
                values["signature_mismatch_sessions"]
            ),
            "checked_session_started_at": cls._normalized_learning_timestamp(
                values["checked_session_started_at"]
            ),
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
