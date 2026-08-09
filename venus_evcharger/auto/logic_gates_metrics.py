# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Mapping

from .component_context import AutoDecisionContext
from .logic_gates_battery_balance import AutoBatteryBalance
from .logic_gates_battery_learning import AutoBatteryLearning
from .logic_learning import AutoLearningPolicy
from .logic_samples import AutoSampleTracker


class AutoDecisionMetrics:
    """Calculate the smoothed, battery-adjusted metrics used by decisions."""

    def __init__(
        self,
        context: AutoDecisionContext,
        learning: AutoLearningPolicy,
        samples: AutoSampleTracker,
        battery_learning: AutoBatteryLearning,
        battery_balance: AutoBatteryBalance,
    ) -> None:
        self.port = context.port
        self.service = context.service
        self.learning = learning
        self.samples = samples
        self.battery_learning = battery_learning
        self.battery_balance = battery_balance
        self._ev_priority_active = False

    def update_average_metrics(
        self,
        now: float,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
    ) -> tuple[float | None, float | None]:
        """Update rolling Auto metrics and return averaged surplus/grid values."""
        svc = self.service
        surplus_power = self.samples.get_available_surplus_watts(pv_power, grid_power)
        self.samples.add_auto_sample(now, surplus_power, float(grid_power))
        avg_surplus_power = self.samples.average_auto_metric(1)
        avg_grid_power = self.samples.average_auto_metric(2)
        if avg_surplus_power is None or avg_grid_power is None:
            return None, None

        threshold_metrics = self._surplus_threshold_metrics(battery_soc)
        smoothing_metrics = self._smoothing_metrics(relay_on, float(avg_surplus_power), float(avg_grid_power))
        smoothed_surplus = self.battery_learning._required_float(smoothing_metrics["surplus"])
        smoothed_grid = self.battery_learning._required_float(smoothing_metrics["grid"])
        battery_metrics = self._battery_adjusted_surplus_metrics(
            smoothed_surplus,
            pv_power=float(pv_power),
            grid_power=smoothed_grid,
            battery_soc=float(battery_soc),
            relay_on=relay_on,
        )
        learned_metrics = self._learned_threshold_metrics(now)
        svc._last_auto_metrics = self._auto_metrics_snapshot(
            avg_surplus_power=float(avg_surplus_power),
            avg_grid_power=float(avg_grid_power),
            battery_soc=float(battery_soc),
            threshold_metrics=threshold_metrics,
            smoothing_metrics=smoothing_metrics,
            battery_metrics=battery_metrics,
            learned_metrics=learned_metrics,
        )
        return self.battery_learning._required_float(battery_metrics["decision_surplus"]), smoothed_grid

    def _surplus_threshold_metrics(self, battery_soc: float) -> dict[str, float | str | None]:
        start_surplus_watts, stop_surplus_watts, threshold_profile = self.learning._surplus_thresholds_for_soc(
            battery_soc
        )
        return {
            "start_threshold": float(start_surplus_watts),
            "stop_threshold": float(stop_surplus_watts),
            "profile": threshold_profile,
        }

    def _smoothing_metrics(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
    ) -> dict[str, float | str | None]:
        adaptive_alpha, adaptive_alpha_stage, surplus_volatility = self.samples._adaptive_stop_alpha()
        decision_surplus_power, decision_grid_power = self._smoothed_decision_metrics(
            relay_on,
            avg_surplus_power,
            avg_grid_power,
            adaptive_alpha,
        )
        return {
            "surplus": float(decision_surplus_power),
            "grid": float(decision_grid_power),
            "stop_alpha": float(adaptive_alpha),
            "stop_alpha_stage": adaptive_alpha_stage,
            "surplus_volatility": float(surplus_volatility) if surplus_volatility is not None else None,
        }

    def _battery_adjusted_surplus_metrics(
        self,
        decision_surplus_power: float,
        *,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
    ) -> dict[str, float | int | str | None]:
        battery_activity = self.battery_balance._combined_battery_activity_context()
        total_surplus_penalty_w = self._activity_power(battery_activity, "surplus_penalty_w")
        charge_penalty_w = self._activity_power(battery_activity, "charge_penalty_w")
        near_term_adjustment_w = self.battery_learning._near_term_grid_adjustment(battery_activity)
        normalized_learning_profile_count = _learning_profile_count(battery_activity)
        priority_metrics = self._ev_priority_metrics(
            decision_surplus_power,
            pv_power=pv_power,
            grid_power=grid_power,
            battery_soc=battery_soc,
            relay_on=relay_on,
            battery_activity=battery_activity,
        )
        priority_active = bool(priority_metrics["active"])
        applied_surplus_penalty_w = _applied_surplus_penalty(
            total_surplus_penalty_w,
            charge_penalty_w,
            priority_active,
        )
        available_surplus_w = self.battery_learning._required_float(priority_metrics["available_surplus_w"])
        return {
            **battery_activity,
            "decision_surplus": float(
                available_surplus_w - applied_surplus_penalty_w + near_term_adjustment_w
            ),
            "raw_decision_surplus": float(decision_surplus_power),
            "surplus_penalty_w": applied_surplus_penalty_w,
            "unadjusted_surplus_penalty_w": total_surplus_penalty_w,
            "near_term_adjustment_w": near_term_adjustment_w,
            "learning_profile_count": normalized_learning_profile_count,
            "ev_priority_active": int(priority_active),
            "ev_priority_credit_w": priority_metrics["credit_w"],
            "ev_priority_available_surplus_w": available_surplus_w,
            "ev_priority_reclaimable_charge_w": priority_metrics["reclaimable_charge_w"],
            "ev_priority_running_load_w": priority_metrics["running_load_w"],
            "ev_priority_soc": priority_metrics["activation_soc"],
            "ev_priority_release_soc": priority_metrics["release_soc"],
        }

    def _ev_priority_metrics(
        self,
        decision_surplus_power: float,
        *,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
        battery_activity: Mapping[str, float | int | str | None],
    ) -> dict[str, bool | float]:
        policy = self.learning._auto_policy()
        active = self._update_ev_priority_state(
            battery_soc,
            activation_soc=policy.ev_priority_soc,
            release_soc=policy.ev_priority_release_soc,
        )
        base_surplus_w = max(0.0, float(decision_surplus_power))
        observed_charge_w = self._activity_power(battery_activity, "charge_power_w")
        reclaimable_charge_w = _reclaimable_charge_power(observed_charge_w, grid_power)
        running_load_w = self._priority_running_load(active, relay_on)
        available_surplus_w = _priority_available_surplus(
            active=active,
            base_surplus_w=base_surplus_w,
            pv_power_w=pv_power,
            reclaimable_charge_w=reclaimable_charge_w,
            running_load_w=running_load_w,
        )
        return {
            "active": active,
            "credit_w": max(0.0, available_surplus_w - base_surplus_w),
            "available_surplus_w": available_surplus_w,
            "reclaimable_charge_w": reclaimable_charge_w if active else 0.0,
            "running_load_w": running_load_w,
            "activation_soc": float(policy.ev_priority_soc),
            "release_soc": float(policy.ev_priority_release_soc),
        }

    def _activity_power(
        self,
        battery_activity: Mapping[str, float | int | str | None],
        key: str,
    ) -> float:
        value = self.battery_learning._non_negative_optional_float(battery_activity.get(key))
        return 0.0 if value is None else value

    def _priority_running_load(self, active: bool, relay_on: bool) -> float:
        if not active or not relay_on:
            return 0.0
        return self.port.charging_power_w()

    def _update_ev_priority_state(
        self,
        battery_soc: float,
        *,
        activation_soc: float,
        release_soc: float,
    ) -> bool:
        if self._ev_priority_active:
            if battery_soc < release_soc:
                self._ev_priority_active = False
        elif battery_soc >= activation_soc:
            self._ev_priority_active = True
        return self._ev_priority_active

    def _learned_threshold_metrics(self, now: float) -> dict[str, float | str | None]:
        learned_charge_power = self.learning._active_learned_charge_power(now)
        return {
            "learned_charge_power": learned_charge_power,
            "learned_charge_power_state": self.learning._current_learned_charge_power_state(now),
            "threshold_scale": float(self.learning._learned_charge_power_scale(now)),
            "threshold_mode": "adaptive" if learned_charge_power is not None else "static",
        }

    def _auto_metrics_snapshot(
        self,
        *,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        threshold_metrics: Mapping[str, float | str | None],
        smoothing_metrics: Mapping[str, float | str | None],
        battery_metrics: Mapping[str, float | int | str | None],
        learned_metrics: Mapping[str, float | str | None],
    ) -> dict[str, float | int | str | None]:
        decision_surplus = self.battery_learning._required_float(battery_metrics["decision_surplus"])
        raw_decision_surplus = self.battery_learning._required_float(battery_metrics["raw_decision_surplus"])
        grid = self.battery_learning._required_float(smoothing_metrics["grid"])
        start_threshold = self.battery_learning._required_float(threshold_metrics["start_threshold"])
        stop_threshold = self.battery_learning._required_float(threshold_metrics["stop_threshold"])
        threshold_scale = self.battery_learning._required_float(learned_metrics["threshold_scale"])
        stop_alpha = self.battery_learning._required_float(smoothing_metrics["stop_alpha"])
        return {
            "surplus": decision_surplus,
            "grid": grid,
            "raw_surplus": avg_surplus_power,
            "decision_surplus_before_battery_penalty": raw_decision_surplus,
            "raw_grid": avg_grid_power,
            "soc": battery_soc,
            "profile": threshold_metrics["profile"],
            "start_threshold": start_threshold,
            "stop_threshold": stop_threshold,
            "learned_charge_power": learned_metrics["learned_charge_power"],
            "learned_charge_power_state": learned_metrics["learned_charge_power_state"],
            "threshold_scale": threshold_scale,
            "threshold_mode": learned_metrics["threshold_mode"],
            "stop_alpha": stop_alpha,
            "stop_alpha_stage": smoothing_metrics["stop_alpha_stage"],
            "surplus_volatility": smoothing_metrics["surplus_volatility"],
            "battery_surplus_penalty_w": battery_metrics["surplus_penalty_w"],
            "battery_unadjusted_surplus_penalty_w": battery_metrics["unadjusted_surplus_penalty_w"],
            "battery_near_term_adjustment_w": battery_metrics["near_term_adjustment_w"],
            "ev_priority_active": battery_metrics["ev_priority_active"],
            "ev_priority_credit_w": battery_metrics["ev_priority_credit_w"],
            "ev_priority_available_surplus_w": battery_metrics["ev_priority_available_surplus_w"],
            "ev_priority_reclaimable_charge_w": battery_metrics["ev_priority_reclaimable_charge_w"],
            "ev_priority_running_load_w": battery_metrics["ev_priority_running_load_w"],
            "ev_priority_soc": battery_metrics["ev_priority_soc"],
            "ev_priority_release_soc": battery_metrics["ev_priority_release_soc"],
            "battery_support_mode": battery_metrics["mode"],
            "battery_discharge_balance_policy_enabled": battery_metrics["discharge_balance_policy_enabled"],
            "battery_discharge_balance_warning_active": battery_metrics["discharge_balance_warning_active"],
            "battery_discharge_balance_warning_error_w": battery_metrics["discharge_balance_warning_error_w"],
            "battery_discharge_balance_warn_threshold_w": battery_metrics["discharge_balance_warn_threshold_w"],
            "battery_discharge_balance_bias_mode": battery_metrics["discharge_balance_bias_mode"],
            "battery_discharge_balance_bias_gate_active": battery_metrics["discharge_balance_bias_gate_active"],
            "battery_discharge_balance_bias_start_error_w": battery_metrics["discharge_balance_bias_start_error_w"],
            "battery_discharge_balance_bias_penalty_w": battery_metrics["discharge_balance_bias_penalty_w"],
            "battery_discharge_balance_coordination_policy_enabled": battery_metrics[
                "discharge_balance_coordination_policy_enabled"
            ],
            "battery_discharge_balance_coordination_support_mode": battery_metrics[
                "discharge_balance_coordination_support_mode"
            ],
            "battery_discharge_balance_coordination_feasibility": battery_metrics[
                "discharge_balance_coordination_feasibility"
            ],
            "battery_discharge_balance_coordination_gate_active": battery_metrics[
                "discharge_balance_coordination_gate_active"
            ],
            "battery_discharge_balance_coordination_start_error_w": battery_metrics[
                "discharge_balance_coordination_start_error_w"
            ],
            "battery_discharge_balance_coordination_penalty_w": battery_metrics[
                "discharge_balance_coordination_penalty_w"
            ],
            "battery_discharge_balance_coordination_advisory_active": battery_metrics[
                "discharge_balance_coordination_advisory_active"
            ],
            "battery_discharge_balance_coordination_advisory_reason": battery_metrics[
                "discharge_balance_coordination_advisory_reason"
            ],
            "battery_charge_power_w": battery_metrics["charge_power_w"],
            "battery_discharge_power_w": battery_metrics["discharge_power_w"],
            "battery_charge_activity_ratio": battery_metrics["charge_activity_ratio"],
            "battery_discharge_activity_ratio": battery_metrics["discharge_activity_ratio"],
            "battery_learning_profile_count": battery_metrics["learning_profile_count"],
            "battery_observed_max_charge_power_w": battery_metrics["observed_max_charge_power_w"],
            "battery_observed_max_discharge_power_w": battery_metrics["observed_max_discharge_power_w"],
            "battery_typical_response_delay_seconds": battery_metrics["typical_response_delay_seconds"],
            "battery_support_bias": battery_metrics["support_bias"],
            "battery_day_support_bias": battery_metrics["day_support_bias"],
            "battery_night_support_bias": battery_metrics["night_support_bias"],
            "battery_import_support_bias": battery_metrics["import_support_bias"],
            "battery_export_bias": battery_metrics["export_bias"],
            "battery_battery_first_export_bias": battery_metrics["battery_first_export_bias"],
            "battery_power_smoothing_ratio": battery_metrics["power_smoothing_ratio"],
            "battery_reserve_band_floor_soc": battery_metrics["reserve_band_floor_soc"],
            "battery_reserve_band_ceiling_soc": battery_metrics["reserve_band_ceiling_soc"],
            "battery_reserve_band_width_soc": battery_metrics["reserve_band_width_soc"],
            "battery_headroom_charge_w": battery_metrics["battery_headroom_charge_w"],
            "battery_headroom_discharge_w": battery_metrics["battery_headroom_discharge_w"],
            "expected_near_term_export_w": battery_metrics["expected_near_term_export_w"],
            "expected_near_term_import_w": battery_metrics["expected_near_term_import_w"],
        }

    def _smoothed_decision_metrics(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        adaptive_alpha: float,
    ) -> tuple[float, float]:
        """Return the decision metrics after optional stop-side EWMA smoothing."""
        svc = self.service
        if not relay_on:
            svc._stop_smoothed_surplus_power = None
            svc._stop_smoothed_grid_power = None
            return avg_surplus_power, avg_grid_power
        decision_surplus_power = self.samples._smooth_metric(
            getattr(svc, "_stop_smoothed_surplus_power", None),
            avg_surplus_power,
            adaptive_alpha,
        )
        decision_grid_power = self.samples._smooth_metric(
            getattr(svc, "_stop_smoothed_grid_power", None),
            avg_grid_power,
            adaptive_alpha,
        )
        svc._stop_smoothed_surplus_power = decision_surplus_power
        svc._stop_smoothed_grid_power = decision_grid_power
        return float(decision_surplus_power), float(decision_grid_power)


def _learning_profile_count(
    battery_activity: Mapping[str, float | int | str | None],
) -> int:
    value = battery_activity.get("learning_profile_count")
    return value if isinstance(value, int) else 0


def _applied_surplus_penalty(
    total_penalty_w: float,
    charge_penalty_w: float,
    priority_active: bool,
) -> float:
    if not priority_active:
        return total_penalty_w
    return max(0.0, total_penalty_w - charge_penalty_w)


def _reclaimable_charge_power(observed_charge_w: float, grid_power_w: float) -> float:
    grid_import_w = max(0.0, float(grid_power_w))
    return max(0.0, observed_charge_w - grid_import_w)


def _priority_available_surplus(
    *,
    active: bool,
    base_surplus_w: float,
    pv_power_w: float,
    reclaimable_charge_w: float,
    running_load_w: float,
) -> float:
    if not active:
        return base_surplus_w
    candidate_w = min(
        max(0.0, float(pv_power_w)),
        base_surplus_w + reclaimable_charge_w + running_load_w,
    )
    return max(base_surplus_w, candidate_w)
