# SPDX-License-Identifier: GPL-3.0-or-later
"""Modbus connector helpers for external energy sources."""

from __future__ import annotations

import configparser
import math
from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport_config import load_modbus_transport_settings
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.backend.template_support import load_template_config

from .connectors_common import _runtime_cache_get, _runtime_cache_put
from .models import EnergySourceDefinition, EnergySourceSnapshot

_REGISTER_TYPE_OPTION = "RegisterType"
_ADDRESS_OPTION = "Address"
_DATA_TYPE_OPTION = "DataType"
_SCALE_OPTION = "Scale"
_WORD_ORDER_OPTION = "WordOrder"
_DEFAULT_REGISTER_TYPE = "holding"
_DEFAULT_DATA_TYPE = "uint16"
_DEFAULT_SCALE = "1"
_DEFAULT_WORD_ORDER = "big"
_CLIENT_CACHE = "modbus.clients"
_SETTINGS_CACHE = "modbus.settings"


@dataclass(frozen=True)
class ModbusEnergyFieldSettings:
    """One optional Modbus read mapping used by the energy connector."""

    register_type: str
    address: int
    data_type: str
    scale: float
    word_order: str


@dataclass(frozen=True)
class ModbusEnergySourceSettings:
    """Normalized config for one Modbus-backed external energy source."""

    transport_settings: ModbusTransportSettings
    soc_field: ModbusEnergyFieldSettings | None
    usable_capacity_field: ModbusEnergyFieldSettings | None
    battery_power_field: ModbusEnergyFieldSettings | None
    charge_limit_power_field: ModbusEnergyFieldSettings | None
    discharge_limit_power_field: ModbusEnergyFieldSettings | None
    ac_power_field: ModbusEnergyFieldSettings | None
    pv_input_power_field: ModbusEnergyFieldSettings | None
    grid_interaction_field: ModbusEnergyFieldSettings | None
    operating_mode_field: ModbusEnergyFieldSettings | None
    operating_mode_map: dict[str, str]
    ac_power_scope_key: str
    pv_input_power_scope_key: str
    grid_interaction_scope_key: str


@dataclass(slots=True)
class ModbusReadProgress:
    """Values accumulated across single-register connector steps."""

    next_field_index: int
    values: dict[str, float | str | None]


def _cached_modbus_client(runtime: object, config_path: str) -> ModbusClient | None:
    return _runtime_cache_get(runtime, _CLIENT_CACHE, config_path, ModbusClient)


def _store_modbus_client(
    runtime: object,
    config_path: str,
    client: ModbusClient,
) -> None:
    _runtime_cache_put(runtime, _CLIENT_CACHE, config_path, client)


def _modbus_source_name(source: EnergySourceDefinition, transport_settings: ModbusTransportSettings) -> str:
    if source.service_name:
        return source.service_name
    if transport_settings.host:
        return transport_settings.host
    if transport_settings.device:
        return transport_settings.device
    return source.config_path or source.source_id


def _modbus_energy_source_settings(
    runtime: object,
    source: EnergySourceDefinition,
) -> ModbusEnergySourceSettings:
    cache_key = str(source.config_path).strip()
    cached = _runtime_cache_get(
        runtime,
        _SETTINGS_CACHE,
        cache_key,
        ModbusEnergySourceSettings,
    )
    if cached is not None:
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for modbus connector")
    parser = load_template_config(cache_key)
    settings = ModbusEnergySourceSettings(
        transport_settings=load_modbus_transport_settings(parser, runtime),
        soc_field=_modbus_field_settings(parser, "SocRead"),
        usable_capacity_field=_modbus_field_settings(parser, "UsableCapacityRead"),
        battery_power_field=_modbus_field_settings(parser, "BatteryPowerRead"),
        charge_limit_power_field=_modbus_field_settings(parser, "ChargeLimitPowerRead"),
        discharge_limit_power_field=_modbus_field_settings(parser, "DischargeLimitPowerRead"),
        ac_power_field=_modbus_field_settings(parser, "AcPowerRead"),
        pv_input_power_field=_modbus_field_settings(parser, "PvInputPowerRead"),
        grid_interaction_field=_modbus_field_settings(parser, "GridInteractionRead"),
        operating_mode_field=_modbus_field_settings(parser, "OperatingModeRead"),
        operating_mode_map=_modbus_text_map(parser, "OperatingModeMap"),
        ac_power_scope_key=_modbus_aggregation_setting(parser, "AcPowerScopeKey"),
        pv_input_power_scope_key=_modbus_aggregation_setting(parser, "PvInputPowerScopeKey"),
        grid_interaction_scope_key=_modbus_aggregation_setting(parser, "GridInteractionScopeKey"),
    )
    _validate_modbus_energy_source_settings(source, settings)
    _runtime_cache_put(runtime, _SETTINGS_CACHE, cache_key, settings)
    return settings


