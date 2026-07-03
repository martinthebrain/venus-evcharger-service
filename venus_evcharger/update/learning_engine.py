# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure learned charge-power decision helpers.

The update-cycle controller owns service state and persistence. This module owns
the small deterministic rules that decide whether a sample is usable, how stable
it is, and when a learning window is mature enough to become stable.
"""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.update.learning_profile import normalized_learning_score


@dataclass(frozen=True)
class LearningWindowConfig:
    """Timing limits for one learned-power sample window."""

    start_delay_seconds: float
    window_seconds: float


@dataclass(frozen=True)
class LearningPlausibilityConfig:
    """Power bounds for accepting one charging-power learning sample."""

    min_watts: float
    max_watts: float


@dataclass(frozen=True)
class LearningStableConfig:
    """Maturity requirements for promoting learning to stable."""

    min_samples: int
    base_seconds: float
    min_stability_score: float


class LearningEngine:
    """Dependency-free learned-power rule engine."""

    @staticmethod
    def window_status(
        charging_started_at: float | None,
        config: LearningWindowConfig,
        now: float,
    ) -> tuple[str, float | None]:
        """Return whether the current charging session may contribute samples."""
        if charging_started_at is None:
            return "waiting", None
        session_started_at = float(charging_started_at)
        minimum_seconds = float(config.start_delay_seconds)
        elapsed = float(now) - session_started_at
        if elapsed < minimum_seconds:
            return "waiting", None
        window_seconds = float(config.window_seconds)
        if window_seconds > 0 and elapsed > minimum_seconds + window_seconds:
            return "expired", session_started_at
        return "ready", session_started_at

    @staticmethod
    def accepted_sample(power: float, config: LearningPlausibilityConfig) -> tuple[float | None, str]:
        """Return a normalized sample and an explainable accept/reject reason."""
        measured_power = float(power)
        if measured_power < float(config.min_watts):
            return None, "below-min"
        if measured_power > float(config.max_watts):
            return None, "above-plausible"
        return measured_power, "accepted"

    @staticmethod
    def smoothed_values(
        previous_power: float,
        measured_power: float,
        previous_voltage_signature: float | None,
        current_voltage_signature: float | None,
        alpha: float,
    ) -> tuple[float, float | None]:
        """Return EWMA-smoothed learned power and voltage signature."""
        learned_power = float(previous_power) + float(alpha) * (float(measured_power) - float(previous_power))
        if current_voltage_signature is None:
            return learned_power, previous_voltage_signature
        if previous_voltage_signature is None or float(previous_voltage_signature) <= 0:
            return learned_power, current_voltage_signature
        learned_voltage = float(previous_voltage_signature) + float(alpha) * (
            float(current_voltage_signature) - float(previous_voltage_signature)
        )
        return learned_power, learned_voltage

    @staticmethod
    def sample_stability_score(measured_power: float, previous_power: float, tolerance_watts: float) -> float:
        """Return one 0..1 score for how well a sample matches the current baseline."""
        tolerance = float(tolerance_watts)
        if tolerance <= 0:
            return 0.0
        deviation_ratio = abs(float(measured_power) - float(previous_power)) / tolerance
        return normalized_learning_score(1.0 - deviation_ratio)

    @staticmethod
    def combined_stability_score(previous_score: float, sample_score: float) -> float:
        """Return an EWMA stability score for the current learning session."""
        previous = normalized_learning_score(previous_score)
        if previous <= 0:
            return normalized_learning_score(sample_score)
        return normalized_learning_score(previous * 0.7 + normalized_learning_score(sample_score) * 0.3)

    @staticmethod
    def adaptive_stable_seconds(base_seconds: float, stability_score: float) -> float:
        """Return the required learning span adjusted by sample stability."""
        score = normalized_learning_score(stability_score)
        base = float(base_seconds)
        if score >= 0.9:
            return max(1.0, base * 0.5)
        if score < 0.75:
            return base * 1.25
        return base

    @staticmethod
    def confidence_score(
        state: str,
        sample_count: int,
        min_samples: int,
        learning_since: float | None,
        now: float,
        stability_score: float,
        adaptive_seconds: float,
    ) -> float:
        """Return a compact confidence estimate for one learned-power profile."""
        if state == "stable":
            return max(0.85, normalized_learning_score(stability_score))
        if state != "learning":
            return 0.0
        sample_progress = min(1.0, max(0, int(sample_count)) / max(1.0, float(min_samples)))
        span_seconds = 0.0 if learning_since is None else max(0.0, float(now) - float(learning_since))
        span_progress = min(1.0, max(0.25, span_seconds / max(1.0, float(adaptive_seconds))))
        return normalized_learning_score(sample_progress * span_progress * stability_score)

    @staticmethod
    def stable_ready(
        sample_count: int,
        learning_span: float,
        stability_score: float,
        config: LearningStableConfig,
    ) -> bool:
        """Return whether one learning window is mature enough to become stable."""
        required_seconds = LearningEngine.adaptive_stable_seconds(config.base_seconds, stability_score)
        return (
            int(sample_count) >= int(config.min_samples)
            and float(learning_span) >= required_seconds
            and normalized_learning_score(stability_score) >= normalized_learning_score(config.min_stability_score)
        )
