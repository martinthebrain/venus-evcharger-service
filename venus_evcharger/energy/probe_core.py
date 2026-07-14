# SPDX-License-Identifier: GPL-3.0-or-later
"""Core helper functions for Modbus energy probing."""

from __future__ import annotations

import configparser
from dataclasses import replace
from itertools import product
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings

_FIELD_PROBE_SECTIONS = (
    "SocRead",
    "BatteryPowerRead",
    "ChargeLimitPowerRead",
    "DischargeLimitPowerRead",
    "AcPowerRead",
    "PvInputPowerRead",
    "GridInteractionRead",
    "OperatingModeRead",
    "UsableCapacityRead",
)

_HUAWEI_METER_FIELDS: tuple[dict[str, object], ...] = (
    {"section": "MeterStatusRead", "register_type": "holding", "address": 37100, "data_type": "uint16", "word_order": "big", "scale": 1.0},
    {"section": "HuaweiMeterActivePowerRead", "register_type": "holding", "address": 37113, "data_type": "int32", "word_order": "big", "scale": -1.0},
    {"section": "HuaweiMeterPositiveActiveEnergyRead", "register_type": "holding", "address": 37119, "data_type": "int32", "word_order": "big", "scale": 1.0},
    {"section": "HuaweiMeterReverseActiveEnergyRead", "register_type": "holding", "address": 37121, "data_type": "int32", "word_order": "big", "scale": 1.0},
    {"section": "MeterTypeRead", "register_type": "holding", "address": 37125, "data_type": "uint16", "word_order": "big", "scale": 1.0},
)
_TRANSPORT_SECTION = "Transport"
_ADDRESS_OPTION = "Address"
_REGISTER_TYPE_OPTION = "RegisterType"
_DATA_TYPE_OPTION = "DataType"
_WORD_ORDER_OPTION = "WordOrder"
_SCALE_OPTION = "Scale"
_DEFAULT_REGISTER_TYPE = "holding"
_DEFAULT_DATA_TYPE = "uint16"
_DEFAULT_WORD_ORDER = "big"
_DEFAULT_SCALE = "1"


def _probe_service() -> Any:
    return SimpleNamespace(shelly_request_timeout_seconds=2.0)


def _config_transport_section(parser: configparser.ConfigParser) -> configparser.SectionProxy:
    if parser.has_section(_TRANSPORT_SECTION):
        return parser[_TRANSPORT_SECTION]
    return parser["DEFAULT"]


def _probe_field(
    parser: configparser.ConfigParser,
    field_settings: Callable[[configparser.ConfigParser, str], dict[str, object] | None],
) -> dict[str, object]:
    for section_name in _FIELD_PROBE_SECTIONS:
        field = field_settings(parser, section_name)
        if field is not None:
            return field
    raise ValueError("Energy probe requires at least one Modbus read section")


def _field_settings(parser: configparser.ConfigParser, section_name: str) -> dict[str, object] | None:
    if not parser.has_section(section_name):
        return None
    section = parser[section_name]
    address = _probe_int_value(section.get(_ADDRESS_OPTION))
    if address is None:
        return None
    register_type = _normalized_probe_text(section.get(_REGISTER_TYPE_OPTION), _DEFAULT_REGISTER_TYPE)
    data_type = _normalized_probe_text(section.get(_DATA_TYPE_OPTION), _DEFAULT_DATA_TYPE)
    word_order = _normalized_probe_text(section.get(_WORD_ORDER_OPTION), _DEFAULT_WORD_ORDER)
    return {
        "section": section_name,
        "register_type": register_type,
        "address": address,
        "data_type": data_type,
        "word_order": word_order,
        "scale": float(_section_text(section, _SCALE_OPTION, _DEFAULT_SCALE)),
    }


def _section_text(section: Mapping[str, object], option_name: str, fallback: str = "") -> str:
    value = section.get(option_name)
    if value is None:
        return fallback
    return str(value).strip() or fallback


def _validate_fields(
    transport_settings: ModbusTransportSettings,
    parser: configparser.ConfigParser,
    profile_name: str,
    field_settings: Callable[[configparser.ConfigParser, str], dict[str, object] | None],
    attempt_probe: Callable[[ModbusTransportSettings, dict[str, object]], dict[str, object]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for section_name in _FIELD_PROBE_SECTIONS:
        field = field_settings(parser, section_name)
        if field is None:
            continue
        attempt = dict(attempt_probe(transport_settings, field))
        attempt["section"] = section_name
        attempt["required"] = True
        results.append(attempt)
    if profile_name.startswith("huawei_"):
        for field in _HUAWEI_METER_FIELDS:
            attempt = dict(attempt_probe(transport_settings, field))
            attempt["section"] = field["section"]
            attempt["required"] = False
            results.append(attempt)
    return results


def _probe_candidates(
    base_transport: ModbusTransportSettings,
    probe_plan: Mapping[str, Any],
) -> tuple[ModbusTransportSettings, ...]:
    host_candidates = _probe_host_candidates(base_transport, probe_plan)
    port_candidates = _probe_default_ports(
        base_transport,
        _int_candidates(probe_plan.get("port_candidates")),
    )
    unit_id_candidates = _probe_default_unit_ids(
        base_transport,
        _int_candidates(probe_plan.get("unit_id_candidates")),
    )
    if base_transport.transport_kind == "serial_rtu":
        return tuple(replace(base_transport, unit_id=unit_id) for unit_id in unit_id_candidates)
    _validate_probe_host_candidates(host_candidates)
    return tuple(
        replace(base_transport, host=host, port=candidate_port, unit_id=candidate_unit_id)
        for host, candidate_port, candidate_unit_id in product(
            host_candidates,
            port_candidates,
            unit_id_candidates,
        )
    )


def _text_candidates(value: object) -> list[str]:
    raw_values = value if isinstance(value, (list, tuple)) else [value]
    return [text for item in raw_values if (text := _optional_probe_text(item))]


def _int_candidates(value: object) -> list[int]:
    return [candidate for candidate in _probe_int_values(value) if candidate is not None]


def _probe_host_candidates(
    base_transport: ModbusTransportSettings,
    probe_plan: Mapping[str, Any],
) -> list[str]:
    host_candidates = _text_candidates(probe_plan.get("host"))
    if host_candidates:
        return host_candidates
    return [base_transport.host] if base_transport.host else []


def _probe_default_ports(
    base_transport: ModbusTransportSettings,
    port_candidates: list[int],
) -> list[int]:
    return port_candidates or ([base_transport.port] if base_transport.port is not None else [])


def _probe_default_unit_ids(
    base_transport: ModbusTransportSettings,
    unit_id_candidates: list[int],
) -> list[int]:
    return unit_id_candidates or [base_transport.unit_id]


def _validate_probe_host_candidates(host_candidates: list[str]) -> None:
    if host_candidates:
        return
    raise ValueError("Energy probe requires a host candidate for TCP/UDP Modbus detection")


def _probe_int_values(value: object) -> list[int | None]:
    raw_values = value if isinstance(value, (list, tuple)) else [value]
    return [_probe_int_value(item) for item in raw_values]


def _probe_int_value(value: object) -> int | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _optional_probe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_probe_text(value: object, fallback: str) -> str:
    text = _optional_probe_text(value)
    return text.lower() if text else fallback
