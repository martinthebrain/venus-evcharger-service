# SPDX-License-Identifier: GPL-3.0-or-later
"""Derived energy forecast helpers for combined external sources."""

from __future__ import annotations

from typing import Any, Mapping

from .numeric import non_negative_optional_float, optional_float


def derive_energy_forecast(
    cluster_payload: Mapping[str, Any] | None,
    learning_summary: Mapping[str, Any] | None,
) -> dict[str, float | None]:
    """Return conservative near-term energy headroom and grid-flow estimates."""
    cluster = dict(cluster_payload or {})
    learning = dict(learning_summary or {})
    charge_power = _non_negative_optional_float(cluster.get("battery_combined_charge_power_w"))
    discharge_power = _non_negative_optional_float(cluster.get("battery_combined_discharge_power_w"))
    charge_limit_power = _non_negative_optional_float(cluster.get("battery_combined_charge_limit_power_w"))
    discharge_limit_power = _non_negative_optional_float(cluster.get("battery_combined_discharge_limit_power_w"))
    combined_soc = _non_negative_optional_float(cluster.get("battery_combined_soc"))
    grid_interaction_w = _optional_float(cluster.get("battery_combined_grid_interaction_w"))
    observed_max_charge = _headroom_limit(
        charge_limit_power,
        learning.get("observed_max_charge_power_w"),
        learning.get("average_active_charge_power_w"),
        charge_power,
    )
    observed_max_discharge = _headroom_limit(
        discharge_limit_power,
        learning.get("observed_max_discharge_power_w"),
        learning.get("average_active_discharge_power_w"),
        discharge_power,
    )
    charge_headroom = _scaled_headroom_w(
        observed_max_charge,
        charge_power,
        _charge_soc_scale(combined_soc, learning.get("reserve_band_ceiling_soc")),
    )
    discharge_headroom = _scaled_headroom_w(
        observed_max_discharge,
        discharge_power,
        _discharge_soc_scale(combined_soc, learning.get("reserve_band_floor_soc")),
    )
    response_weight = _response_weight(learning.get("typical_response_delay_seconds"))
    export_bias = _positive_bias(learning.get("export_bias"))
    battery_first_export_bias = _positive_bias(learning.get("battery_first_export_bias"))
    import_support_bias = _positive_bias(
        learning.get("import_support_bias"),
        fallback=learning.get("support_bias"),
    )
    smoothing_ratio = _positive_bias(learning.get("power_smoothing_ratio"))
    return {
        "battery_headroom_charge_w": charge_headroom,
        "battery_headroom_discharge_w": discharge_headroom,
        "expected_near_term_export_w": _expected_near_term_export_w(
            grid_interaction_w,
            charge_power,
            charge_headroom,
            observed_max_charge,
            export_bias,
            battery_first_export_bias,
            response_weight,
            smoothing_ratio,
        ),
        "expected_near_term_import_w": _expected_near_term_import_w(
            grid_interaction_w,
            discharge_power,
            import_support_bias,
            response_weight,
            smoothing_ratio,
            _discharge_soc_scale(combined_soc, learning.get("reserve_band_floor_soc")),
        ),
    }


def _headroom_limit(
    configured_limit_power_w: object,
    observed_max_power_w: object,
    average_active_power_w: object,
    current_power_w: float | None,
) -> float | None:
    current = _current_headroom_power(current_power_w)
    for limit in (
        _non_negative_optional_float(configured_limit_power_w),
        _non_negative_optional_float(observed_max_power_w),
        _average_headroom_limit(average_active_power_w),
    ):
        if limit is not None:
            return max(float(limit), current)
    return current if current > 0.0 else None


def _current_headroom_power(current_power_w: float | None) -> float:
    return 0.0 if current_power_w is None else float(current_power_w)


def _average_headroom_limit(average_active_power_w: object) -> float | None:
    average = _non_negative_optional_float(average_active_power_w)
    if average is None:
        return None
    return float(average) * 1.25


