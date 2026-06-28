# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for Victron ESS balance-bias learning profiles."""

from __future__ import annotations

from typing import Any, Callable


def _victron_ess_balance_grid_site_regime(grid_interaction_w: float | None) -> str:
    if grid_interaction_w is not None and grid_interaction_w <= -25.0:
        return "export"
    if grid_interaction_w is not None and grid_interaction_w >= 25.0:
        return "import"
    return ""


def _victron_ess_balance_forecast_site_regime(expected_export_w: float, expected_import_w: float) -> str:
    if expected_export_w > max(25.0, expected_import_w):
        return "export"
    if expected_import_w > max(25.0, expected_export_w):
        return "import"
    return ""


def _victron_ess_balance_action_direction_site_regime(action_direction: str) -> str:
    return "export" if action_direction == "more_export" else "import"


def _victron_ess_balance_near_discharge_limit(site_regime: str, combined_discharge_headroom_w: float | None) -> bool:
    return site_regime == "export" and combined_discharge_headroom_w is not None and combined_discharge_headroom_w <= 300.0


def _victron_ess_balance_near_charge_limit(site_regime: str, combined_charge_headroom_w: float | None) -> bool:
    return site_regime == "import" and combined_charge_headroom_w is not None and combined_charge_headroom_w <= 300.0


def _victron_ess_balance_pv_phase(expected_export_w: float, pv_input_power_w: float) -> str:
    return "pv_strong" if max(expected_export_w, pv_input_power_w) >= 1500.0 else "pv_weak"


def _victron_ess_balance_adaptive_scalar_int(value: Any) -> int:
    return max(0, int(value or 0))


def _victron_ess_balance_adaptive_scalar_str(value: Any) -> str:
    return str(value or "")


def _victron_ess_balance_adaptive_scalar_value(
    raw_value: Any,
    caster: Any,
    optional_float: Callable[[Any], float | None],
) -> Any:
    scalar_casts: dict[str, Callable[[Any], Any]] = {
        "int": _victron_ess_balance_adaptive_scalar_int,
        "str": _victron_ess_balance_adaptive_scalar_str,
        "bool": bool,
        "optional_float": optional_float,
    }
    return scalar_casts[str(caster)](raw_value)


def _victron_ess_balance_learning_profile_key(
    action_direction: str,
    site_regime: str,
    day_phase: str,
    reserve_phase: str,
    ev_phase: str,
    pv_phase: str,
    battery_limit_phase: str,
) -> str:
    return f"{action_direction}:{site_regime}:{day_phase}:{reserve_phase}:{ev_phase}:{pv_phase}:{battery_limit_phase}"


def _victron_ess_balance_profile_identity(profile_key: str) -> dict[str, str]:
    parts = profile_key.split(":")
    action_direction = ""
    site_regime = ""
    day_phase = ""
    reserve_phase = ""
    ev_phase = "ev_idle"
    pv_phase = "pv_weak"
    battery_limit_phase = "mid_band"
    if len(parts) >= 4:
        action_direction, site_regime, day_phase, reserve_phase = parts[:4]
    elif len(parts) >= 3:
        site_regime, day_phase, reserve_phase = parts[:3]
    elif parts:
        site_regime = parts[0]
    if len(parts) >= 7:
        ev_phase, pv_phase, battery_limit_phase = parts[4:7]
    return {
        "key": profile_key,
        "action_direction": action_direction,
        "site_regime": site_regime,
        "direction": site_regime,
        "day_phase": day_phase,
        "reserve_phase": reserve_phase,
        "ev_phase": ev_phase,
        "pv_phase": pv_phase,
        "battery_limit_phase": battery_limit_phase,
    }


def _victron_ess_balance_profile_counter(profile: dict[str, Any], field: str) -> int:
    return max(0, int(profile.get(field, 0) or 0))


def _victron_ess_balance_update_profile_sample(
    profile: dict[str, Any],
    sample: float,
    *,
    samples_field: str,
    value_field: str,
    deviation_field: str,
    optional_float: Callable[[Any], float | None],
    ewma: Callable[[float | None, float, int], float],
) -> None:
    """Update one learned profile sample value and its mean absolute deviation."""
    samples = _victron_ess_balance_profile_counter(profile, samples_field)
    current_value = optional_float(profile.get(value_field))
    if current_value is not None:
        profile[deviation_field] = ewma(
            optional_float(profile.get(deviation_field)),
            abs(float(sample) - float(current_value)),
            samples,
        )
    profile[value_field] = ewma(current_value, float(sample), samples)
    profile[samples_field] = samples + 1


def _victron_ess_balance_update_service_sample(
    svc: Any,
    sample: float,
    *,
    samples_attr: str,
    value_attr: str,
    optional_float: Callable[[Any], float | None],
    ewma: Callable[[float | None, float, int], float],
) -> None:
    """Update one service-level telemetry sample value and its counter."""
    samples = max(0, int(getattr(svc, samples_attr, 0) or 0))
    current_value = optional_float(getattr(svc, value_attr, None))
    setattr(svc, value_attr, ewma(current_value, float(sample), samples))
    setattr(svc, samples_attr, samples + 1)


def _victron_ess_balance_profile_scalar_snapshot(
    profile: dict[str, Any],
    scalar_fields: tuple[str, ...],
) -> dict[str, str]:
    return {
        field: str(profile.get(field, "") or "")
        for field in scalar_fields[1:]
    }


def _victron_ess_balance_prefixed_scalar_metrics(
    snapshot: dict[str, Any],
    scalar_fields: tuple[str, ...],
) -> dict[str, str]:
    return {
        f"battery_discharge_balance_victron_bias_learning_profile_{field}": str(snapshot.get(field, "") or "")
        for field in scalar_fields
    }


