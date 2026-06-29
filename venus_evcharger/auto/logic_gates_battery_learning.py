# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from typing import Any, Mapping

from .logic_gates_battery_balance_support import _AutoDecisionBatteryBalanceSupport


class _AutoDecisionBatteryLearning(_AutoDecisionBatteryBalanceSupport):
    def _battery_activity_inputs(self) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        svc = self.service
        cluster = self._normalized_mapping(getattr(svc, "_last_energy_cluster", {}))
        raw_sources = cluster.get("battery_sources", [])
        sources = self._normalized_mapping_list(raw_sources)
        profiles = self._normalized_mapping(getattr(svc, "_last_energy_learning_profiles", {}))
        return cluster, sources, profiles

    @staticmethod
    def _normalized_mapping(raw_value: object) -> dict[str, Any]:
        return raw_value if isinstance(raw_value, dict) else {}

    def _normalized_mapping_list(self, raw_value: object) -> list[dict[str, Any]]:
        if not isinstance(raw_value, list):
            return []
        return [value for value in raw_value if isinstance(value, dict)]

    def _source_activity_penalties(
        self,
        sources: list[dict[str, Any]],
        profiles: dict[str, Any],
    ) -> tuple[float, float, float | None, float | None]:
        charge_penalty = 0.0
        discharge_penalty = 0.0
        max_charge_ratio: float | None = None
        max_discharge_ratio: float | None = None
        for source in sources:
            source_charge_penalty, source_discharge_penalty, charge_ratio, discharge_ratio = (
                self._source_activity_penalty(source, profiles)
            )
            charge_penalty += source_charge_penalty
            discharge_penalty += source_discharge_penalty
            max_charge_ratio = self._max_optional_ratio(max_charge_ratio, charge_ratio)
            max_discharge_ratio = self._max_optional_ratio(max_discharge_ratio, discharge_ratio)
        return charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio

    def _source_activity_penalty(
        self,
        source: dict[str, Any],
        profiles: dict[str, Any],
    ) -> tuple[float, float, float | None, float | None]:
        source_id = str(source.get("source_id", "")).strip()
        profile = profiles.get(source_id, {})
        charge_active, charge_ratio = self._active_battery_power(
            self._non_negative_optional_float(source.get("charge_power_w")),
            self._learning_observed_value(profile, "observed_max_charge_power_w"),
        )
        discharge_active, discharge_ratio = self._active_battery_power(
            self._non_negative_optional_float(source.get("discharge_power_w")),
            self._learning_observed_value(profile, "observed_max_discharge_power_w"),
        )
        return (
            0.0 if charge_active is None else charge_active,
            0.0 if discharge_active is None else discharge_active,
            charge_ratio,
            discharge_ratio,
        )

    def _cluster_activity_penalties(
        self,
        cluster: dict[str, Any],
        learning_summary: dict[str, float | int | None],
    ) -> tuple[float, float, float | None, float | None]:
        charge_active, charge_ratio = self._active_battery_power(
            self._non_negative_optional_float(cluster.get("battery_combined_charge_power_w")),
            self._non_negative_optional_float(learning_summary.get("observed_max_charge_power_w")),
        )
        discharge_active, discharge_ratio = self._active_battery_power(
            self._non_negative_optional_float(cluster.get("battery_combined_discharge_power_w")),
            self._non_negative_optional_float(learning_summary.get("observed_max_discharge_power_w")),
        )
        return (
            0.0 if charge_active is None else charge_active,
            0.0 if discharge_active is None else discharge_active,
            charge_ratio,
            discharge_ratio,
        )

    def _battery_learning_behavior(
        self,
        learning_summary: dict[str, float | int | None],
    ) -> dict[str, float | None]:
        day_support_bias = self._bounded_optional_float(learning_summary.get("day_support_bias"))
        night_support_bias = self._bounded_optional_float(learning_summary.get("night_support_bias"))
        return {
            "response_delay_seconds": self._non_negative_optional_float(
                learning_summary.get("typical_response_delay_seconds")
            ),
            "support_bias": self._support_bias_for_current_period(
                self._bounded_optional_float(learning_summary.get("support_bias")),
                day_support_bias,
                night_support_bias,
            ),
            "day_support_bias": day_support_bias,
            "night_support_bias": night_support_bias,
            "import_support_bias": self._bounded_optional_float(learning_summary.get("import_support_bias")),
            "export_bias": self._bounded_optional_float(learning_summary.get("export_bias")),
            "battery_first_export_bias": self._bounded_optional_float(learning_summary.get("battery_first_export_bias")),
            "power_smoothing_ratio": self._non_negative_optional_float(learning_summary.get("power_smoothing_ratio")),
            "reserve_band_floor_soc": self._non_negative_optional_float(learning_summary.get("reserve_band_floor_soc")),
            "reserve_band_ceiling_soc": self._non_negative_optional_float(
                learning_summary.get("reserve_band_ceiling_soc")
            ),
            "reserve_band_width_soc": self._non_negative_optional_float(learning_summary.get("reserve_band_width_soc")),
        }

    def _support_bias_for_current_period(
        self,
        default_bias: float | None,
        day_bias: float | None,
        night_bias: float | None,
    ) -> float | None:
        current_period = self._current_learning_period()
        if current_period == "day":
            return day_bias if day_bias is not None else default_bias
        if current_period == "night":
            return night_bias if night_bias is not None else default_bias
        return default_bias

    def _current_learning_period(self) -> str | None:
        service_now = getattr(self.service, "_time_now", None)
        raw_now = service_now() if callable(service_now) else None
        if not isinstance(raw_now, (int, float)):
            return None
        hour = time.localtime(float(raw_now)).tm_hour
        return "day" if 6 <= hour < 22 else "night"

    def _cluster_or_forecast_metric(
        self,
        cluster: Mapping[str, Any],
        forecast: Mapping[str, object],
        key: str,
    ) -> float | None:
        if key in cluster:
            return self._non_negative_optional_float(cluster.get(key))
        return self._non_negative_optional_float(forecast.get(key))

    @staticmethod
    def _battery_activity_mode(charge_penalty: float, discharge_penalty: float) -> str:
        if charge_penalty > 0.0 and discharge_penalty > 0.0:
            return "mixed"
        if discharge_penalty > 0.0:
            return "discharging"
        if charge_penalty > 0.0:
            return "charging"
        return "idle"

    @classmethod
    def _near_term_grid_adjustment(cls, battery_activity: dict[str, float | int | str | None]) -> float:
        expected_export_w = cls._non_negative_optional_float(battery_activity.get("expected_near_term_export_w")) or 0.0
        expected_import_w = cls._non_negative_optional_float(battery_activity.get("expected_near_term_import_w")) or 0.0
        export_credit_w = expected_export_w * 0.15
        import_penalty_w = expected_import_w * 0.15
        return float(export_credit_w - import_penalty_w)

    @classmethod
    def _learning_observed_value(cls, profile: object, key: str) -> float | None:
        if not isinstance(profile, dict):
            return None
        return cls._non_negative_optional_float(profile.get(key))

    @classmethod
    def _active_battery_power(
        cls,
        current_power_w: float | None,
        observed_max_power_w: float | None,
    ) -> tuple[float | None, float | None]:
        if current_power_w is None or current_power_w <= 0.0:
            return None, None
        ratio = cls._battery_activity_ratio(current_power_w, observed_max_power_w)
        if ratio is not None and ratio < 0.05:
            return None, ratio
        return float(current_power_w), ratio

    @staticmethod
    def _battery_activity_ratio(current_power_w: float, observed_max_power_w: float | None) -> float | None:
        if observed_max_power_w is None or observed_max_power_w <= 0.0:
            return None
        return float(current_power_w) / float(observed_max_power_w)

    @staticmethod
    def _non_negative_optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        numeric_value = float(value)
        if numeric_value < 0.0:
            return None
        return numeric_value

    @staticmethod
    def _required_float(value: object) -> float:
        assert isinstance(value, (int, float))
        return float(value)

    @staticmethod
    def _max_optional_ratio(current: float | None, candidate: float | None) -> float | None:
        if candidate is None:
            return current
        if current is None:
            return float(candidate)
        return max(float(current), float(candidate))

    @staticmethod
    def _bounded_optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        numeric_value = float(value)
        if numeric_value < -1.0:
            return -1.0
        if numeric_value > 1.0:
            return 1.0
        return numeric_value

    def _battery_penalty_multiplier(
        self,
        *,
        direction: str,
        response_delay_seconds: float | None,
        support_bias: float | None,
        import_support_bias: float | None,
        export_bias: float | None,
    ) -> float:
        multiplier = 1.0
        multiplier *= self._response_delay_penalty_factor(response_delay_seconds)
        if direction == "charge":
            multiplier *= self._charge_bias_penalty_factor(export_bias)
        elif direction == "discharge":
            multiplier *= self._discharge_bias_penalty_factor(import_support_bias, support_bias)
        return float(multiplier)

    @staticmethod
    def _response_delay_penalty_factor(response_delay_seconds: float | None) -> float:
        if response_delay_seconds is None or response_delay_seconds <= 0.0:
            return 1.0
        return 1.0 + min(float(response_delay_seconds), 60.0) / 120.0

    @staticmethod
    def _charge_bias_penalty_factor(export_bias: float | None) -> float:
        positive_export_bias = 0.0 if export_bias is None else max(0.0, float(export_bias))
        return 1.0 + (positive_export_bias * 0.25)

    @staticmethod
    def _discharge_bias_penalty_factor(
        import_support_bias: float | None,
        support_bias: float | None,
    ) -> float:
        discharge_bias = import_support_bias
        if discharge_bias is None and support_bias is not None:
            discharge_bias = max(0.0, float(support_bias))
        positive_discharge_bias = 0.0 if discharge_bias is None else max(0.0, float(discharge_bias))
        return 1.0 + (positive_discharge_bias * 0.25)