def _modbus_field_settings(
    parser: configparser.ConfigParser,
    section_name: str,
) -> ModbusEnergyFieldSettings | None:
    if not parser.has_section(section_name):
        return None
    section = parser[section_name]
    address_text = _modbus_field_address_text(section)
    if not address_text:
        return None
    return ModbusEnergyFieldSettings(
        register_type=_modbus_field_option(section, _REGISTER_TYPE_OPTION, _DEFAULT_REGISTER_TYPE),
        address=int(address_text),
        data_type=_modbus_field_option(section, _DATA_TYPE_OPTION, _DEFAULT_DATA_TYPE),
        scale=_modbus_field_scale(section),
        word_order=_modbus_field_option(section, _WORD_ORDER_OPTION, _DEFAULT_WORD_ORDER),
    )


def _modbus_field_option(
    section: configparser.SectionProxy,
    option_name: str,
    fallback: str,
) -> str:
    return _section_text(section, option_name, fallback).lower()


def _section_text(
    section: configparser.SectionProxy,
    option_name: str,
    fallback: str = "",
) -> str:
    value = section.get(option_name)
    if value is None:
        return fallback
    return str(value).strip() or fallback


def _modbus_field_address_text(section: configparser.SectionProxy) -> str:
    return _section_text(section, _ADDRESS_OPTION)


def _modbus_field_scale(section: configparser.SectionProxy) -> float:
    scale = float(_section_text(section, _SCALE_OPTION, _DEFAULT_SCALE))
    if not math.isfinite(scale):
        raise ValueError("Modbus field scale must be finite")
    return scale


def _modbus_text_map(
    parser: configparser.ConfigParser,
    section_name: str,
) -> dict[str, str]:
    if not parser.has_section(section_name):
        return {}
    section = parser[section_name]
    normalized: dict[str, str] = {}
    for raw_key, raw_value in section.items():
        key = str(raw_key).strip()
        value = str(raw_value).strip()
        if key and value:
            normalized[key] = value
    return normalized


def _modbus_aggregation_setting(
    parser: configparser.ConfigParser,
    option_name: str,
) -> str:
    if not parser.has_section("Aggregation"):
        return ""
    return str(parser["Aggregation"].get(option_name, "")).strip()


def _modbus_has_any_read_field(settings: ModbusEnergySourceSettings) -> bool:
    return any(
        (
            settings.soc_field is not None,
            settings.usable_capacity_field is not None,
            settings.battery_power_field is not None,
            settings.charge_limit_power_field is not None,
            settings.discharge_limit_power_field is not None,
            settings.ac_power_field is not None,
            settings.pv_input_power_field is not None,
            settings.grid_interaction_field is not None,
            settings.operating_mode_field is not None,
        )
    )


def _validate_modbus_energy_source_settings(
    source: EnergySourceDefinition,
    settings: ModbusEnergySourceSettings,
) -> None:
    if not _modbus_has_any_read_field(settings) and source.usable_capacity_wh is None:
        raise ValueError(
            f"Energy source '{source.source_id}' requires at least one Modbus read section or UsableCapacityWh"
        )


