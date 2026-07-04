# SPDX-License-Identifier: GPL-3.0-or-later
"""Update logic for runtime-only external energy learning profiles."""

from __future__ import annotations

from typing import Callable, TypedDict

from .models import EnergyLearningProfile, EnergySourceSnapshot

_ACTIVE_POWER_THRESHOLD_W = 50.0
_GRID_ACTIVITY_THRESHOLD_W = 100.0


class _SampleUpdates(TypedDict):
    sample_count: int
    active_sample_count: int
    charge_sample_count: int
    discharge_sample_count: int
    import_support_sample_count: int
    import_charge_sample_count: int
    export_charge_sample_count: int
    export_discharge_sample_count: int
    export_idle_sample_count: int
    day_active_sample_count: int
    night_active_sample_count: int
    day_charge_sample_count: int
    night_charge_sample_count: int
    day_discharge_sample_count: int
    night_discharge_sample_count: int
    response_sample_count: int
    smoothing_sample_count: int


class _ObservationUpdates(TypedDict):
    observed_max_charge_power_w: float | None
    observed_max_discharge_power_w: float | None
    observed_max_ac_power_w: float | None
    observed_max_pv_input_power_w: float | None
    observed_max_grid_import_w: float | None
    observed_max_grid_export_w: float | None
    observed_min_discharge_soc: float | None
    observed_max_charge_soc: float | None


class _ActivityUpdates(TypedDict):
    average_active_charge_power_w: float | None
    average_active_discharge_power_w: float | None
    average_active_power_delta_w: float | None
    typical_response_delay_seconds: float | None
    direction_change_count: int
    last_direction: str
    last_activity_state: str
    last_active_at: float | None
    last_inactive_at: float | None
    last_change_at: float | None


class _ActivityMarkers(TypedDict):
    last_direction: str
    last_activity_state: str
    last_active_at: float | None
    last_inactive_at: float | None
    last_change_at: float | None


def _build_updated_learning_profile(
    previous: EnergyLearningProfile,
    source: EnergySourceSnapshot,
    now: float,
    sample_period: Callable[[float], str],
) -> EnergyLearningProfile:
    (
        direction,
        active,
        charge_power,
        discharge_power,
        observed_ac,
        observed_pv_input,
        grid_import,
        grid_export,
    ) = _source_learning_metrics(source)
    response_delay = _activation_response_delay(previous, active, direction, now)
    sample_updates = _profile_sample_updates(
        previous,
        active,
        direction,
        grid_import,
        grid_export,
        response_delay,
        sample_period(now),
        _smoothing_delta(previous, direction, charge_power, discharge_power),
    )
    observation_updates = _profile_observation_updates(
        previous,
        source,
        direction,
        charge_power,
        discharge_power,
        observed_ac,
        observed_pv_input,
        grid_import,
        grid_export,
    )
    activity_updates = _profile_activity_updates(
        previous,
        direction,
        active,
        now,
        charge_power,
        discharge_power,
        response_delay,
    )
    return EnergyLearningProfile(
        source_id=source.source_id,
        sample_count=sample_updates["sample_count"],
        active_sample_count=sample_updates["active_sample_count"],
        charge_sample_count=sample_updates["charge_sample_count"],
        discharge_sample_count=sample_updates["discharge_sample_count"],
        import_support_sample_count=sample_updates["import_support_sample_count"],
        import_charge_sample_count=sample_updates["import_charge_sample_count"],
        export_charge_sample_count=sample_updates["export_charge_sample_count"],
        export_discharge_sample_count=sample_updates["export_discharge_sample_count"],
        export_idle_sample_count=sample_updates["export_idle_sample_count"],
        day_active_sample_count=sample_updates["day_active_sample_count"],
        night_active_sample_count=sample_updates["night_active_sample_count"],
        day_charge_sample_count=sample_updates["day_charge_sample_count"],
        night_charge_sample_count=sample_updates["night_charge_sample_count"],
        day_discharge_sample_count=sample_updates["day_discharge_sample_count"],
        night_discharge_sample_count=sample_updates["night_discharge_sample_count"],
        response_sample_count=sample_updates["response_sample_count"],
        smoothing_sample_count=sample_updates["smoothing_sample_count"],
        observed_max_charge_power_w=observation_updates["observed_max_charge_power_w"],
        observed_max_discharge_power_w=observation_updates["observed_max_discharge_power_w"],
        observed_max_ac_power_w=observation_updates["observed_max_ac_power_w"],
        observed_max_pv_input_power_w=observation_updates["observed_max_pv_input_power_w"],
        observed_max_grid_import_w=observation_updates["observed_max_grid_import_w"],
        observed_max_grid_export_w=observation_updates["observed_max_grid_export_w"],
        observed_min_discharge_soc=observation_updates["observed_min_discharge_soc"],
        observed_max_charge_soc=observation_updates["observed_max_charge_soc"],
        average_active_charge_power_w=activity_updates["average_active_charge_power_w"],
        average_active_discharge_power_w=activity_updates["average_active_discharge_power_w"],
        average_active_power_delta_w=activity_updates["average_active_power_delta_w"],
        typical_response_delay_seconds=activity_updates["typical_response_delay_seconds"],
        direction_change_count=activity_updates["direction_change_count"],
        last_direction=activity_updates["last_direction"],
        last_activity_state=activity_updates["last_activity_state"],
        last_active_at=activity_updates["last_active_at"],
        last_inactive_at=activity_updates["last_inactive_at"],
        last_change_at=activity_updates["last_change_at"],
    )


