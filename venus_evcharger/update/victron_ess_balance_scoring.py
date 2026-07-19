# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure scoring rules for Victron ESS balance learning."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_learning_profiles_support import _victron_ess_balance_profile_counter


class VictronEssTelemetryScorer:
    """Calculate learning scores without owning runtime state."""

    @staticmethod
    def _optional_float(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def ewma_learned_value(current: float | None, sample: float, samples: int) -> float:
        if current is None or samples <= 0:
            return float(sample)
        return (0.25 * float(sample)) + (0.75 * float(current))

    @staticmethod
    def _profile_sample_count(profile: dict[str, Any]) -> int:
        delay_samples = _victron_ess_balance_profile_counter(profile, "delay_samples")
        gain_samples = _victron_ess_balance_profile_counter(profile, "gain_samples")
        outcome_samples = _victron_ess_balance_profile_counter(
            profile, "settled_count"
        ) + _victron_ess_balance_profile_counter(profile, "overshoot_count")
        return max(delay_samples, gain_samples, outcome_samples)

    @staticmethod
    def stability_score_values(
        settled_count: int,
        overshoot_count: int,
        estimated_gain: float | None,
        response_delay_seconds: float | None,
    ) -> float:
        total_outcomes = settled_count + overshoot_count
        return clamped_stability_score(
            settle_ratio_value(settled_count, total_outcomes),
            gain_bonus_value(estimated_gain),
            overshoot_penalty_value(overshoot_count, total_outcomes),
            delay_penalty_value(response_delay_seconds),
        )

    def stability_score(self, svc: object) -> float:
        settled_count = max(0, int(getattr(svc, "_victron_ess_balance_telemetry_settled_count")))
        overshoot_count = max(0, int(getattr(svc, "_victron_ess_balance_telemetry_overshoot_count")))
        estimated_gain = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_estimated_gain"))
        response_delay = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds")
        )
        return self.stability_score_values(settled_count, overshoot_count, estimated_gain, response_delay)

    @staticmethod
    def variance_ratio(mean: float | None, mad: float | None, floor: float) -> float:
        if mean is None or mean == 0.0 or mad is None:
            return 1.0
        return max(0.0, 1.0 - (float(mad) / max(float(floor), float(mean))))

    @classmethod
    def variance_score(
        cls,
        delay_mean: float | None,
        delay_mad: float | None,
        gain_mean: float | None,
        gain_mad: float | None,
    ) -> float:
        return (
            cls.variance_ratio(delay_mean, delay_mad, 1.0)
            + cls.variance_ratio(gain_mean, gain_mad, 0.1)
        ) / 2.0

    def regime_consistency_score(self, profile: dict[str, Any]) -> float:
        sample_score = min(1.0, float(self._profile_sample_count(profile)) / 4.0)
        stability_score = self._optional_float(profile.get("stability_score")) or 0.0
        variance_score = self._optional_float(profile.get("response_variance_score")) or 0.0
        return max(0.0, min(1.0, (0.3 * sample_score) + (0.35 * stability_score) + (0.35 * variance_score)))

    def reproducibility_score(self, profile: dict[str, Any]) -> float:
        settled_count = max(0, int(profile["settled_count"]))
        overshoot_count = max(0, int(profile["overshoot_count"]))
        total = settled_count + overshoot_count
        settle_ratio = 1.0 if total <= 0 else float(settled_count) / float(total)
        variance_score = self._optional_float(profile.get("response_variance_score")) or 0.0
        return max(0.0, min(1.0, (0.6 * settle_ratio) + (0.4 * variance_score)))


def settle_ratio_value(settled_count: int, total_outcomes: int) -> float:
    return 1.0 if total_outcomes <= 0 else float(settled_count) / float(total_outcomes)


def overshoot_penalty_value(overshoot_count: int, total_outcomes: int) -> float:
    return 0.0 if total_outcomes <= 0 else min(0.6, float(overshoot_count) / float(total_outcomes))


def gain_bonus_value(estimated_gain: float | None) -> float:
    return 0.15 if estimated_gain is not None and estimated_gain > 0.0 else 0.0


def delay_penalty_value(response_delay_seconds: float | None) -> float:
    if response_delay_seconds is None:
        return 0.0
    return min(0.2, max(0.0, response_delay_seconds - 5.0) / 50.0)


def clamped_stability_score(
    settle_ratio: float,
    gain_bonus: float,
    overshoot_penalty: float,
    delay_penalty: float,
) -> float:
    return max(0.0, min(1.0, 0.45 + (0.4 * settle_ratio) + gain_bonus - overshoot_penalty - delay_penalty))
