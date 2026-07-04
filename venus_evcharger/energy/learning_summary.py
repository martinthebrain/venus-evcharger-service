# SPDX-License-Identifier: GPL-3.0-or-later
"""Summary helpers for runtime-only external energy learning profiles."""

from __future__ import annotations

from typing import Iterable, Mapping

from .models import EnergyLearningProfile
from .numeric import sum_optional


def _summarize_normalized_energy_learning_profiles(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
) -> dict[str, float | int | None]:
    counts = _profile_count_totals(normalized_profiles)
    return {
        "profile_count": len(normalized_profiles),
        "sample_count": counts["sample_count"],
        "active_sample_count": counts["active_sample_count"],
        "direction_change_count": counts["direction_change_count"],
        "day_active_sample_count": counts["day_active_count"],
        "night_active_sample_count": counts["night_active_count"],
        **_profile_power_summaries(normalized_profiles),
        **_profile_average_summaries(normalized_profiles),
        **_profile_bias_summaries(counts),
        **_profile_reserve_summaries(normalized_profiles),
    }


def _profile_count_totals(normalized_profiles: tuple[EnergyLearningProfile, ...]) -> dict[str, int]:
    return {
        "sample_count": _sum_profile_int(normalized_profiles, "sample_count"),
        "active_sample_count": _sum_profile_int(normalized_profiles, "active_sample_count"),
        "direction_change_count": _sum_profile_int(normalized_profiles, "direction_change_count"),
        "day_active_count": _sum_profile_int(normalized_profiles, "day_active_sample_count"),
        "night_active_count": _sum_profile_int(normalized_profiles, "night_active_sample_count"),
        "charge_count": _sum_profile_int(normalized_profiles, "charge_sample_count"),
        "discharge_count": _sum_profile_int(normalized_profiles, "discharge_sample_count"),
        "import_support_count": _sum_profile_int(normalized_profiles, "import_support_sample_count"),
        "import_charge_count": _sum_profile_int(normalized_profiles, "import_charge_sample_count"),
        "export_charge_count": _sum_profile_int(normalized_profiles, "export_charge_sample_count"),
        "export_discharge_count": _sum_profile_int(normalized_profiles, "export_discharge_sample_count"),
        "export_idle_count": _sum_profile_int(normalized_profiles, "export_idle_sample_count"),
        "day_charge_count": _sum_profile_int(normalized_profiles, "day_charge_sample_count"),
        "night_charge_count": _sum_profile_int(normalized_profiles, "night_charge_sample_count"),
        "day_discharge_count": _sum_profile_int(normalized_profiles, "day_discharge_sample_count"),
        "night_discharge_count": _sum_profile_int(normalized_profiles, "night_discharge_sample_count"),
    }


def _profile_power_summaries(normalized_profiles: tuple[EnergyLearningProfile, ...]) -> dict[str, float | None]:
    power_fields = (
        "observed_max_charge_power_w",
        "observed_max_discharge_power_w",
        "observed_max_ac_power_w",
        "observed_max_pv_input_power_w",
        "observed_max_grid_import_w",
        "observed_max_grid_export_w",
    )
    return {field_name: _sum_optional(getattr(profile, field_name) for profile in normalized_profiles) for field_name in power_fields}


def _profile_average_summaries(normalized_profiles: tuple[EnergyLearningProfile, ...]) -> dict[str, float | None]:
    return {
        "average_active_charge_power_w": _weighted_profile_average(
            normalized_profiles, "average_active_charge_power_w", "charge_sample_count"
        ),
        "average_active_discharge_power_w": _weighted_profile_average(
            normalized_profiles, "average_active_discharge_power_w", "discharge_sample_count"
        ),
        "typical_response_delay_seconds": _mean_optional(
            profile.typical_response_delay_seconds for profile in normalized_profiles
        ),
        "average_active_power_delta_w": _weighted_profile_average(
            normalized_profiles, "average_active_power_delta_w", "smoothing_sample_count"
        ),
        "power_smoothing_ratio": _weighted_average(
            _weighted_profile_pairs(
                normalized_profiles,
                value_field="power_smoothing_ratio",
                weight_field="smoothing_sample_count",
            )
        ),
    }