def _source_learning_metrics(
    source: EnergySourceSnapshot,
) -> tuple[str, bool, float | None, float | None, float | None, float | None, float | None, float | None]:
    direction = _direction(source)
    return (
        direction,
        _is_active(source, direction),
        source.charge_power_w,
        source.discharge_power_w,
        _absolute_optional(source.ac_power_w),
        _positive_optional(source.pv_input_power_w),
        _positive_optional(source.grid_interaction_w),
        _positive_optional(None if source.grid_interaction_w is None else max(0.0, -float(source.grid_interaction_w))),
    )


def _profile_sample_updates(
    previous: EnergyLearningProfile,
    active: bool,
    direction: str,
    grid_import: float | None,
    grid_export: float | None,
    response_delay: float | None,
    period: str,
    smoothing_delta_w: float | None,
) -> _SampleUpdates:
    direction_increments = _direction_sample_increments(direction)
    grid_increments = _grid_sample_increments(direction, grid_import, grid_export)
    period_increments = _period_sample_increments(active, direction, period)
    return {
        "sample_count": int(previous.sample_count) + 1,
        "active_sample_count": int(previous.active_sample_count) + (1 if active else 0),
        "charge_sample_count": int(previous.charge_sample_count) + direction_increments["charge"],
        "discharge_sample_count": int(previous.discharge_sample_count) + direction_increments["discharge"],
        "import_support_sample_count": int(previous.import_support_sample_count) + grid_increments["import_support"],
        "import_charge_sample_count": int(previous.import_charge_sample_count) + grid_increments["import_charge"],
        "export_charge_sample_count": int(previous.export_charge_sample_count) + grid_increments["export_charge"],
        "export_discharge_sample_count": int(previous.export_discharge_sample_count) + grid_increments["export_discharge"],
        "export_idle_sample_count": int(previous.export_idle_sample_count) + grid_increments["export_idle"],
        "day_active_sample_count": int(previous.day_active_sample_count) + period_increments["day_active"],
        "night_active_sample_count": int(previous.night_active_sample_count) + period_increments["night_active"],
        "day_charge_sample_count": int(previous.day_charge_sample_count) + period_increments["day_charge"],
        "night_charge_sample_count": int(previous.night_charge_sample_count) + period_increments["night_charge"],
        "day_discharge_sample_count": int(previous.day_discharge_sample_count) + period_increments["day_discharge"],
        "night_discharge_sample_count": int(previous.night_discharge_sample_count) + period_increments["night_discharge"],
        "response_sample_count": int(previous.response_sample_count) + (1 if response_delay is not None else 0),
        "smoothing_sample_count": int(previous.smoothing_sample_count) + (1 if smoothing_delta_w is not None else 0),
    }


