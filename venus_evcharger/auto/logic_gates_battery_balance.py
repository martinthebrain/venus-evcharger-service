# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from venus_evcharger.energy import derive_energy_forecast, summarize_energy_learning_profiles
from .logic_gates_battery_learning import _AutoDecisionBatteryLearning
from .logic_gates_battery_balance_support import (
    _battery_discharge_balance_allowed_feasibilities,
    _battery_discharge_balance_coordination_blocked_by_availability,
    _battery_discharge_balance_coordination_counts,
    _battery_discharge_balance_coordination_experimental,
    _battery_discharge_balance_coordination_not_needed,
    _battery_discharge_balance_coordination_partial,
    _battery_discharge_balance_coordination_supported,
    _battery_discharge_balance_penalty_inputs_valid,
    _battery_discharge_balance_penalty_scale,
    _battery_discharge_balance_penalty_value,
    _battery_discharge_balance_zero_start_penalty,
    _discharge_balance_bias_mode_active,
)


class _AutoDecisionBatteryBalance(_AutoDecisionBatteryLearning):
    if TYPE_CHECKING:  # pragma: no cover

        def _source_activity_penalties(
            self,
            sources: list[dict[str, Any]],
            profiles: dict[str, Any],
        ) -> tuple[float, float, float | None, float | None]: ...

        def _cluster_activity_penalties(
            self,
            cluster: dict[str, Any],
            learning_summary: dict[str, float | int | None],
        ) -> tuple[float, float, float | None, float | None]: ...

        def _battery_penalty_multiplier(
            self,
            *,
            direction: str,
            response_delay_seconds: float | None,
            support_bias: float | None,
            import_support_bias: float | None,
            export_bias: float | None,
        ) -> float: ...

    def _combined_battery_activity_context(self) -> dict[str, float | int | str | None]:
        """Return a conservative battery activity picture used to de-bias surplus decisions."""
        cluster, sources, profiles = self._battery_activity_inputs()
        learning_summary = self._combined_battery_learning_summary(profiles)
        charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio = self._combined_battery_penalties(
            cluster,
            sources,
            profiles,
            learning_summary,
        )
        behavior = self._battery_learning_behavior(learning_summary)
        forecast = derive_energy_forecast(cluster, learning_summary)
        scaled_charge_penalty, scaled_discharge_penalty = self._combined_battery_scaled_penalties(
            charge_penalty,
            discharge_penalty,
            behavior,
        )
        bias_context = self._combined_battery_discharge_balance_context(cluster, forecast, behavior)
        coordination_context = self._combined_battery_coordination_context(cluster, bias_context["warning_active"])
        coordination_policy_context = self._combined_battery_coordination_policy_context(
            cluster,
            coordination_context["feasibility"],
        )
        self._emit_combined_battery_balance_warnings(cluster, bias_context, coordination_context)
        effective_penalty_w = self._combined_battery_effective_penalty_w(
            bias_context["bias_penalty_w"],
            coordination_policy_context["penalty_w"],
        )
        return self._combined_battery_activity_payload(
            cluster=cluster,
            learning_summary=learning_summary,
            behavior=behavior,
            forecast=forecast,
            charge_penalty=scaled_charge_penalty,
            discharge_penalty=scaled_discharge_penalty,
            max_charge_ratio=max_charge_ratio,
            max_discharge_ratio=max_discharge_ratio,
            effective_penalty_w=effective_penalty_w,
            bias_context=bias_context,
            coordination_context=coordination_context,
            coordination_policy_context=coordination_policy_context,
        )

    @staticmethod
    def _combined_battery_learning_summary(profiles: dict[str, Any]) -> dict[str, float | int | None]:
        return summarize_energy_learning_profiles(profiles)

    def _combined_battery_penalties(
        self,
        cluster: dict[str, Any],
        sources: list[dict[str, Any]],
        profiles: dict[str, Any],
        learning_summary: dict[str, float | int | None],
    ) -> tuple[float, float, float | None, float | None]:
        if sources:
            return self._source_activity_penalties(sources, profiles)
        return self._cluster_activity_penalties(cluster, learning_summary)

    def _combined_battery_scaled_penalties(
        self,
        charge_penalty: float,
        discharge_penalty: float,
        behavior: Mapping[str, float | None],
    ) -> tuple[float, float]:
        return (
            charge_penalty * self._combined_battery_penalty_multiplier("charge", behavior),
            discharge_penalty * self._combined_battery_penalty_multiplier("discharge", behavior),
        )

    def _combined_battery_penalty_multiplier(
        self,
        direction: str,
        behavior: Mapping[str, float | None],
    ) -> float:
        return self._battery_penalty_multiplier(
            direction=direction,
            response_delay_seconds=behavior["response_delay_seconds"],
            support_bias=behavior["support_bias"],
            import_support_bias=behavior["import_support_bias"],
            export_bias=behavior["export_bias"],
        )

    def _combined_battery_discharge_balance_context(
        self,
        cluster: Mapping[str, Any],
        forecast: Mapping[str, Any],
        behavior: Mapping[str, float | None],
    ) -> dict[str, bool | float | str]:
        warning_active, error_w, warn_threshold_w, bias_mode, gate_active, start_error_w, penalty_w = (
            self._battery_discharge_balance_policy_context(
                cluster,
                expected_export_w=self._cluster_or_forecast_metric(
                    cluster,
                    forecast,
                    "expected_near_term_export_w",
                ),
                reserve_floor_soc=behavior["reserve_band_floor_soc"],
            )
        )
        return {
            "warning_active": warning_active,
            "error_w": error_w,
            "warn_threshold_w": warn_threshold_w,
            "bias_mode": bias_mode,
            "bias_gate_active": gate_active,
            "bias_start_error_w": start_error_w,
            "bias_penalty_w": penalty_w,
        }

    def _combined_battery_coordination_context(
        self,
        cluster: Mapping[str, Any],
        warning_active: bool | float | str,
    ) -> dict[str, bool | str]:
        feasibility, advisory_active, advisory_reason = self._battery_discharge_balance_coordination_advisory(
            cluster,
            warning_active=bool(warning_active),
        )
        return {
            "feasibility": feasibility,
            "advisory_active": advisory_active,
            "advisory_reason": advisory_reason,
        }

    def _combined_battery_coordination_policy_context(
        self,
        cluster: Mapping[str, Any],
        feasibility: bool | float | str,
    ) -> dict[str, bool | float | str]:
        enabled, support_mode, gate_active, start_error_w, penalty_w = (
            self._battery_discharge_balance_coordination_policy_context(
                cluster,
                feasibility=str(feasibility),
            )
        )
        return {
            "enabled": enabled,
            "support_mode": support_mode,
            "gate_active": gate_active,
            "start_error_w": start_error_w,
            "penalty_w": penalty_w,
        }

    def _emit_combined_battery_balance_warnings(
        self,
        cluster: Mapping[str, Any],
        bias_context: Mapping[str, bool | float | str],
        coordination_context: Mapping[str, bool | str],
    ) -> None:
        self._emit_combined_battery_discharge_warning(cluster, bias_context)
        self._emit_combined_battery_coordination_warning(coordination_context)

    def _emit_combined_battery_discharge_warning(
        self,
        cluster: Mapping[str, Any],
        bias_context: Mapping[str, bool | float | str],
    ) -> None:
        if not bool(bias_context["warning_active"]):
            return
        self._combined_battery_warning_throttled(
            "battery-discharge-balance-warning",
            "Auto mode observed battery discharge imbalance: error=%s W active=%s eligible=%s",
            round(float(bias_context["error_w"]), 1),
            int(cluster.get("battery_discharge_balance_active_source_count", 0) or 0),
            int(cluster.get("battery_discharge_balance_eligible_source_count", 0) or 0),
        )

    def _emit_combined_battery_coordination_warning(
        self,
        coordination_context: Mapping[str, bool | str],
    ) -> None:
        if not bool(coordination_context["advisory_active"]):
            return
        self._combined_battery_warning_throttled(
            "battery-discharge-balance-coordination-advisory",
            "Auto mode observed ESS imbalance but coordination feasibility is limited: %s",
            coordination_context["advisory_reason"],
        )

    def _combined_battery_warning_throttled(self, key: str, message: str, *args: object) -> None:
        svc = self.service
        warning_throttled = getattr(svc, "_warning_throttled", None)
        if not callable(warning_throttled):
            return
        warning_throttled(
            key,
            max(30.0, float(getattr(svc, "auto_battery_scan_interval_seconds", 60.0) or 60.0)),
            message,
            *args,
        )

    @staticmethod
    def _combined_battery_effective_penalty_w(
        bias_penalty_w: bool | float | str,
        coordination_penalty_w: bool | float | str,
    ) -> float:
        return max(float(bias_penalty_w), float(coordination_penalty_w))

    def _combined_battery_activity_payload(
        self,
        *,
        cluster: Mapping[str, Any],
        learning_summary: Mapping[str, Any],
        behavior: Mapping[str, float | None],
        forecast: Mapping[str, Any],
        charge_penalty: float,
        discharge_penalty: float,
        max_charge_ratio: float | None,
        max_discharge_ratio: float | None,
        effective_penalty_w: float,
        bias_context: Mapping[str, bool | float | str],
        coordination_context: Mapping[str, bool | str],
        coordination_policy_context: Mapping[str, bool | float | str],
    ) -> dict[str, float | int | str | None]:
        payload: dict[str, float | int | str | None] = {
            "surplus_penalty_w": float(charge_penalty + discharge_penalty + effective_penalty_w),
        }
        payload.update(self._combined_battery_power_payload(charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio))
        payload.update(self._combined_battery_learning_payload(learning_summary))
        payload.update(self._combined_battery_behavior_payload(behavior))
        payload.update(self._combined_battery_forecast_payload(cluster, forecast))
        payload.update(self._combined_battery_bias_payload(bias_context))
        payload.update(self._combined_battery_coordination_payload(coordination_context, coordination_policy_context))
        return payload

    def _combined_battery_power_payload(
        self,
        charge_penalty: float,
        discharge_penalty: float,
        max_charge_ratio: float | None,
        max_discharge_ratio: float | None,
    ) -> dict[str, float | int | str | None]:
        return {
            "charge_power_w": charge_penalty if charge_penalty > 0.0 else None,
            "discharge_power_w": discharge_penalty if discharge_penalty > 0.0 else None,
            "charge_activity_ratio": max_charge_ratio,
            "discharge_activity_ratio": max_discharge_ratio,
            "mode": self._battery_activity_mode(charge_penalty, discharge_penalty),
        }

    def _combined_battery_learning_payload(
        self,
        learning_summary: Mapping[str, Any],
    ) -> dict[str, float | int | str | None]:
        return {
            "learning_profile_count": int(learning_summary.get("profile_count", 0) or 0),
            "observed_max_charge_power_w": self._non_negative_optional_float(
                learning_summary.get("observed_max_charge_power_w")
            ),
            "observed_max_discharge_power_w": self._non_negative_optional_float(
                learning_summary.get("observed_max_discharge_power_w")
            ),
        }

    @staticmethod
    def _combined_battery_behavior_payload(
        behavior: Mapping[str, float | None],
    ) -> dict[str, float | int | str | None]:
        return {
            "typical_response_delay_seconds": behavior["response_delay_seconds"],
            "support_bias": behavior["support_bias"],
            "day_support_bias": behavior["day_support_bias"],
            "night_support_bias": behavior["night_support_bias"],
            "import_support_bias": behavior["import_support_bias"],
            "export_bias": behavior["export_bias"],
            "battery_first_export_bias": behavior["battery_first_export_bias"],
            "power_smoothing_ratio": behavior["power_smoothing_ratio"],
            "reserve_band_floor_soc": behavior["reserve_band_floor_soc"],
            "reserve_band_ceiling_soc": behavior["reserve_band_ceiling_soc"],
            "reserve_band_width_soc": behavior["reserve_band_width_soc"],
        }

    def _combined_battery_forecast_payload(
        self,
        cluster: Mapping[str, Any],
        forecast: Mapping[str, Any],
    ) -> dict[str, float | int | str | None]:
        return {
            "battery_headroom_charge_w": self._cluster_or_forecast_metric(cluster, forecast, "battery_headroom_charge_w"),
            "battery_headroom_discharge_w": self._cluster_or_forecast_metric(cluster, forecast, "battery_headroom_discharge_w"),
            "expected_near_term_export_w": self._cluster_or_forecast_metric(cluster, forecast, "expected_near_term_export_w"),
            "expected_near_term_import_w": self._cluster_or_forecast_metric(cluster, forecast, "expected_near_term_import_w"),
        }

    def _combined_battery_bias_payload(
        self,
        bias_context: Mapping[str, bool | float | str],
    ) -> dict[str, float | int | str | None]:
        return {
            "discharge_balance_policy_enabled": 1 if self._battery_discharge_balance_policy_enabled() else 0,
            "discharge_balance_warning_active": 1 if bool(bias_context["warning_active"]) else 0,
            "discharge_balance_warning_error_w": self._combined_battery_warning_error_w(bias_context),
            "discharge_balance_warn_threshold_w": float(bias_context["warn_threshold_w"]),
            "discharge_balance_bias_mode": str(bias_context["bias_mode"]),
            "discharge_balance_bias_gate_active": 1 if bool(bias_context["bias_gate_active"]) else 0,
            "discharge_balance_bias_start_error_w": float(bias_context["bias_start_error_w"]),
            "discharge_balance_bias_penalty_w": float(bias_context["bias_penalty_w"]),
        }

    @staticmethod
    def _combined_battery_warning_error_w(
        bias_context: Mapping[str, bool | float | str],
    ) -> float | None:
        if not bool(bias_context["warning_active"]):
            return None
        return float(bias_context["error_w"])

    @staticmethod
    def _combined_battery_coordination_payload(
        coordination_context: Mapping[str, bool | str],
        coordination_policy_context: Mapping[str, bool | float | str],
    ) -> dict[str, float | int | str | None]:
        return {
            "discharge_balance_coordination_policy_enabled": 1 if bool(coordination_policy_context["enabled"]) else 0,
            "discharge_balance_coordination_support_mode": str(coordination_policy_context["support_mode"]),
            "discharge_balance_coordination_feasibility": str(coordination_context["feasibility"]),
            "discharge_balance_coordination_gate_active": 1 if bool(coordination_policy_context["gate_active"]) else 0,
            "discharge_balance_coordination_start_error_w": float(coordination_policy_context["start_error_w"]),
            "discharge_balance_coordination_penalty_w": float(coordination_policy_context["penalty_w"]),
            "discharge_balance_coordination_advisory_active": 1 if bool(coordination_context["advisory_active"]) else 0,
            "discharge_balance_coordination_advisory_reason": str(coordination_context["advisory_reason"]),
        }
