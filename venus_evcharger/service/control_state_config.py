# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration-effective state payload helpers for the Control API role."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, SupportsFloat, SupportsIndex, SupportsInt

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.energy import energy_source_profile_details
from venus_evcharger.service.control_state_victron import _ControlApiStateVictron

_ConfigConverter = Callable[[object], object]


def _identity(value: object) -> object:
    return value


@dataclass(frozen=True)
class _ConfigField:
    name: str
    default: object
    converter: _ConfigConverter = _identity
    attr: str | None = None


def _as_bool(value: object) -> bool:
    return bool(value)


def _as_int(value: object) -> int:
    if isinstance(value, (str, bytes, bytearray)):
        return int(value)
    if isinstance(value, (SupportsInt, SupportsIndex)):
        return int(value)
    raise TypeError(f"Expected an integer-compatible value, got {type(value).__name__}")


def _as_float(value: object) -> float:
    if isinstance(value, (str, bytes, bytearray)):
        return float(value)
    if isinstance(value, (SupportsFloat, SupportsIndex)):
        return float(value)
    raise TypeError(f"Expected a float-compatible value, got {type(value).__name__}")


def _as_str(value: object) -> str:
    return str(value)


def _token_configured(value: object) -> bool:
    return bool(str(value).strip())


_BASE_FIELDS = (
    _ConfigField("deviceinstance", 0),
    _ConfigField("host", ""),
    _ConfigField("phase", "L1"),
    _ConfigField("service_name", ""),
    _ConfigField("connection_name", ""),
    _ConfigField("runtime_state_path", ""),
    _ConfigField("runtime_overrides_path", ""),
    _ConfigField("max_current", 0.0),
    _ConfigField("min_current", 0.0),
    _ConfigField("auto_daytime_only", False, _as_bool),
    _ConfigField("auto_scheduled_enabled_days", ""),
    _ConfigField("auto_scheduled_latest_end_time", ""),
    _ConfigField("auto_scheduled_night_current_amps", 0.0),
)

_CONTROL_API_FIELDS = (
    _ConfigField("control_api_enabled", False, _as_bool),
    _ConfigField("control_api_host", "127.0.0.1"),
    _ConfigField("control_api_port", 0, _as_int),
    _ConfigField("control_api_localhost_only", True, _as_bool),
    _ConfigField("control_api_unix_socket_path", ""),
    _ConfigField("control_api_audit_path", ""),
    _ConfigField("control_api_idempotency_path", ""),
    _ConfigField("control_api_rate_limit_max_requests", 0, _as_int),
    _ConfigField("control_api_rate_limit_window_seconds", 0.0, _as_float),
    _ConfigField("control_api_critical_cooldown_seconds", 0.0, _as_float),
    _ConfigField("control_api_read_token_configured", "", _token_configured, attr="control_api_read_token"),
    _ConfigField("control_api_control_token_configured", "", _token_configured, attr="control_api_control_token"),
    _ConfigField("control_api_admin_token_configured", "", _token_configured, attr="control_api_admin_token"),
    _ConfigField("control_api_update_token_configured", "", _token_configured, attr="control_api_update_token"),
)

_COMPANION_FIELDS = (
    _ConfigField("companion_dbus_bridge_enabled", False, _as_bool),
    _ConfigField("companion_battery_service_enabled", False, _as_bool),
    _ConfigField("companion_pvinverter_service_enabled", False, _as_bool),
    _ConfigField("companion_grid_service_enabled", False, _as_bool),
    _ConfigField("companion_grid_authoritative_source", "", _as_str),
    _ConfigField("companion_grid_hold_seconds", 0.0, _as_float),
    _ConfigField("companion_grid_smoothing_alpha", 1.0, _as_float),
    _ConfigField("companion_grid_smoothing_max_jump_watts", 0.0, _as_float),
    _ConfigField("companion_source_services_enabled", False, _as_bool),
    _ConfigField("companion_source_grid_services_enabled", False, _as_bool),
    _ConfigField("companion_source_grid_hold_seconds", 0.0, _as_float),
    _ConfigField("companion_source_grid_smoothing_alpha", 1.0, _as_float),
    _ConfigField("companion_source_grid_smoothing_max_jump_watts", 0.0, _as_float),
    _ConfigField("companion_battery_deviceinstance", 0, _as_int),
    _ConfigField("companion_pvinverter_deviceinstance", 0, _as_int),
    _ConfigField("companion_grid_deviceinstance", 0, _as_int),
    _ConfigField("companion_source_battery_deviceinstance_base", 0, _as_int),
    _ConfigField("companion_source_pvinverter_deviceinstance_base", 0, _as_int),
    _ConfigField("companion_source_grid_deviceinstance_base", 0, _as_int),
    _ConfigField("companion_battery_service_name", ""),
    _ConfigField("companion_pvinverter_service_name", ""),
    _ConfigField("companion_grid_service_name", ""),
    _ConfigField("companion_source_battery_service_prefix", ""),
    _ConfigField("companion_source_pvinverter_service_prefix", ""),
    _ConfigField("companion_source_grid_service_prefix", ""),
)

_BALANCE_POLICY_FIELDS = (
    _ConfigField("auto_battery_discharge_balance_policy_enabled", False, _as_bool),
    _ConfigField("auto_battery_discharge_balance_warn_error_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_bias_start_error_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_bias_max_penalty_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_bias_mode", "always", _as_str),
    _ConfigField("auto_battery_discharge_balance_bias_reserve_margin_soc", 0.0, _as_float),
)