def _profile_observation_updates(
    previous: EnergyLearningProfile,
    source: EnergySourceSnapshot,
    direction: str,
    charge_power: float | None,
    discharge_power: float | None,
    observed_ac: float | None,
    observed_pv_input: float | None,
    grid_import: float | None,
    grid_export: float | None,
) -> _ObservationUpdates:
    return {
        "observed_max_charge_power_w": _max_optional(previous.observed_max_charge_power_w, charge_power),
        "observed_max_discharge_power_w": _max_optional(previous.observed_max_discharge_power_w, discharge_power),
        "observed_max_ac_power_w": _max_optional(previous.observed_max_ac_power_w, observed_ac),
        "observed_max_pv_input_power_w": _max_optional(previous.observed_max_pv_input_power_w, observed_pv_input),
        "observed_max_grid_import_w": _max_optional(previous.observed_max_grid_import_w, grid_import),
        "observed_max_grid_export_w": _max_optional(previous.observed_max_grid_export_w, grid_export),
        "observed_min_discharge_soc": _min_optional(
            previous.observed_min_discharge_soc,
            source.soc if direction == "discharge" else None,
        ),
        "observed_max_charge_soc": _max_optional(
            previous.observed_max_charge_soc,
            source.soc if direction == "charge" else None,
        ),
    }


def _profile_activity_updates(
    previous: EnergyLearningProfile,
    direction: str,
    active: bool,
    now: float,
    charge_power: float | None,
    discharge_power: float | None,
    response_delay: float | None,
) -> _ActivityUpdates:
    smoothing_delta_w = _smoothing_delta(previous, direction, charge_power, discharge_power)
    markers = _activity_markers(previous, direction, active, now)
    return {
        "average_active_charge_power_w": _rolling_average(
            previous.average_active_charge_power_w,
            previous.charge_sample_count,
            charge_power if direction == "charge" else None,
        ),
        "average_active_discharge_power_w": _rolling_average(
            previous.average_active_discharge_power_w,
            previous.discharge_sample_count,
            discharge_power if direction == "discharge" else None,
        ),
        "average_active_power_delta_w": _rolling_average(
            previous.average_active_power_delta_w,
            previous.smoothing_sample_count,
            smoothing_delta_w,
        ),
        "typical_response_delay_seconds": _rolling_average(
            previous.typical_response_delay_seconds,
            previous.response_sample_count,
            response_delay,
        ),
        "direction_change_count": int(previous.direction_change_count) + (1 if _direction_changed(previous, direction) else 0),
        "last_direction": markers["last_direction"],
        "last_activity_state": markers["last_activity_state"],
        "last_active_at": markers["last_active_at"],
        "last_inactive_at": markers["last_inactive_at"],
        "last_change_at": markers["last_change_at"],
    }


def _direction(source: EnergySourceSnapshot) -> str:
    net_power = source.net_battery_power_w
    if net_power is None:
        return "idle"
    if float(net_power) <= -_ACTIVE_POWER_THRESHOLD_W:
        return "charge"
    if float(net_power) >= _ACTIVE_POWER_THRESHOLD_W:
        return "discharge"
    return "idle"


def _is_active(source: EnergySourceSnapshot, direction: str) -> bool:
    if direction in {"charge", "discharge"}:
        return True
    return any(
        abs(float(value)) >= _ACTIVE_POWER_THRESHOLD_W
        for value in (source.ac_power_w, source.pv_input_power_w)
        if value is not None
    )


def _activation_response_delay(
    previous: EnergyLearningProfile,
    active: bool,
    direction: str,
    now: float,
) -> float | None:
    if not active:
        return None
    return _inactive_response_delay(previous, now) or _direction_change_response_delay(previous, direction, now)


def _activity_markers(
    previous: EnergyLearningProfile,
    direction: str,
    active: bool,
    now: float,
) -> _ActivityMarkers:
    return {
        "last_direction": direction,
        "last_activity_state": "active" if active else "idle",
        "last_active_at": float(now) if active else previous.last_active_at,
        "last_inactive_at": float(now) if not active else previous.last_inactive_at,
        "last_change_at": float(now),
    }