def _headroom_w(limit_power_w: float | None, current_power_w: float | None) -> float | None:
    if limit_power_w is None:
        return None
    current = 0.0 if current_power_w is None else float(current_power_w)
    return max(0.0, float(limit_power_w) - current)


def _scaled_headroom_w(limit_power_w: float | None, current_power_w: float | None, scale: float) -> float | None:
    headroom = _headroom_w(limit_power_w, current_power_w)
    if headroom is None:
        return None
    return max(0.0, float(headroom) * max(0.0, min(1.0, float(scale))))


def _expected_near_term_export_w(
    grid_interaction_w: float | None,
    charge_power_w: float | None,
    charge_headroom_w: float | None,
    observed_max_charge_w: float | None,
    export_bias: float,
    battery_first_export_bias: float,
    response_weight: float,
    smoothing_ratio: float,
) -> float | None:
    if grid_interaction_w is None:
        return None
    base_export_w = max(0.0, -float(grid_interaction_w))
    charge_power = 0.0 if charge_power_w is None else float(charge_power_w)
    saturation = _charge_saturation(charge_headroom_w, observed_max_charge_w, charge_power)
    smoothing_weight = 1.0 - (0.5 * max(0.0, min(1.0, float(smoothing_ratio))))
    export_risk_w = export_bias * response_weight * charge_power * (0.5 + (0.5 * saturation)) * smoothing_weight
    battery_capture_credit_w = battery_first_export_bias * response_weight * charge_power * (0.25 + (0.75 * smoothing_ratio))
    return max(0.0, base_export_w + export_risk_w - battery_capture_credit_w)


def _expected_near_term_import_w(
    grid_interaction_w: float | None,
    discharge_power_w: float | None,
    import_support_bias: float,
    response_weight: float,
    smoothing_ratio: float,
    discharge_soc_scale: float,
) -> float | None:
    if grid_interaction_w is None:
        return None
    base_import_w = max(0.0, float(grid_interaction_w))
    discharge_power = 0.0 if discharge_power_w is None else float(discharge_power_w)
    support_relief_w = import_support_bias * response_weight * discharge_power * discharge_soc_scale
    support_relief_w *= 1.0 - (0.25 * max(0.0, min(1.0, float(smoothing_ratio))))
    return max(0.0, base_import_w - support_relief_w)


def _charge_saturation(
    charge_headroom_w: float | None,
    observed_max_charge_w: float | None,
    charge_power_w: float,
) -> float:
    if observed_max_charge_w is not None and observed_max_charge_w > 0.0 and charge_headroom_w is not None:
        return max(0.0, min(1.0, 1.0 - (float(charge_headroom_w) / float(observed_max_charge_w))))
    if charge_power_w <= 0.0:
        return 0.0
    return 1.0


def _response_weight(delay_seconds: object) -> float:
    normalized_delay = _non_negative_optional_float(delay_seconds)
    if normalized_delay is None:
        return 1.0
    return 1.0 / (1.0 + (float(normalized_delay) / 30.0))


def _charge_soc_scale(combined_soc: float | None, reserve_ceiling_soc: object) -> float:
    ceiling = _non_negative_optional_float(reserve_ceiling_soc)
    if combined_soc is None or ceiling is None:
        return 1.0
    if combined_soc >= ceiling:
        return 0.0
    return min(1.0, max(0.0, (float(ceiling) - float(combined_soc)) / 10.0))


def _discharge_soc_scale(combined_soc: float | None, reserve_floor_soc: object) -> float:
    floor = _non_negative_optional_float(reserve_floor_soc)
    if combined_soc is None or floor is None:
        return 1.0
    if combined_soc <= floor:
        return 0.0
    return min(1.0, max(0.0, (float(combined_soc) - float(floor)) / 10.0))


def _positive_bias(value: object, *, fallback: object | None = None) -> float:
    normalized = _optional_float(value)
    if normalized is None:
        normalized = _optional_float(fallback)
    if normalized is None:
        return 0.0
    return max(0.0, min(1.0, float(normalized)))


def _non_negative_optional_float(value: object) -> float | None:
    return non_negative_optional_float(value)


def _optional_float(value: object) -> float | None:
    return optional_float(value)