_BALANCE_COORDINATION_FIELDS = (
    _ConfigField("auto_battery_discharge_balance_coordination_enabled", False, _as_bool),
    _ConfigField("auto_battery_discharge_balance_coordination_support_mode", "supported_only", _as_str),
    _ConfigField("auto_battery_discharge_balance_coordination_start_error_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_coordination_max_penalty_watts", 0.0, _as_float),
)

_VICTRON_BIAS_FIELDS = (
    _ConfigField("auto_battery_discharge_balance_victron_bias_enabled", False, _as_bool),
    _ConfigField("auto_battery_discharge_balance_victron_bias_source_id", "", _as_str),
    _ConfigField("auto_battery_discharge_balance_victron_bias_service", "", _as_str),
    _ConfigField("auto_battery_discharge_balance_victron_bias_path", "", _as_str),
    _ConfigField("auto_battery_discharge_balance_victron_bias_base_setpoint_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_activation_mode", "always", _as_str),
    _ConfigField("auto_battery_discharge_balance_victron_bias_support_mode", "allow_experimental", _as_str),
    _ConfigField("auto_battery_discharge_balance_victron_bias_kp", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_ki", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_kd", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_integral_limit_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_min_update_seconds", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False, _as_bool),
    _ConfigField("auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples", 0, _as_int),
    _ConfigField("auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_auto_apply_blend", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_observation_window_seconds", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", False, _as_bool),
    _ConfigField("auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes", 0, _as_int),
    _ConfigField("auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_rollback_enabled", False, _as_bool),
    _ConfigField("auto_battery_discharge_balance_victron_bias_rollback_min_stability_score", 0.0, _as_float),
    _ConfigField("auto_battery_discharge_balance_victron_bias_require_clean_phases", False, _as_bool),
)


def _owner_value(owner: object, field: _ConfigField) -> object:
    return getattr(owner, field.attr or field.name, field.default)


def _field_values(owner: object, fields: tuple[_ConfigField, ...]) -> dict[str, Any]:
    return {field.name: field.converter(_owner_value(owner, field)) for field in fields}


def _source_id(source: object) -> str:
    return str(getattr(source, "source_id", ""))


def _profile_name(source: object) -> str:
    return str(getattr(source, "profile_name", ""))


def _has_source_id(source: object) -> bool:
    return bool(_source_id(source).strip())


def _energy_sources(owner: object) -> tuple[object, ...]:
    if not hasattr(owner, "auto_energy_sources"):
        return ()
    sources = getattr(owner, "auto_energy_sources")
    if sources is None or isinstance(sources, str) or not isinstance(sources, Iterable):
        return ()
    return tuple(sources)


def _config_effective_energy_source_ids(sources: tuple[object, ...]) -> list[str]:
    return [_source_id(source) for source in sources]


def _config_effective_energy_source_profiles(sources: tuple[object, ...]) -> dict[str, str]:
    return {_source_id(source): _profile_name(source) for source in sources if _has_source_id(source)}


def _config_effective_energy_source_profile_details(sources: tuple[object, ...]) -> dict[str, dict[str, Any]]:
    return {
        _source_id(source): dict(energy_source_profile_details(_profile_name(source)))
        for source in sources
        if _has_source_id(source)
    }


def _config_effective_state_base(owner: object) -> dict[str, Any]:
    state = _field_values(owner, _BASE_FIELDS)
    state.update(
        {
            "backend_mode": backend_mode_for_service(owner, "combined"),
            "meter_backend": backend_type_for_service(owner, "meter", "na"),
            "switch_backend": backend_type_for_service(owner, "switch", "na"),
            "charger_backend": backend_type_for_service(owner, "charger", "na"),
        }
    )
    return state


def _config_effective_control_api(owner: object) -> dict[str, Any]:
    return _field_values(owner, _CONTROL_API_FIELDS)


def _config_effective_companion(owner: object) -> dict[str, Any]:
    return _field_values(owner, _COMPANION_FIELDS)


def _config_effective_balance(owner: object) -> dict[str, Any]:
    state = _field_values(owner, _BALANCE_POLICY_FIELDS)
    state.update(_field_values(owner, _BALANCE_COORDINATION_FIELDS))
    state.update(_field_values(owner, _VICTRON_BIAS_FIELDS))
    return state


def _config_effective_energy_sources(owner: object) -> dict[str, Any]:
    sources = _energy_sources(owner)
    return {
        "auto_use_combined_battery_soc": bool(getattr(owner, "auto_use_combined_battery_soc", True)),
        "auto_energy_source_ids": _config_effective_energy_source_ids(sources),
        "auto_energy_source_profiles": _config_effective_energy_source_profiles(sources),
        "auto_energy_source_profile_details": _config_effective_energy_source_profile_details(sources),
        "auto_energy_source_count": len(sources),
    }


def _state_api_config_effective_state(owner: object) -> dict[str, Any]:
    state: dict[str, Any] = {}
    state.update(_config_effective_state_base(owner))
    state.update(_config_effective_control_api(owner))
    state.update(_config_effective_companion(owner))
    state.update(_config_effective_balance(owner))
    state.update(_config_effective_energy_sources(owner))
    return state


class _ControlApiStateConfig(_ControlApiStateVictron):
    def _state_api_config_effective_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "config-effective",
            "state": _state_api_config_effective_state(self),
        }
