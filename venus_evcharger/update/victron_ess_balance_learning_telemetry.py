# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias telemetry-learning helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .victron_ess_balance_learning_profiles_support import (
    _clear_victron_ess_balance_tracking_episode_state,
    _record_victron_ess_balance_tracking_command,
    _reset_victron_ess_balance_pid_integral_state,
    _reset_victron_ess_balance_pid_state,
    _victron_ess_balance_update_service_sample,
)


class _UpdateCycleVictronEssBalanceLearningTelemetryMixin:
    if TYPE_CHECKING:  # pragma: no cover
        @staticmethod
        def _optional_float(value: Any) -> float | None: ...
        @staticmethod
        def _victron_ess_balance_profile_sample_count(profile: dict[str, Any]) -> int: ...
        def _victron_ess_balance_update_profile_delay(
            self,
            svc: Any,
            profile_key: str,
            response_delay_seconds: float,
        ) -> None: ...
        def _victron_ess_balance_update_profile_gain(
            self,
            svc: Any,
            profile_key: str,
            gain_sample: float,
        ) -> None: ...
        def _victron_ess_balance_increment_profile_counter(
            self,
            svc: Any,
            profile_key: str,
            counter_name: str,
        ) -> None: ...
        def _enter_victron_ess_balance_overshoot_cooldown(self, svc: Any, now: float, reason: str) -> None: ...
        def _victron_ess_balance_telemetry_is_clean(
            self,
            svc: Any,
            cluster: dict[str, Any],
            source_error_w: float,
        ) -> tuple[bool, str]: ...
        def _victron_ess_balance_ev_power_w(self, svc: Any) -> float | None: ...
        def _victron_ess_balance_refresh_profile_stability(
            self,
            svc: Any,
            profile_key: str,
        ) -> None: ...
        def _populate_victron_ess_balance_telemetry_metrics(
            self,
            svc: Any,
            metrics: dict[str, Any],
        ) -> None: ...

    @staticmethod
    def _victron_ess_balance_improvement_stats(
        source_error_w: float,
        command_error_w_f: float,
        command_setpoint_w_f: float,
        base_setpoint_w: float,
    ) -> dict[str, float]:
        initial_abs_error_w = abs(command_error_w_f)
        current_abs_error_w = abs(float(source_error_w))
        return {
            "initial_abs_error_w": initial_abs_error_w,
            "current_abs_error_w": current_abs_error_w,
            "improvement_w": max(0.0, initial_abs_error_w - current_abs_error_w),
            "setpoint_bias_w": abs(command_setpoint_w_f - base_setpoint_w),
        }

    @staticmethod
    def _victron_ess_balance_is_overshoot(
        source_error_w: float,
        command_error_w_f: float,
        current_abs_error_w: float,
        improvement_threshold_w: float,
    ) -> bool:
        return bool(
            command_error_w_f != 0.0
            and float(source_error_w) != 0.0
            and (command_error_w_f * float(source_error_w)) < 0.0
            and current_abs_error_w >= improvement_threshold_w
        )

    def _victron_ess_balance_telemetry_command_state(
        self,
        svc: Any,
        profile_key: str,
    ) -> dict[str, Any]:
        return {
            "command_at": self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_command_at", None)),
            "command_error_w": self._optional_float(
                getattr(svc, "_victron_ess_balance_telemetry_last_command_error_w", None)
            ),
            "command_setpoint_w": self._optional_float(
                getattr(svc, "_victron_ess_balance_telemetry_last_command_setpoint_w", None)
            ),
            "command_profile_key": str(
                getattr(svc, "_victron_ess_balance_telemetry_last_command_profile_key", profile_key) or profile_key
            ).strip(),
            "command_response_recorded": bool(
                getattr(svc, "_victron_ess_balance_telemetry_command_response_recorded", False)
            ),
            "command_overshoot_recorded": bool(
                getattr(svc, "_victron_ess_balance_telemetry_command_overshoot_recorded", False)
            ),
            "command_settled_recorded": bool(
                getattr(svc, "_victron_ess_balance_telemetry_command_settled_recorded", False)
            ),
        }

    @staticmethod
    def _victron_ess_balance_telemetry_thresholds(svc: Any) -> tuple[float, float]:
        deadband_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
        )
        base_setpoint_w = float(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_base_setpoint_watts", 50.0) or 0.0
        )
        return deadband_w, base_setpoint_w

    def _victron_ess_balance_update_response_delay(
        self,
        svc: Any,
        command_profile_key: str,
        response_delay_seconds: float,
    ) -> None:
        _victron_ess_balance_update_service_sample(
            svc,
            response_delay_seconds,
            samples_attr="_victron_ess_balance_telemetry_delay_samples",
            value_attr="_victron_ess_balance_telemetry_response_delay_seconds",
            optional_float=self._optional_float,
            ewma=self._ewma_learned_value,
        )
        self._victron_ess_balance_update_profile_delay(svc, command_profile_key, response_delay_seconds)

    def _victron_ess_balance_update_gain(
        self,
        svc: Any,
        command_profile_key: str,
        gain_sample: float,
    ) -> None:
        _victron_ess_balance_update_service_sample(
            svc,
            gain_sample,
            samples_attr="_victron_ess_balance_telemetry_gain_samples",
            value_attr="_victron_ess_balance_telemetry_estimated_gain",
            optional_float=self._optional_float,
            ewma=self._ewma_learned_value,
        )
        self._victron_ess_balance_update_profile_gain(svc, command_profile_key, gain_sample)

    def _victron_ess_balance_mark_overshoot(
        self,
        svc: Any,
        now: float,
        command_profile_key: str,
    ) -> None:
        svc._victron_ess_balance_telemetry_command_overshoot_recorded = True
        svc._victron_ess_balance_telemetry_overshoot_count = int(
            getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0
        ) + 1
        self._victron_ess_balance_increment_profile_counter(svc, command_profile_key, "overshoot_count")
        self._enter_victron_ess_balance_overshoot_cooldown(svc, now, "overshoot_detected")
        self._reset_victron_ess_balance_pid_integral(svc, aggressive=True)

    def _victron_ess_balance_mark_settled(self, svc: Any, command_profile_key: str) -> None:
        svc._victron_ess_balance_telemetry_command_settled_recorded = True
        svc._victron_ess_balance_telemetry_settled_count = int(
            getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0
        ) + 1
        self._victron_ess_balance_increment_profile_counter(svc, command_profile_key, "settled_count")

    def _victron_ess_balance_maybe_record_response_delay(
        self,
        svc: Any,
        now: float,
        command_state: dict[str, Any],
        command_profile_key: str,
        improvement_w: float,
        improvement_threshold_w: float,
        command_at_f: float,
    ) -> None:
        if command_state["command_response_recorded"] or improvement_w < improvement_threshold_w:
            return
        self._victron_ess_balance_update_response_delay(
            svc,
            command_profile_key,
            max(0.0, float(now) - command_at_f),
        )
        command_state["command_response_recorded"] = True
        svc._victron_ess_balance_telemetry_command_response_recorded = True

    def _victron_ess_balance_maybe_record_gain(
        self,
        svc: Any,
        command_profile_key: str,
        improvement_w: float,
        setpoint_bias_w: float,
    ) -> None:
        if improvement_w <= 0.0 or setpoint_bias_w < 1.0:
            return
        self._victron_ess_balance_update_gain(
            svc,
            command_profile_key,
            improvement_w / setpoint_bias_w,
        )

    def _victron_ess_balance_maybe_mark_overshoot(
        self,
        svc: Any,
        now: float,
        source_error_w: float,
        command_state: dict[str, Any],
        command_profile_key: str,
        command_error_w_f: float,
        current_abs_error_w: float,
        improvement_threshold_w: float,
    ) -> None:
        if command_state["command_overshoot_recorded"]:
            return
        if not self._victron_ess_balance_is_overshoot(
            source_error_w,
            command_error_w_f,
            current_abs_error_w,
            improvement_threshold_w,
        ):
            return
        self._victron_ess_balance_mark_overshoot(svc, now, command_profile_key)
        command_state["command_overshoot_recorded"] = True

    def _victron_ess_balance_maybe_mark_settled(
        self,
        svc: Any,
        command_state: dict[str, Any],
        command_profile_key: str,
        current_abs_error_w: float,
        deadband_w: float,
    ) -> None:
        if command_state["command_settled_recorded"] or current_abs_error_w > deadband_w:
            return
        self._victron_ess_balance_mark_settled(svc, command_profile_key)
        command_state["command_settled_recorded"] = True

    def _victron_ess_balance_process_clean_episode(
        self,
        svc: Any,
        now: float,
        source_error_w: float,
        command_state: dict[str, Any],
        improvement_threshold_w: float,
        base_setpoint_w: float,
        deadband_w: float,
    ) -> tuple[bool, bool]:
        command_at_f = float(command_state["command_at"])
        command_error_w_f = float(command_state["command_error_w"])
        command_setpoint_w_f = float(command_state["command_setpoint_w"])
        command_profile_key = str(command_state["command_profile_key"])
        stats = self._victron_ess_balance_improvement_stats(
            source_error_w,
            command_error_w_f,
            command_setpoint_w_f,
            base_setpoint_w,
        )
        self._victron_ess_balance_maybe_record_response_delay(
            svc,
            now,
            command_state,
            command_profile_key,
            stats["improvement_w"],
            improvement_threshold_w,
            command_at_f,
        )
        self._victron_ess_balance_maybe_record_gain(
            svc,
            command_profile_key,
            stats["improvement_w"],
            stats["setpoint_bias_w"],
        )
        self._victron_ess_balance_maybe_mark_overshoot(
            svc,
            now,
            source_error_w,
            command_state,
            command_profile_key,
            command_error_w_f,
            stats["current_abs_error_w"],
            improvement_threshold_w,
        )
        self._victron_ess_balance_maybe_mark_settled(
            svc,
            command_state,
            command_profile_key,
            stats["current_abs_error_w"],
            deadband_w,
        )
        return bool(command_state["command_overshoot_recorded"]), bool(
            not command_state["command_settled_recorded"] and not command_state["command_overshoot_recorded"]
        )

    def _update_victron_ess_balance_telemetry(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source_error_w: float,
        metrics: dict[str, Any],
        profile_key: str,
    ) -> None:
        command_state = self._victron_ess_balance_telemetry_command_state(svc, profile_key)
        deadband_w, base_setpoint_w = self._victron_ess_balance_telemetry_thresholds(svc)
        improvement_threshold_w = max(10.0, deadband_w * 0.5)
        active_episode = all(
            command_state[key] is not None for key in ("command_at", "command_error_w", "command_setpoint_w")
        )
        overshoot_active = False
        settling_active = False
        telemetry_clean, telemetry_clean_reason = self._victron_ess_balance_telemetry_is_clean(
            svc,
            cluster,
            source_error_w,
        )
        metrics["battery_discharge_balance_victron_bias_telemetry_clean"] = int(telemetry_clean)
        metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"] = telemetry_clean_reason

        if active_episode and telemetry_clean:
            overshoot_active, settling_active = self._victron_ess_balance_process_clean_episode(
                svc,
                now,
                source_error_w,
                command_state,
                improvement_threshold_w,
                base_setpoint_w,
                deadband_w,
            )

        svc._victron_ess_balance_telemetry_overshoot_active = overshoot_active
        svc._victron_ess_balance_telemetry_settling_active = settling_active
        svc._victron_ess_balance_telemetry_last_observed_error_w = float(source_error_w)
        svc._victron_ess_balance_telemetry_last_observed_at = float(now)
        svc._victron_ess_balance_telemetry_last_grid_interaction_w = self._optional_float(
            cluster.get("battery_combined_grid_interaction_w")
        )
        svc._victron_ess_balance_telemetry_last_ac_power_w = self._optional_float(
            cluster.get("battery_combined_ac_power_w")
        )
        svc._victron_ess_balance_telemetry_last_ev_power_w = self._victron_ess_balance_ev_power_w(svc)
        svc._victron_ess_balance_telemetry_stability_score = self._victron_ess_balance_stability_score(svc)
        self._victron_ess_balance_refresh_profile_stability(
            svc,
            str(command_state.get("command_profile_key", "") or profile_key),
        )
        self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)

    @staticmethod
    def _ewma_learned_value(current: float | None, sample: float, samples: int) -> float:
        if current is None or samples <= 0:
            return float(sample)
        alpha = 0.25
        return (alpha * float(sample)) + ((1.0 - alpha) * float(current))

    @staticmethod
    def _victron_ess_balance_stability_score_values(
        settled_count: int,
        overshoot_count: int,
        estimated_gain: float | None,
        response_delay_seconds: float | None,
    ) -> float:
        total_outcomes = settled_count + overshoot_count
        settle_ratio = _victron_ess_balance_settle_ratio(settled_count, total_outcomes)
        overshoot_penalty = _victron_ess_balance_overshoot_penalty(overshoot_count, total_outcomes)
        gain_bonus = _victron_ess_balance_gain_bonus(estimated_gain)
        delay_penalty = _victron_ess_balance_delay_penalty(response_delay_seconds)
        return float(_victron_ess_balance_clamped_stability_score(settle_ratio, gain_bonus, overshoot_penalty, delay_penalty))

    @classmethod
    def _victron_ess_balance_stability_score(cls, svc: Any) -> float:
        settled_count = max(0, int(getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0))
        overshoot_count = max(0, int(getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0))
        estimated_gain = cls._optional_float(getattr(svc, "_victron_ess_balance_telemetry_estimated_gain", None))
        response_delay_seconds = cls._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
        )
        return cls._victron_ess_balance_stability_score_values(
            settled_count,
            overshoot_count,
            estimated_gain,
            response_delay_seconds,
        )

    @staticmethod
    def _victron_ess_balance_variance_ratio(mean: float | None, mad: float | None, floor: float) -> float:
        normalized_mean = None if mean is None else float(mean)
        if normalized_mean is None or normalized_mean == 0.0 or mad is None:
            return 1.0
        return max(0.0, 1.0 - (float(mad) / max(float(floor), normalized_mean)))

    @staticmethod
    def _victron_ess_balance_variance_score(
        delay_mean: float | None,
        delay_mad: float | None,
        gain_mean: float | None,
        gain_mad: float | None,
    ) -> float:
        delay_ratio = _UpdateCycleVictronEssBalanceLearningTelemetryMixin._victron_ess_balance_variance_ratio(
            delay_mean,
            delay_mad,
            1.0,
        )
        gain_ratio = _UpdateCycleVictronEssBalanceLearningTelemetryMixin._victron_ess_balance_variance_ratio(
            gain_mean,
            gain_mad,
            0.1,
        )
        return max(0.0, min(1.0, (delay_ratio + gain_ratio) / 2.0))

    @classmethod
    def _victron_ess_balance_regime_consistency_score(cls, profile: dict[str, Any]) -> float:
        sample_count = cls._victron_ess_balance_profile_sample_count(profile)
        sample_score = min(1.0, float(sample_count) / 4.0)
        stability_score = cls._optional_float(profile.get("stability_score")) or 0.0
        variance_score = cls._optional_float(profile.get("response_variance_score")) or 0.0
        return max(0.0, min(1.0, (0.3 * sample_score) + (0.35 * stability_score) + (0.35 * variance_score)))

    @classmethod
    def _victron_ess_balance_reproducibility_score(cls, profile: dict[str, Any]) -> float:
        settled_count = max(0, int(profile.get("settled_count", 0) or 0))
        overshoot_count = max(0, int(profile.get("overshoot_count", 0) or 0))
        total = settled_count + overshoot_count
        settle_ratio = 1.0 if total <= 0 else float(settled_count) / float(total)
        variance_score = cls._optional_float(profile.get("response_variance_score")) or 0.0
        return max(0.0, min(1.0, (0.6 * settle_ratio) + (0.4 * variance_score)))

    def _record_victron_ess_balance_command(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
    ) -> None:
        _record_victron_ess_balance_tracking_command(svc, now, setpoint_w, source_error_w, profile_key)

    @staticmethod
    def _clear_victron_ess_balance_tracking_episode(svc: Any) -> None:
        _clear_victron_ess_balance_tracking_episode_state(svc)

    @staticmethod
    def _reset_victron_ess_balance_pid(svc: Any) -> None:
        _reset_victron_ess_balance_pid_state(svc)

    @staticmethod
    def _reset_victron_ess_balance_pid_integral(svc: Any, aggressive: bool = False) -> None:
        _reset_victron_ess_balance_pid_integral_state(svc, aggressive)


def _victron_ess_balance_settle_ratio(settled_count: int, total_outcomes: int) -> float:
    if total_outcomes <= 0:
        return 1.0
    return float(settled_count) / float(total_outcomes)


def _victron_ess_balance_overshoot_penalty(overshoot_count: int, total_outcomes: int) -> float:
    if total_outcomes <= 0:
        return 0.0
    return min(0.6, float(overshoot_count) / float(total_outcomes))


def _victron_ess_balance_gain_bonus(estimated_gain: float | None) -> float:
    if estimated_gain is not None and estimated_gain > 0.0:
        return 0.15
    return 0.0


def _victron_ess_balance_delay_penalty(response_delay_seconds: float | None) -> float:
    if response_delay_seconds is None:
        return 0.0
    return min(0.2, max(0.0, response_delay_seconds - 5.0) / 50.0)


def _victron_ess_balance_clamped_stability_score(
    settle_ratio: float,
    gain_bonus: float,
    overshoot_penalty: float,
    delay_penalty: float,
) -> float:
    return max(0.0, min(1.0, 0.45 + (0.4 * settle_ratio) + gain_bonus - overshoot_penalty - delay_penalty))