def _inactive_response_delay(previous: EnergyLearningProfile, now: float) -> float | None:
    if previous.last_activity_state == "active" or previous.last_inactive_at is None:
        return None
    return max(0.0, float(now) - float(previous.last_inactive_at))


def _direction_change_response_delay(
    previous: EnergyLearningProfile,
    direction: str,
    now: float,
) -> float | None:
    if not _direction_changed(previous, direction) or previous.last_change_at is None:
        return None
    return max(0.0, float(now) - float(previous.last_change_at))


def _direction_changed(previous: EnergyLearningProfile, direction: str) -> bool:
    if direction not in {"charge", "discharge"}:
        return False
    return previous.last_direction in {"charge", "discharge"} and previous.last_direction != direction


def _direction_sample_increments(direction: str) -> dict[str, int]:
    return {"charge": 1 if direction == "charge" else 0, "discharge": 1 if direction == "discharge" else 0}


def _grid_sample_increments(direction: str, grid_import: float | None, grid_export: float | None) -> dict[str, int]:
    return {
        "import_support": _grid_increment(direction, grid_import, "discharge"),
        "import_charge": _grid_increment(direction, grid_import, "charge"),
        "export_charge": _grid_increment(direction, grid_export, "charge"),
        "export_discharge": _grid_increment(direction, grid_export, "discharge"),
        "export_idle": _grid_idle_increment(direction, grid_export),
    }


def _period_sample_increments(active: bool, direction: str, period: str) -> dict[str, int]:
    return {
        "day_active": _active_period_increment(active, period, expected_period="day"),
        "night_active": _active_period_increment(active, period, expected_period="night"),
        "day_charge": _period_increment(active, direction, period, expected_direction="charge", expected_period="day"),
        "night_charge": _period_increment(active, direction, period, expected_direction="charge", expected_period="night"),
        "day_discharge": _period_increment(
            active, direction, period, expected_direction="discharge", expected_period="day"
        ),
        "night_discharge": _period_increment(
            active, direction, period, expected_direction="discharge", expected_period="night"
        ),
    }


def _grid_increment(direction: str, grid_value: float | None, expected_direction: str) -> int:
    if grid_value is None:
        return 0
    return 1 if direction == expected_direction and float(grid_value) >= _GRID_ACTIVITY_THRESHOLD_W else 0


def _grid_idle_increment(direction: str, grid_export: float | None) -> int:
    if grid_export is None:
        return 0
    return 1 if direction == "idle" and float(grid_export) >= _GRID_ACTIVITY_THRESHOLD_W else 0


def _active_period_increment(
    active: bool,
    period: str,
    *,
    expected_period: str,
) -> int:
    return 1 if active and period == expected_period else 0


def _period_increment(
    active: bool,
    direction: str,
    period: str,
    *,
    expected_direction: str,
    expected_period: str,
) -> int:
    if not active or period != expected_period:
        return 0
    return 1 if direction == expected_direction else 0


def _smoothing_delta(
    previous: EnergyLearningProfile,
    direction: str,
    charge_power: float | None,
    discharge_power: float | None,
) -> float | None:
    current_power, average_power = _directional_power_pair(previous, direction, charge_power, discharge_power)
    if current_power is None or average_power is None:
        return None
    return abs(float(current_power) - float(average_power))


def _directional_power_pair(
    previous: EnergyLearningProfile,
    direction: str,
    charge_power: float | None,
    discharge_power: float | None,
) -> tuple[float | None, float | None]:
    if direction == "charge":
        return charge_power, previous.average_active_charge_power_w
    if direction == "discharge":
        return discharge_power, previous.average_active_discharge_power_w
    return None, None


def _absolute_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return abs(float(value))


def _positive_optional(value: float | None) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    return normalized if normalized > 0.0 else None


def _max_optional(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return max(float(current), float(candidate))


def _min_optional(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return min(float(current), float(candidate))


def _rolling_average(current: float | None, count: int, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return ((float(current) * float(count)) + float(candidate)) / float(count + 1)