def _profile_bias_summaries(counts: Mapping[str, int]) -> dict[str, float | None]:
    return {
        "support_bias": _bias_from_counts(counts["discharge_count"], counts["charge_count"]),
        "import_support_bias": _bias_from_counts(counts["import_support_count"], counts["import_charge_count"]),
        "export_bias": _bias_from_counts(counts["export_charge_count"], counts["export_discharge_count"]),
        "battery_first_export_bias": _bias_from_counts(
            counts["export_charge_count"],
            counts["export_discharge_count"] + counts["export_idle_count"],
        ),
        "day_support_bias": _bias_from_counts(counts["day_discharge_count"], counts["day_charge_count"]),
        "night_support_bias": _bias_from_counts(counts["night_discharge_count"], counts["night_charge_count"]),
    }


def _profile_reserve_summaries(normalized_profiles: tuple[EnergyLearningProfile, ...]) -> dict[str, float | None]:
    reserve_floor = _max_known(_known_profile_values(normalized_profiles, "reserve_band_floor_soc"))
    reserve_ceiling = _min_known(_known_profile_values(normalized_profiles, "reserve_band_ceiling_soc"))
    return {
        "reserve_band_floor_soc": reserve_floor,
        "reserve_band_ceiling_soc": reserve_ceiling,
        "reserve_band_width_soc": _reserve_band_width(reserve_floor, reserve_ceiling),
    }


def _sum_profile_int(normalized_profiles: tuple[EnergyLearningProfile, ...], field_name: str) -> int:
    return sum(int(getattr(profile, field_name)) for profile in normalized_profiles)


_sum_optional = sum_optional


def _mean_optional(values: Iterable[float | None]) -> float | None:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values) / float(len(numeric_values))


def _bias_from_counts(positive_count: int, negative_count: int) -> float | None:
    total = int(positive_count) + int(negative_count)
    if total <= 0:
        return None
    return (float(positive_count) - float(negative_count)) / float(total)


def _weighted_profile_average(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
    value_field: str,
    weight_field: str,
) -> float | None:
    return _weighted_average(_weighted_profile_pairs(normalized_profiles, value_field=value_field, weight_field=weight_field))


def _weighted_profile_pairs(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
    *,
    value_field: str,
    weight_field: str,
) -> tuple[tuple[float | None, float], ...]:
    return tuple((getattr(profile, value_field), float(getattr(profile, weight_field))) for profile in normalized_profiles)


def _weighted_average(values: tuple[tuple[float | None, float], ...]) -> float | None:
    weighted_total = 0.0
    weight_total = 0.0
    for value, weight in values:
        if value is None:
            continue
        normalized_weight = max(0.0, float(weight))
        weighted_total += float(value) * normalized_weight
        weight_total += normalized_weight
    if weight_total <= 0.0:
        return None
    return weighted_total / weight_total


def _known_profile_values(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
    field_name: str,
) -> tuple[float, ...]:
    return tuple(
        float(value)
        for profile in normalized_profiles
        for value in (getattr(profile, field_name),)
        if value is not None
    )


def _max_known(values: Iterable[float]) -> float | None:
    normalized = [float(value) for value in values]
    if not normalized:
        return None
    return max(normalized)


def _min_known(values: Iterable[float]) -> float | None:
    normalized = [float(value) for value in values]
    if not normalized:
        return None
    return min(normalized)


def _reserve_band_width(reserve_floor: float | None, reserve_ceiling: float | None) -> float | None:
    if reserve_floor is None or reserve_ceiling is None or reserve_ceiling < reserve_floor:
        return None
    return float(reserve_ceiling) - float(reserve_floor)
