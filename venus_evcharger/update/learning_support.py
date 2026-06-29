# SPDX-License-Identifier: GPL-3.0-or-later
"""Sample-window helpers for learned charging-power tracking."""

from __future__ import annotations

from typing import Any

from venus_evcharger.update.learning_signature import _UpdateCycleLearningSignature


class _UpdateCycleLearningSupport(_UpdateCycleLearningSignature):
    def _learning_window_status(self, now: float) -> tuple[str, float | None]:
        """Return whether the current charging session is ready for learning samples."""
        charging_started_at = getattr(self.service, "charging_started_at", None)
        if charging_started_at is None:
            return "waiting", None
        session_started_at = float(charging_started_at)
        minimum_seconds = float(getattr(self.service, "auto_learn_charge_power_start_delay_seconds", 30.0))
        if (float(now) - session_started_at) < minimum_seconds:
            return "waiting", None
        learning_window_seconds = float(getattr(self.service, "auto_learn_charge_power_window_seconds", 180.0))
        if learning_window_seconds > 0 and (float(now) - session_started_at) > (minimum_seconds + learning_window_seconds):
            return "expired", session_started_at
        return "ready", session_started_at

    def _accepted_learning_sample(self, power: float, voltage: float) -> float | None:
        """Return one plausible learning sample or ``None`` when it should be ignored."""
        measured_power = float(power)
        if measured_power < float(getattr(self.service, "auto_learn_charge_power_min_watts", 500.0)):
            return None
        if measured_power > self._plausible_learning_power_max(voltage):
            return None
        return measured_power

    def _learning_session_result(
        self,
        enabled: bool,
        pm_confirmed: bool,
        relay_on: bool,
        status: int,
        now: float,
        current_state: str,
    ) -> tuple[float | None, bool | None]:
        """Return an eligible session start or the immediate learning decision."""
        if self._learning_measurement_ignored(enabled, pm_confirmed):
            return None, False
        if not self._learning_measurement_active(relay_on, status):
            return None, self._inactive_learning_session_decision(current_state)
        learning_window_status, charging_started_at = self._learning_window_status(now)
        if learning_window_status == "ready":
            return charging_started_at, None
        return None, self._windowed_learning_session_decision(learning_window_status, current_state)

    @staticmethod
    def _learning_measurement_ignored(enabled: bool, pm_confirmed: bool) -> bool:
        """Return whether learning should ignore the current measurement immediately."""
        return not enabled or not pm_confirmed

    @staticmethod
    def _learning_measurement_active(relay_on: bool, status: int) -> bool:
        """Return whether the current measurement represents active charging."""
        return bool(relay_on) and int(status) == 2

    def _inactive_learning_session_decision(self, current_state: str) -> bool:
        """Return the learning result when charging is not actively running."""
        if current_state != "learning":
            return False
        return self._clear_learning_tracking()

    def _windowed_learning_session_decision(
        self,
        learning_window_status: str,
        current_state: str,
    ) -> bool:
        """Return the learning result after inspecting the current learning window state."""
        if learning_window_status == "expired" and current_state == "learning":
            return self._clear_learning_tracking()
        return False

    def _should_restart_learning(
        self,
        current_state: str,
        previous: float | None,
        now: float,
    ) -> bool:
        """Return whether learning must restart from the next accepted sample."""
        return (
            current_state in {"unknown", "stale"}
            or previous is None
            or previous <= 0
            or self._is_learned_charge_power_stale(now)
        )

    def _smoothed_learning_values(
        self,
        previous_value: float,
        measured_power: float,
        current_voltage_signature: float | None,
    ) -> tuple[float, float | None]:
        """Return EWMA-smoothed learned power and voltage signature."""
        alpha = float(getattr(self.service, "auto_learn_charge_power_alpha", 0.2))
        learned_power = previous_value + alpha * (measured_power - previous_value)
        previous_voltage_signature = getattr(self.service, "learned_charge_power_voltage", None)
        if current_voltage_signature is None:
            learned_voltage_signature = previous_voltage_signature
        elif previous_voltage_signature is None or float(previous_voltage_signature) <= 0:
            learned_voltage_signature = current_voltage_signature
        else:
            learned_voltage_signature = float(previous_voltage_signature) + alpha * (
                float(current_voltage_signature) - float(previous_voltage_signature)
            )
        return learned_power, learned_voltage_signature

    def _restart_learning_sample(
        self,
        measured_power: float,
        now: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
    ) -> bool:
        """Start or restart the learning window from the current sample."""
        return bool(
            self._set_learning_tracking(
            self.service,
            state="learning",
            learned_power=measured_power,
            updated_at=now,
            learning_since=now,
            sample_count=1,
            phase_signature=current_phase_signature,
            voltage_signature=current_voltage_signature,
            signature_mismatch_sessions=0,
            checked_session_started_at=None,
            )
        )

    def _apply_learning_progress(
        self,
        measured_power: float,
        previous_value: float,
        learned_power: float,
        learned_voltage_signature: float | None,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        charging_started_at: float,
        now: float,
    ) -> bool:
        """Advance learning from one accepted sample after smoothing."""
        learning_since = getattr(self.service, "learned_charge_power_learning_since", None)
        if learning_since is None:
            learning_since = now
        sample_count = max(1, int(getattr(self.service, "learned_charge_power_sample_count", 0)))
        if abs(measured_power - previous_value) > self._learning_stability_tolerance(previous_value):
            return self._restart_learning_sample(measured_power, now, current_phase_signature, current_voltage_signature)
        sample_count += 1
        learning_span = float(now) - float(learning_since)
        if sample_count >= self.LEARNED_POWER_STABLE_MIN_SAMPLES and learning_span >= self.LEARNED_POWER_STABLE_MIN_SECONDS:
            return self._apply_stable_learning(
                learned_power,
                updated_at=now,
                phase_signature=current_phase_signature,
                voltage_signature=learned_voltage_signature,
                signature_mismatch_sessions=0,
                checked_session_started_at=charging_started_at,
            )
        return bool(
            self._set_learning_tracking(
            self.service,
            state="learning",
            learned_power=learned_power,
            updated_at=now,
            learning_since=float(learning_since),
            sample_count=sample_count,
            phase_signature=current_phase_signature,
            voltage_signature=learned_voltage_signature,
            signature_mismatch_sessions=0,
            checked_session_started_at=None,
            )
        )

    def _apply_learning_sample(
        self,
        current_state: str,
        measured_power: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        charging_started_at: float,
        now: float,
    ) -> bool:
        """Update learned power from one accepted sample inside the learning window."""
        previous = getattr(self.service, "learned_charge_power_watts", None)
        if self._should_restart_learning(current_state, previous, now):
            return self._restart_learning_sample(
                measured_power,
                now,
                current_phase_signature,
                current_voltage_signature,
            )

        assert previous is not None
        previous_value = float(previous)
        learned_power, learned_voltage_signature = self._smoothed_learning_values(
            previous_value,
            measured_power,
            current_voltage_signature,
        )
        if current_state == "stable":
            return self._apply_stable_learning(
                learned_power,
                updated_at=now,
                phase_signature=current_phase_signature,
                voltage_signature=learned_voltage_signature,
                signature_mismatch_sessions=0,
                checked_session_started_at=charging_started_at,
            )
        return self._apply_learning_progress(
            measured_power,
            previous_value,
            learned_power,
            learned_voltage_signature,
            current_phase_signature,
            current_voltage_signature,
            charging_started_at,
            now,
        )
