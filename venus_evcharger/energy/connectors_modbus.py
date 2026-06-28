# SPDX-License-Identifier: GPL-3.0-or-later
"""Modbus connector helpers for external energy sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport_config import load_modbus_transport_settings
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.backend.template_support import load_template_config

from .connectors_common import _cache_map
from .models import EnergySourceDefinition, EnergySourceSnapshot


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


def _modbus_client_cache(runtime: Any) -> dict[str, ModbusClient]:
    return cast(dict[str, ModbusClient], _cache_map(runtime, "_energy_modbus_client_cache"))


def _modbus_source_name(source: EnergySourceDefinition, transport_settings: ModbusTransportSettings) -> str:
    if source.service_name:
        return source.service_name
    if transport_settings.host:
        return transport_settings.host
    if transport_settings.device:
        return transport_settings.device
    return source.config_path or source.source_id


def _modbus_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> ModbusEnergySourceSettings:
    cache = cast(dict[str, ModbusEnergySourceSettings], _cache_map(runtime, "_energy_modbus_settings_cache"))
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, ModbusEnergySourceSettings):
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
    cache[cache_key] = settings
    return settings


def _modbus_field_settings(parser: Any, section_name: str) -> ModbusEnergyFieldSettings | None:
    if not parser.has_section(section_name):
        return None
    section = parser[section_name]
    address_text = _modbus_field_address_text(section)
    if not address_text:
        return None
    return ModbusEnergyFieldSettings(
        register_type=_modbus_field_option(section, "RegisterType", "holding"),
        address=int(address_text),
        data_type=_modbus_field_option(section, "DataType", "uint16"),
        scale=_modbus_field_scale(section),
        word_order=_modbus_field_option(section, "WordOrder", "big"),
    )


def _modbus_field_option(section: Any, option_name: str, fallback: str) -> str:
    return str(section.get(option_name, fallback)).strip().lower() or fallback


def _modbus_field_address_text(section: Any) -> str:
    return str(section.get("Address", "")).strip()


def _modbus_field_scale(section: Any) -> float:
    return float(str(section.get("Scale", "1")).strip() or "1")


def _modbus_text_map(parser: Any, section_name: str) -> dict[str, str]:
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


def _modbus_aggregation_setting(parser: Any, option_name: str) -> str:
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
    numeric_value = 1.0 if isinstance(raw_value, bool) and raw_value else 0.0 if isinstance(raw_value, bool) else float(raw_value)
    return numeric_value * float(field.scale)


def _build_modbus_energy_source_snapshot(
    source: EnergySourceDefinition,
    now: float,
    settings: ModbusEnergySourceSettings,
    client: ModbusClient,
    field_value: Callable[[ModbusClient, ModbusEnergyFieldSettings | None], float | None],
    field_text: Callable[[ModbusClient, ModbusEnergyFieldSettings | None, dict[str, str] | None], str],
) -> EnergySourceSnapshot:
    soc_value = field_value(client, settings.soc_field)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        soc_value = None
    usable_capacity_wh = field_value(client, settings.usable_capacity_field)
    if usable_capacity_wh is None:
        usable_capacity_wh = source.usable_capacity_wh
    elif usable_capacity_wh <= 0.0:
        usable_capacity_wh = None
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_modbus_source_name(source, settings.transport_settings),
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=field_value(client, settings.battery_power_field),
        charge_limit_power_w=field_value(client, settings.charge_limit_power_field),
        discharge_limit_power_w=field_value(client, settings.discharge_limit_power_field),
        ac_power_w=field_value(client, settings.ac_power_field),
        pv_input_power_w=field_value(client, settings.pv_input_power_field),
        grid_interaction_w=field_value(client, settings.grid_interaction_field),
        ac_power_scope_key=_render_scope_key(source, settings.transport_settings, settings.ac_power_scope_key),
        pv_input_power_scope_key=_render_scope_key(source, settings.transport_settings, settings.pv_input_power_scope_key),
        grid_interaction_scope_key=_render_scope_key(
            source,
            settings.transport_settings,
            settings.grid_interaction_scope_key,
        ),
        operating_mode=field_text(client, settings.operating_mode_field, settings.operating_mode_map),
        online=True,
        confidence=1.0,
        captured_at=now,
    )


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