def _modbus_field_value(client: ModbusClient, field: ModbusEnergyFieldSettings | None) -> float | None:
    if field is None:
        return None
    raw_value = client.read_scalar(field.register_type, field.address, field.data_type, field.word_order)
    numeric_value = _modbus_numeric_value(raw_value)
    scaled_value = numeric_value * float(field.scale)
    if not math.isfinite(scaled_value):
        raise ValueError("Modbus energy-source field returned a non-finite value")
    return scaled_value


def _modbus_numeric_value(raw_value: float | int | bool) -> float:
    if isinstance(raw_value, bool):
        return float(raw_value)
    return float(raw_value)


def _build_modbus_energy_source_snapshot(
    source: EnergySourceDefinition,
    now: float,
    settings: ModbusEnergySourceSettings,
    values: Mapping[str, float | str | None],
) -> EnergySourceSnapshot:
    soc_value = _numeric_progress_value(values, "soc")
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        soc_value = None
    usable_capacity_wh = _numeric_progress_value(values, "usable_capacity")
    if usable_capacity_wh is None:
        usable_capacity_wh = source.usable_capacity_wh
    elif usable_capacity_wh <= 0.0:
        usable_capacity_wh = None
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_modbus_source_name(source, settings.transport_settings),
        physical_id=source.physical_id,
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_numeric_progress_value(values, "battery_power"),
        charge_limit_power_w=_numeric_progress_value(values, "charge_limit_power"),
        discharge_limit_power_w=_numeric_progress_value(values, "discharge_limit_power"),
        ac_power_w=_numeric_progress_value(values, "ac_power"),
        pv_input_power_w=_numeric_progress_value(values, "pv_input_power"),
        grid_interaction_w=_numeric_progress_value(values, "grid_interaction"),
        ac_power_scope_key=_render_scope_key(source, settings.transport_settings, settings.ac_power_scope_key),
        pv_input_power_scope_key=_render_scope_key(
            source, settings.transport_settings, settings.pv_input_power_scope_key
        ),
        grid_interaction_scope_key=_render_scope_key(
            source,
            settings.transport_settings,
            settings.grid_interaction_scope_key,
        ),
        operating_mode=_text_progress_value(values, "operating_mode"),
        online=True,
        confidence=1.0,
        captured_at=now,
    )


def _modbus_read_fields(
    settings: ModbusEnergySourceSettings,
) -> tuple[tuple[str, ModbusEnergyFieldSettings], ...]:
    configured = (
        ("soc", settings.soc_field),
        ("usable_capacity", settings.usable_capacity_field),
        ("battery_power", settings.battery_power_field),
        ("charge_limit_power", settings.charge_limit_power_field),
        ("discharge_limit_power", settings.discharge_limit_power_field),
        ("ac_power", settings.ac_power_field),
        ("pv_input_power", settings.pv_input_power_field),
        ("grid_interaction", settings.grid_interaction_field),
        ("operating_mode", settings.operating_mode_field),
    )
    return tuple(
        (field_name, field)
        for field_name, field in configured
        if field is not None
    )


def _modbus_progress_value(
    field_name: str,
    value: float,
    settings: ModbusEnergySourceSettings,
) -> float | str:
    if field_name != "operating_mode":
        return value
    normalized = str(int(value)) if value.is_integer() else str(value)
    return settings.operating_mode_map.get(normalized, normalized)


def _numeric_progress_value(
    values: Mapping[str, float | str | None],
    field_name: str,
) -> float | None:
    value = values.get(field_name)
    return float(value) if isinstance(value, (int, float)) else None


def _text_progress_value(
    values: Mapping[str, float | str | None],
    field_name: str,
) -> str:
    value = values.get(field_name)
    return value if isinstance(value, str) else ""


def _render_scope_key(
    source: EnergySourceDefinition,
    transport_settings: ModbusTransportSettings,
    template: str,
) -> str:
    normalized = str(template or "").strip()
    if not normalized:
        return ""
    values = {
        "source_id": source.source_id,
        "host": transport_settings.host,
        "port": transport_settings.port,
        "unit_id": transport_settings.unit_id,
        "device": transport_settings.device,
    }
    try:
        return normalized.format_map(_ScopeKeyFormatter(values))
    except (KeyError, IndexError, ValueError):
        return normalized


class _ScopeKeyFormatter(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
