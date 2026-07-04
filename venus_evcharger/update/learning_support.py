# SPDX-License-Identifier: GPL-3.0-or-later
"""Sample-window helpers for learned charging-power tracking."""

from __future__ import annotations

from venus_evcharger.update.learning_engine import (
    LearningEngine,
    LearningPlausibilityConfig,
    LearningStableConfig,
    LearningWindowConfig,
)
from venus_evcharger.update.learning_profile import normalized_learning_score
from venus_evcharger.update.learning_signature import _UpdateCycleLearningSignature


class _UpdateCycleLearningSupport(_UpdateCycleLearningSignature):
    _MIN_STABLE_LEARNING_SCORE = 0.65

    def _learning_window_status(self, now: float) -> tuple[str, float | None]:
        """Return whether the current charging session is ready for learning samples."""
        charging_started_at = getattr(self.service, "charging_started_at", None)
        return LearningEngine.window_status(
            None if charging_started_at is None else float(charging_started_at),
            LearningWindowConfig(
                start_delay_seconds=float(
                    getattr(self.service, "auto_learn_charge_power_start_delay_seconds", 30.0)
                ),
                window_seconds=float(getattr(self.service, "auto_learn_charge_power_window_seconds", 180.0)),
            ),
            now,
        )

    def _accepted_learning_sample(self, power: float, voltage: float) -> float | None:
        """Return one plausible learning sample or ``None`` when it should be ignored."""
        measured_power, _reason = self._accepted_learning_sample_result(power, voltage)
        return measured_power

    def _accepted_learning_sample_result(self, power: float, voltage: float) -> tuple[float | None, str]:
        """Return one plausible learning sample and the explainable outcome reason."""
        return LearningEngine.accepted_sample(
            power,
            LearningPlausibilityConfig(
                min_watts=float(getattr(self.service, "auto_learn_charge_power_min_watts", 500.0)),
                max_watts=self._plausible_learning_power_max(voltage),
            ),
        )

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
        previous_voltage_signature = getattr(self.service, "learned_charge_power_voltage", None)
        return LearningEngine.smoothed_values(
            previous_value,
            measured_power,
            None if previous_voltage_signature is None else float(previous_voltage_signature),
            current_voltage_signature,
            alpha,
        )

    def _restart_learning_sample(
        self,
        measured_power: float,
        now: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        *,
        reason: str = "learning-restart",
        detail: str = "new-baseline",
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
                confidence=self._learning_confidence_score("learning", 1, now, now, 1.0),
                stability_score=1.0,
                reason=reason,
                detail=detail,
            )
        )

    def _learning_sample_stability_score(self, measured_power: float, previous_value: float) -> float:
        """Return how well one sample matches the current learned baseline."""
        tolerance = self._learning_stability_tolerance(previous_value)
        return LearningEngine.sample_stability_score(measured_power, previous_value, tolerance)

    def _combined_learning_stability_score(self, sample_score: float) -> float:
        """Return an EWMA stability score for the current learning session."""
        previous_score = normalized_learning_score(
            getattr(self.service, "learned_charge_power_stability_score", 0.0)
        )
        return LearningEngine.combined_stability_score(previous_score, sample_score)

    def _adaptive_stable_learning_seconds(self, stability_score: float) -> float:
        """Return the minimum learning span adjusted by observed sample stability."""
        return LearningEngine.adaptive_stable_seconds(
            float(self.LEARNED_POWER_STABLE_MIN_SECONDS),
            stability_score,
        )

    def _learning_confidence_score(
        self,
        state: str,
        sample_count: int,
        learning_since: float | None,
        now: float,
        stability_score: float,
    ) -> float:
        """Return a confidence estimate for the current learned-power profile."""
        return LearningEngine.confidence_score(
            state,
            sample_count,
            self.LEARNED_POWER_STABLE_MIN_SAMPLES,
            learning_since,
            now,
            stability_score,
            self._adaptive_stable_learning_seconds(stability_score),
        )

    def _stable_learning_ready(self, sample_count: int, learning_span: float, stability_score: float) -> bool:
        """Return whether the current learning window is mature enough to become stable."""
        return LearningEngine.stable_ready(
            sample_count,
            learning_span,
            stability_score,
            LearningStableConfig(
                min_samples=self.LEARNED_POWER_STABLE_MIN_SAMPLES,
                base_seconds=float(self.LEARNED_POWER_STABLE_MIN_SECONDS),
                min_stability_score=self._MIN_STABLE_LEARNING_SCORE,
            ),
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
        sample_count_value = getattr(self.service, "learned_charge_power_sample_count", None)
        sample_count = 1 if sample_count_value is None else max(1, int(sample_count_value))
        sample_stability_score = self._learning_sample_stability_score(measured_power, previous_value)
        if sample_stability_score <= 0:
            return self._restart_learning_sample(
                measured_power,
                now,
                current_phase_signature,
                current_voltage_signature,
                reason="learning-restart",
                detail="sample-unstable",
            )
        sample_count += 1
        learning_span = float(now) - float(learning_since)
        stability_score = self._combined_learning_stability_score(sample_stability_score)
        confidence = self._learning_confidence_score(
            "learning",
            sample_count,
            float(learning_since),
            now,
            stability_score,
        )
        if self._stable_learning_ready(sample_count, learning_span, stability_score):
            return self._apply_stable_learning(
                learned_power,
                updated_at=now,
                phase_signature=current_phase_signature,
                voltage_signature=learned_voltage_signature,
                signature_mismatch_sessions=0,
                checked_session_started_at=charging_started_at,
                confidence=1.0,
                stability_score=stability_score,
                reason="learning-stable",
                detail=f"samples={sample_count}",
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
                confidence=confidence,
                stability_score=stability_score,
                reason="learning-sample",
                detail=f"samples={sample_count}",
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
            stability_score = self._learning_sample_stability_score(measured_power, previous_value)
            return self._apply_stable_learning(
                learned_power,
                updated_at=now,
                phase_signature=current_phase_signature,
                voltage_signature=learned_voltage_signature,
                signature_mismatch_sessions=0,
                checked_session_started_at=charging_started_at,
                confidence=1.0,
                stability_score=stability_score,
                reason="learning-stable-refresh",
                detail="stable-sample",
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
