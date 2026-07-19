# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias telemetry-learning helpers."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_learning_profiles_support import (
    _clear_victron_ess_balance_tracking_episode_state,
    _record_victron_ess_balance_tracking_command,
    _victron_ess_balance_update_service_sample,
)
from .victron_ess_balance_apply_pid import VictronEssPidController
from .victron_ess_balance_apply_sources import VictronEssSourceResolver
from .victron_ess_balance_learning_profiles import VictronEssLearningProfiles
from .victron_ess_balance_recommendation import VictronEssRecommendationEngine
from .victron_ess_balance_safety import VictronEssSafetyController
from .victron_ess_balance_safety_support import VictronEssSafetyRecovery
from .victron_ess_balance_scoring import VictronEssTelemetryScorer


class VictronEssTelemetryRecorder:
    def __init__(
        self,
        sources: VictronEssSourceResolver,
        profiles: VictronEssLearningProfiles,
        safety: VictronEssSafetyController,
        recovery: VictronEssSafetyRecovery,
        recommendation: VictronEssRecommendationEngine,
        scorer: VictronEssTelemetryScorer,
        pid: VictronEssPidController,
    ) -> None:
        self._sources = sources
        self._profiles = profiles
        self._safety = safety
        self._recovery = recovery
        self._recommendation = recommendation
        self._scorer = scorer
        self._pid = pid

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
        opposite_signs = (command_error_w_f * float(source_error_w)) < 0.0
        return bool(opposite_signs and current_abs_error_w >= improvement_threshold_w)

    def _victron_ess_balance_telemetry_command_state(
        self,
        svc: Any,
        profile_key: str,
    ) -> dict[str, Any]:
        return {
            "command_at": self._sources._optional_float(svc._victron_ess_balance_telemetry_last_command_at),
            "command_error_w": self._sources._optional_float(svc._victron_ess_balance_telemetry_last_command_error_w),
            "command_setpoint_w": self._sources._optional_float(
                svc._victron_ess_balance_telemetry_last_command_setpoint_w
            ),
            "command_profile_key": str(svc._victron_ess_balance_telemetry_last_command_profile_key or profile_key).strip(),
            "command_response_recorded": bool(svc._victron_ess_balance_telemetry_command_response_recorded),
            "command_overshoot_recorded": bool(svc._victron_ess_balance_telemetry_command_overshoot_recorded),
            "command_settled_recorded": bool(svc._victron_ess_balance_telemetry_command_settled_recorded),
        }

    @staticmethod
    def _victron_ess_balance_telemetry_thresholds(svc: Any) -> tuple[float, float]:
        deadband_w = max(0.0, float(svc.auto_battery_discharge_balance_victron_bias_deadband_watts))
        base_setpoint_w = float(svc.auto_battery_discharge_balance_victron_bias_base_setpoint_watts)
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
            optional_float=self._sources._optional_float,
            ewma=self._scorer.ewma_learned_value,
        )
        self._profiles._victron_ess_balance_update_profile_delay(svc, command_profile_key, response_delay_seconds)

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
            optional_float=self._sources._optional_float,
            ewma=self._scorer.ewma_learned_value,
        )
        self._profiles._victron_ess_balance_update_profile_gain(svc, command_profile_key, gain_sample)

    def _victron_ess_balance_mark_overshoot(
        self,
        svc: Any,
        now: float,
        command_profile_key: str,
    ) -> None:
        svc._victron_ess_balance_telemetry_command_overshoot_recorded = True
        svc._victron_ess_balance_telemetry_overshoot_count = int(
            svc._victron_ess_balance_telemetry_overshoot_count
        ) + 1
        self._profiles._victron_ess_balance_increment_profile_counter(svc, command_profile_key, "overshoot_count")
        self._recovery._enter_victron_ess_balance_overshoot_cooldown(svc, now, "overshoot_detected")
        self._pid.reset_integral(svc, aggressive=True)

    def _victron_ess_balance_mark_settled(self, svc: Any, command_profile_key: str) -> None:
        svc._victron_ess_balance_telemetry_command_settled_recorded = True
        svc._victron_ess_balance_telemetry_settled_count = int(svc._victron_ess_balance_telemetry_settled_count) + 1
        self._profiles._victron_ess_balance_increment_profile_counter(svc, command_profile_key, "settled_count")

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
        telemetry_clean, telemetry_clean_reason = self._safety._victron_ess_balance_telemetry_is_clean(
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
        svc._victron_ess_balance_telemetry_last_grid_interaction_w = self._sources._optional_float(
            cluster.get("battery_combined_grid_interaction_w")
        )
        svc._victron_ess_balance_telemetry_last_ac_power_w = self._sources._optional_float(
            cluster.get("battery_combined_ac_power_w")
        )
        svc._victron_ess_balance_telemetry_last_ev_power_w = self._sources._victron_ess_balance_ev_power_w(svc)
        svc._victron_ess_balance_telemetry_stability_score = self._scorer.stability_score(svc)
        self._profiles._victron_ess_balance_refresh_profile_stability(
            svc,
            str(command_state["command_profile_key"] or profile_key),
        )
        self._recommendation._populate_victron_ess_balance_telemetry_metrics(svc, metrics)

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