def _victron_ess_balance_active_profile_fields() -> tuple[tuple[str, str], ...]:
    return (
        ("_victron_ess_balance_active_learning_profile_key", "key"),
        ("_victron_ess_balance_active_learning_profile_action_direction", "action_direction"),
        ("_victron_ess_balance_active_learning_profile_site_regime", "site_regime"),
        ("_victron_ess_balance_active_learning_profile_direction", "direction"),
        ("_victron_ess_balance_active_learning_profile_day_phase", "day_phase"),
        ("_victron_ess_balance_active_learning_profile_reserve_phase", "reserve_phase"),
        ("_victron_ess_balance_active_learning_profile_ev_phase", "ev_phase"),
        ("_victron_ess_balance_active_learning_profile_pv_phase", "pv_phase"),
        ("_victron_ess_balance_active_learning_profile_battery_limit_phase", "battery_limit_phase"),
    )


def _reset_victron_ess_balance_pid_state(svc: Any) -> None:
    """Reset Victron ESS balance PID state."""
    svc._victron_ess_balance_pid_last_error_w = 0.0
    svc._victron_ess_balance_pid_last_at = None
    svc._victron_ess_balance_pid_integral_output_w = 0.0
    svc._victron_ess_balance_pid_last_output_w = 0.0


def _reset_victron_ess_balance_pid_integral_state(svc: Any, aggressive: bool = False) -> None:
    """Reset Victron ESS balance PID integral state."""
    svc._victron_ess_balance_pid_integral_output_w = 0.0
    if aggressive:
        svc._victron_ess_balance_pid_last_error_w = 0.0
        svc._victron_ess_balance_pid_last_output_w = 0.0


def _record_victron_ess_balance_tracking_command(
    svc: Any,
    now: float,
    setpoint_w: float,
    source_error_w: float,
    profile_key: str,
) -> None:
    """Record one Victron ESS balance tracking command."""
    svc._victron_ess_balance_telemetry_last_command_at = float(now)
    svc._victron_ess_balance_telemetry_last_command_setpoint_w = float(setpoint_w)
    svc._victron_ess_balance_telemetry_last_command_error_w = float(source_error_w)
    svc._victron_ess_balance_telemetry_last_command_profile_key = str(profile_key or "").strip()
    svc._victron_ess_balance_telemetry_command_response_recorded = False
    svc._victron_ess_balance_telemetry_command_overshoot_recorded = False
    svc._victron_ess_balance_telemetry_command_settled_recorded = False
    svc._victron_ess_balance_telemetry_overshoot_active = False
    svc._victron_ess_balance_telemetry_settling_active = True


def _clear_victron_ess_balance_tracking_episode_state(svc: Any) -> None:
    """Clear one Victron ESS balance telemetry tracking episode."""
    svc._victron_ess_balance_telemetry_last_command_at = None
    svc._victron_ess_balance_telemetry_last_command_setpoint_w = None
    svc._victron_ess_balance_telemetry_last_command_error_w = None
    svc._victron_ess_balance_telemetry_last_command_profile_key = ""
    svc._victron_ess_balance_telemetry_command_response_recorded = False
    svc._victron_ess_balance_telemetry_command_overshoot_recorded = False
    svc._victron_ess_balance_telemetry_command_settled_recorded = False
    svc._victron_ess_balance_telemetry_overshoot_active = False
    svc._victron_ess_balance_telemetry_settling_active = False


def _clear_victron_ess_balance_active_profile_state(svc: Any) -> None:
    """Clear the active Victron ESS balance learning-profile identity."""
    for attr_name, _field_name in _victron_ess_balance_active_profile_fields():
        setattr(svc, attr_name, "")


def _victron_ess_balance_energy_ids(svc: Any) -> list[str]:
    energy_ids: list[str] = []
    for definition in tuple(getattr(svc, "auto_energy_sources", ()) or ()):
        normalized_id = str(getattr(definition, "source_id", "") or "").strip()
        if normalized_id:
            energy_ids.append(normalized_id)
    return energy_ids


def _victron_ess_balance_adaptive_scalar_specs() -> tuple[tuple[str, str, str], ...]:
    return (
        ("auto_apply_generation", "_victron_ess_balance_auto_apply_generation", "int"),
        ("auto_apply_observe_until", "_victron_ess_balance_auto_apply_observe_until", "optional_float"),
        ("auto_apply_last_applied_param", "_victron_ess_balance_auto_apply_last_applied_param", "str"),
        ("auto_apply_last_applied_at", "_victron_ess_balance_auto_apply_last_applied_at", "optional_float"),
        ("oscillation_lockout_until", "_victron_ess_balance_oscillation_lockout_until", "optional_float"),
        ("oscillation_lockout_reason", "_victron_ess_balance_oscillation_lockout_reason", "str"),
        ("last_stable_at", "_victron_ess_balance_last_stable_at", "optional_float"),
        ("last_stable_profile_key", "_victron_ess_balance_last_stable_profile_key", "str"),
        ("auto_apply_suspend_until", "_victron_ess_balance_auto_apply_suspend_until", "optional_float"),
        ("auto_apply_suspend_reason", "_victron_ess_balance_auto_apply_suspend_reason", "str"),
        ("overshoot_cooldown_until", "_victron_ess_balance_overshoot_cooldown_until", "optional_float"),
        ("overshoot_cooldown_reason", "_victron_ess_balance_overshoot_cooldown_reason", "str"),
        ("safe_state_active", "_victron_ess_balance_safe_state_active", "bool"),
        ("safe_state_reason", "_victron_ess_balance_safe_state_reason", "str"),
    )


def _victron_ess_balance_float_attr(svc: Any, attr_name: str) -> float:
    return float(getattr(svc, attr_name, 0.0) or 0.0)
