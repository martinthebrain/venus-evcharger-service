# SPDX-License-Identifier: GPL-3.0-or-later
"""Coercion helpers for runtime-only external energy learning profiles."""

from __future__ import annotations

from typing import Any, Mapping

from .models import EnergyLearningProfile
from .numeric import optional_float


def _normalized_profile_iter(
    profiles: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
) -> tuple[EnergyLearningProfile, ...]:
    normalized_profiles: list[EnergyLearningProfile] = []
    for source_id, raw_profile in dict(profiles or {}).items():
        if isinstance(raw_profile, EnergyLearningProfile):
            normalized_profiles.append(raw_profile)
            continue
        normalized_profiles.append(_coerce_learning_profile(source_id, raw_profile))
    return tuple(normalized_profiles)


def _coerce_learning_profile(source_id: str, raw_profile: Mapping[str, Any]) -> EnergyLearningProfile:
    int_fields = _coerced_learning_int_fields(raw_profile)
    float_fields = _coerced_learning_float_fields(raw_profile)
    return EnergyLearningProfile(
        source_id=str(raw_profile.get("source_id", source_id)),
        sample_count=int_fields["sample_count"],
        active_sample_count=int_fields["active_sample_count"],
        charge_sample_count=int_fields["charge_sample_count"],
        discharge_sample_count=int_fields["discharge_sample_count"],
        import_support_sample_count=int_fields["import_support_sample_count"],
        import_charge_sample_count=int_fields["import_charge_sample_count"],
        export_charge_sample_count=int_fields["export_charge_sample_count"],
        export_discharge_sample_count=int_fields["export_discharge_sample_count"],
        export_idle_sample_count=int_fields["export_idle_sample_count"],
        day_active_sample_count=int_fields["day_active_sample_count"],
        night_active_sample_count=int_fields["night_active_sample_count"],
        day_charge_sample_count=int_fields["day_charge_sample_count"],
        night_charge_sample_count=int_fields["night_charge_sample_count"],
        day_discharge_sample_count=int_fields["day_discharge_sample_count"],
        night_discharge_sample_count=int_fields["night_discharge_sample_count"],
        response_sample_count=int_fields["response_sample_count"],
        smoothing_sample_count=int_fields["smoothing_sample_count"],
        observed_max_charge_power_w=float_fields["observed_max_charge_power_w"],
        observed_max_discharge_power_w=float_fields["observed_max_discharge_power_w"],
        observed_max_ac_power_w=float_fields["observed_max_ac_power_w"],
        observed_max_pv_input_power_w=float_fields["observed_max_pv_input_power_w"],
        observed_max_grid_import_w=float_fields["observed_max_grid_import_w"],
        observed_max_grid_export_w=float_fields["observed_max_grid_export_w"],
        observed_min_discharge_soc=float_fields["observed_min_discharge_soc"],
        observed_max_charge_soc=float_fields["observed_max_charge_soc"],
        average_active_charge_power_w=float_fields["average_active_charge_power_w"],
        average_active_discharge_power_w=float_fields["average_active_discharge_power_w"],
        average_active_power_delta_w=float_fields["average_active_power_delta_w"],
        typical_response_delay_seconds=float_fields["typical_response_delay_seconds"],
        direction_change_count=int_fields["direction_change_count"],
        last_direction=_normalized_direction(raw_profile.get("last_direction")),
        last_activity_state=_normalized_activity_state(raw_profile.get("last_activity_state")),
        last_active_at=float_fields["last_active_at"],
        last_inactive_at=float_fields["last_inactive_at"],
        last_change_at=float_fields["last_change_at"],
    )


def _coerced_learning_int_fields(raw_profile: Mapping[str, Any]) -> dict[str, int]:
    return {
        "sample_count": _coerced_int(raw_profile, "sample_count"),
        "active_sample_count": _coerced_int(raw_profile, "active_sample_count"),
        "charge_sample_count": _coerced_int(raw_profile, "charge_sample_count"),
        "discharge_sample_count": _coerced_int(raw_profile, "discharge_sample_count"),
        "import_support_sample_count": _coerced_int(raw_profile, "import_support_sample_count"),
        "import_charge_sample_count": _coerced_int(raw_profile, "import_charge_sample_count"),
        "export_charge_sample_count": _coerced_int(raw_profile, "export_charge_sample_count"),
        "export_discharge_sample_count": _coerced_int(raw_profile, "export_discharge_sample_count"),
        "export_idle_sample_count": _coerced_int(raw_profile, "export_idle_sample_count"),
        "day_active_sample_count": _coerced_int(raw_profile, "day_active_sample_count"),
        "night_active_sample_count": _coerced_int(raw_profile, "night_active_sample_count"),
        "day_charge_sample_count": _coerced_int(raw_profile, "day_charge_sample_count"),
        "night_charge_sample_count": _coerced_int(raw_profile, "night_charge_sample_count"),
        "day_discharge_sample_count": _coerced_int(raw_profile, "day_discharge_sample_count"),
        "night_discharge_sample_count": _coerced_int(raw_profile, "night_discharge_sample_count"),
        "response_sample_count": _coerced_int(raw_profile, "response_sample_count"),
        "smoothing_sample_count": _coerced_int(raw_profile, "smoothing_sample_count"),
        "direction_change_count": _coerced_int(raw_profile, "direction_change_count"),
    }


def _coerced_learning_float_fields(raw_profile: Mapping[str, Any]) -> dict[str, float | None]:
    return {
        "observed_max_charge_power_w": _optional_float(raw_profile.get("observed_max_charge_power_w")),
        "observed_max_discharge_power_w": _optional_float(raw_profile.get("observed_max_discharge_power_w")),
        "observed_max_ac_power_w": _optional_float(raw_profile.get("observed_max_ac_power_w")),
        "observed_max_pv_input_power_w": _optional_float(raw_profile.get("observed_max_pv_input_power_w")),
        "observed_max_grid_import_w": _optional_float(raw_profile.get("observed_max_grid_import_w")),
        "observed_max_grid_export_w": _optional_float(raw_profile.get("observed_max_grid_export_w")),
        "observed_min_discharge_soc": _optional_float(raw_profile.get("observed_min_discharge_soc")),
        "observed_max_charge_soc": _optional_float(raw_profile.get("observed_max_charge_soc")),
        "average_active_charge_power_w": _optional_float(raw_profile.get("average_active_charge_power_w")),
        "average_active_discharge_power_w": _optional_float(raw_profile.get("average_active_discharge_power_w")),
        "average_active_power_delta_w": _optional_float(raw_profile.get("average_active_power_delta_w")),
        "typical_response_delay_seconds": _optional_float(raw_profile.get("typical_response_delay_seconds")),
        "last_active_at": _optional_float(raw_profile.get("last_active_at")),
        "last_inactive_at": _optional_float(raw_profile.get("last_inactive_at")),
        "last_change_at": _optional_float(raw_profile.get("last_change_at")),
    }


def _optional_float(value: object) -> float | None:
    return optional_float(value)


def _coerced_int(raw_profile: Mapping[str, Any], key: str) -> int:
    return int(raw_profile.get(key) or 0)


def _normalized_direction(value: object) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in {"charge", "discharge"} else "idle"


def _normalized_activity_state(value: object) -> str:
    normalized = str(value).strip().lower()
    return "active" if normalized == "active" else "idle"
