# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-json connector for external energy sources."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.backend.template_support import config_section, load_template_config
from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import (
    _bounded_request_timeout_seconds,
    _optional_bool_path,
    _optional_confidence_path,
    _optional_float_path,
    _optional_path,
    _optional_text_path,
    _runtime_cache_get,
    _runtime_cache_put,
    _runtime_default_timeout_seconds,
)
from .models import EnergySourceDefinition, EnergySourceSnapshot

_DEFAULT_TIMEOUT_SECONDS = 2.0
_ADAPTER_SECTION = "Adapter"
_COMMAND_SECTION = "Command"
_RESPONSE_SECTION = "Response"
_COMMAND_ARGS_KEY = "Args"
_COMMAND_TIMEOUT_KEY = "TimeoutSeconds"
_REQUEST_TIMEOUT_KEY = "RequestTimeoutSeconds"
_SETTINGS_CACHE = "command_json.settings"
_RESPONSE_PATH_KEYS = (
    "SocPath",
    "UsableCapacityWhPath",
    "BatteryPowerPath",
    "AcPowerPath",
    "PvInputPowerPath",
    "GridInteractionPath",
    "OperatingModePath",
    "OnlinePath",
    "ConfidencePath",
)


@dataclass(frozen=True)
class CommandJsonEnergySourceSettings:
    """Normalized config for one local helper command returning JSON."""

    command: tuple[str, ...]
    timeout_seconds: float
    soc_path: str | None
    usable_capacity_wh_path: str | None
    battery_power_path: str | None
    ac_power_path: str | None
    pv_input_power_path: str | None
    grid_interaction_path: str | None
    operating_mode_path: str | None
    online_path: str | None
    confidence_path: str | None


def _build_command_json_energy_source_snapshot(
    source: EnergySourceDefinition,
    now: float,
    settings: CommandJsonEnergySourceSettings,
    payload: dict[str, object],
) -> EnergySourceSnapshot:
    soc_value = _command_soc_value(payload, settings)
    usable_capacity_wh = _command_usable_capacity_wh(payload, settings, source)
    online = _command_online(payload, settings)
    confidence = _command_confidence(payload, settings)
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_command_source_name(source, settings),
        physical_id=source.physical_id,
        physical_priority=source.physical_priority,
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_optional_float_path(payload, settings.battery_power_path),
        ac_power_w=_optional_float_path(payload, settings.ac_power_path),
        pv_input_power_w=_optional_float_path(payload, settings.pv_input_power_path),
        grid_interaction_w=_optional_float_path(payload, settings.grid_interaction_path),
        operating_mode=_optional_text_path(payload, settings.operating_mode_path) or "",
        online=online,
        confidence=confidence,
        captured_at=now,
    )


def _command_source_name(source: EnergySourceDefinition, settings: CommandJsonEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    return settings.command[0] if settings.command else (source.config_path or source.source_id)


def _command_soc_value(
    payload: dict[str, object],
    settings: CommandJsonEnergySourceSettings,
) -> float | None:
    soc_value = _optional_float_path(payload, settings.soc_path)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        return None
    return soc_value


def _command_usable_capacity_wh(
    payload: dict[str, object],
    settings: CommandJsonEnergySourceSettings,
    source: EnergySourceDefinition,
) -> float | None:
    usable_capacity_wh = _optional_float_path(payload, settings.usable_capacity_wh_path)
    if usable_capacity_wh is None:
        return source.usable_capacity_wh
    if usable_capacity_wh <= 0.0:
        return None
    return usable_capacity_wh


def _command_online(
    payload: dict[str, object],
    settings: CommandJsonEnergySourceSettings,
) -> bool:
    online = _optional_bool_path(payload, settings.online_path)
    return True if online is None else bool(online)


def _command_confidence(
    payload: dict[str, object],
    settings: CommandJsonEnergySourceSettings,
) -> float:
    confidence = _optional_confidence_path(payload, settings.confidence_path)
    return 1.0 if confidence is None else confidence


def _command_timeout_seconds(
    runtime: object,
    adapter: Mapping[str, object],
    command: Mapping[str, object],
) -> float:
    default_timeout = _runtime_default_timeout_seconds(runtime, _DEFAULT_TIMEOUT_SECONDS)
    timeout_seconds = finite_float_or_none(command.get(_COMMAND_TIMEOUT_KEY))
    if timeout_seconds is None:
        timeout_seconds = finite_float_or_none(adapter.get(_REQUEST_TIMEOUT_KEY))
    configured = default_timeout if timeout_seconds is None or timeout_seconds <= 0.0 else float(timeout_seconds)
    return _bounded_request_timeout_seconds(runtime, configured)


def _command_json_energy_source_settings(
    runtime: object,
    source: EnergySourceDefinition,
) -> CommandJsonEnergySourceSettings:
    cache_key = str(source.config_path).strip()
    cached = _runtime_cache_get(
        runtime,
        _SETTINGS_CACHE,
        cache_key,
        CommandJsonEnergySourceSettings,
    )
    if cached is not None:
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for command_json connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, _ADAPTER_SECTION)
    command = config_section(parser, _COMMAND_SECTION)
    response = config_section(parser, _RESPONSE_SECTION)
    response_paths = tuple(_optional_path(response.get(key)) for key in _RESPONSE_PATH_KEYS)
    settings = CommandJsonEnergySourceSettings(
        command=_command_args(command),
        timeout_seconds=_command_timeout_seconds(runtime, adapter, command),
        soc_path=response_paths[0],
        usable_capacity_wh_path=response_paths[1],
        battery_power_path=response_paths[2],
        ac_power_path=response_paths[3],
        pv_input_power_path=response_paths[4],
        grid_interaction_path=response_paths[5],
        operating_mode_path=response_paths[6],
        online_path=response_paths[7],
        confidence_path=response_paths[8],
    )
    _validate_command_json_energy_source_settings(source, settings)
    _runtime_cache_put(runtime, _SETTINGS_CACHE, cache_key, settings)
    return settings


def _command_args(command: Mapping[str, object]) -> tuple[str, ...]:
    args_text = str(command.get(_COMMAND_ARGS_KEY, "")).strip()
    if not args_text:
        return ()
    return tuple(shlex.split(args_text))


def _validate_command_json_energy_source_settings(
    source: EnergySourceDefinition,
    settings: CommandJsonEnergySourceSettings,
) -> None:
    if not settings.command:
        raise ValueError(f"Energy source '{source.source_id}' requires [Command] Args")
    if _command_has_readable_response(settings, source):
        return
    raise ValueError(f"Energy source '{source.source_id}' requires at least one Response path or UsableCapacityWh")


def _command_has_readable_response(
    settings: CommandJsonEnergySourceSettings,
    source: EnergySourceDefinition,
) -> bool:
    readable_paths = (
        settings.soc_path,
        settings.usable_capacity_wh_path,
        settings.battery_power_path,
        settings.ac_power_path,
        settings.pv_input_power_path,
        settings.grid_interaction_path,
    )
    if any(path is not None for path in readable_paths):
        return True
    return source.usable_capacity_wh is not None
