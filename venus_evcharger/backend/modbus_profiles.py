# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic Modbus EVSE register-profile support.

Profiles describe how a charger exposes state and control registers without
hard-coding one vendor layout into the runtime backend. The parser validates
register types, data types, scales, value maps, phase maps, and capability
flags before a profile reaches the charger adapter. Runtime reads then stay
small: each configured field knows how to fetch and normalize its own value,
while writes encode enable, current, and phase-selection commands.
"""

from __future__ import annotations

import configparser

from venus_evcharger.core.contracts import finite_float_or_none

from .modbus_profile_models import (
    GenericModbusChargerProfile,
    ModbusEnableWrite,
    ModbusNumericWrite,
    ModbusPhaseWrite,
    ModbusReadField,
    _optional_bool,
    _optional_float_value,
    _optional_text_value,
)
from .models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple

_DEFAULT_BIT_DATA_TYPE = "bool"
_DEFAULT_REGISTER_DATA_TYPE = "uint16"
_DEFAULT_WORD_ORDER = "big"
_DEFAULT_PROFILE_NAME = "generic"


def _optional_section(parser: configparser.ConfigParser, name: str) -> configparser.SectionProxy | None:
    """Return one optional config section."""
    return parser[name] if parser.has_section(name) else None


def _required_int(section: configparser.SectionProxy, key: str) -> int:
    """Return one required integer config value."""
    value = str(section.get(key, "")).strip()
    if not value:
        raise ValueError(f"Modbus config section [{section.name}] requires {key}")
    return int(value)


def _normalized_register_type(section: configparser.SectionProxy, *, write: bool = False) -> str:
    """Return one validated register kind."""
    normalized = str(section.get("RegisterType", "")).strip().lower()
    allowed = {"holding", "input", "coil", "discrete"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported Modbus RegisterType '{normalized}' in [{section.name}]")
    if write and normalized in {"input", "discrete"}:
        raise ValueError(f"Modbus writes in [{section.name}] require RegisterType=coil or holding")
    return normalized


def _normalized_data_type(section: configparser.SectionProxy, default: str) -> str:
    """Return one validated Modbus scalar data type."""
    normalized = str(section.get("DataType", default)).strip().lower()
    if normalized not in {"bool", "uint16", "int16", "uint32", "int32", "float32"}:
        raise ValueError(f"Unsupported Modbus DataType '{normalized}' in [{section.name}]")
    return normalized


def _normalized_word_order(section: configparser.SectionProxy) -> str:
    """Return one validated Modbus word order."""
    normalized = str(section.get("WordOrder", _DEFAULT_WORD_ORDER)).strip().lower()
    if normalized not in {"big", "little"}:
        raise ValueError(f"Unsupported Modbus WordOrder '{normalized}' in [{section.name}]")
    return normalized


def _normalized_scale(section: configparser.SectionProxy) -> float:
    """Return one validated Modbus scale factor."""
    scale = finite_float_or_none(section.get("Scale"))
    if scale is None or scale == 0.0:
        return 1.0
    return float(scale)


def _parsed_value_map(raw_value: object) -> dict[int, str] | None:
    """Return one optional integer-to-text value map."""
    text = str(raw_value).strip()
    if not text:
        return None
    parsed: dict[int, str] = {}
    for token in text.split(","):
        key_text, _, value_text = token.partition(":")
        key = int(key_text.strip())
        parsed[key] = value_text.strip()
    return parsed or None


def _parsed_phase_selection_map(raw_value: object) -> dict[PhaseSelection, int]:
    """Return one required phase-selection write map."""
    text = str(raw_value).strip()
    if not text:
        raise ValueError("Modbus phase write requires Map")
    parsed: dict[PhaseSelection, int] = {}
    for token in text.split(","):
        key_text, _, value_text = token.partition(":")
        selection = normalize_phase_selection(key_text.strip())
        parsed[selection] = int(value_text.strip())
    return parsed


def _optional_read_field(parser: configparser.ConfigParser, section_name: str) -> ModbusReadField | None:
    """Return one optional read-field descriptor."""
    section = _optional_section(parser, section_name)
    if section is None:
        return None
    register_type = _normalized_register_type(section)
    data_type = _normalized_data_type(
        section,
        _default_read_data_type(register_type),
    )
    return ModbusReadField(
        register_type=register_type,
        address=_required_int(section, "Address"),
        data_type=data_type,
        scale=_normalized_scale(section),
        word_order=_normalized_word_order(section),
        value_map=_parsed_value_map(section.get("ValueMap", "")),
    )


def _required_enable_write(parser: configparser.ConfigParser) -> ModbusEnableWrite:
    """Return the required enable-write descriptor."""
    section = parser["EnableWrite"] if parser.has_section("EnableWrite") else None
    if section is None:
        raise ValueError("Modbus charger backend requires [EnableWrite]")
    return ModbusEnableWrite(
        register_type=_normalized_register_type(section, write=True),
        address=_required_int(section, "Address"),
        true_value=int(str(section.get("TrueValue", "1")).strip() or "1"),
        false_value=int(str(section.get("FalseValue", "0")).strip() or "0"),
    )


def _required_current_write(parser: configparser.ConfigParser) -> ModbusNumericWrite:
    """Return the required current-write descriptor."""
    section = parser["CurrentWrite"] if parser.has_section("CurrentWrite") else None
    if section is None:
        raise ValueError("Modbus charger backend requires [CurrentWrite]")
    return ModbusNumericWrite(
        register_type=_normalized_register_type(section, write=True),
        address=_required_int(section, "Address"),
        data_type=_normalized_data_type(section, _DEFAULT_REGISTER_DATA_TYPE),
        scale=_normalized_scale(section),
        word_order=_normalized_word_order(section),
    )


def _optional_phase_write(parser: configparser.ConfigParser) -> ModbusPhaseWrite | None:
    """Return the optional phase-write descriptor."""
    section = _optional_section(parser, "PhaseWrite")
    if section is None:
        return None
    return ModbusPhaseWrite(
        register_type=_normalized_register_type(section, write=True),
        address=_required_int(section, "Address"),
        data_type=_normalized_data_type(section, _DEFAULT_REGISTER_DATA_TYPE),
        word_order=_normalized_word_order(section),
        selection_map=_parsed_phase_selection_map(section.get("Map", "")),
    )


def _supported_phase_selections(capabilities: configparser.SectionProxy | None, phase_write: ModbusPhaseWrite | None) -> tuple[PhaseSelection, ...]:
    """Return one normalized supported-phase tuple for the generic profile."""
    configured = _configured_supported_phase_selections(capabilities)
    normalized = normalize_phase_selection_tuple(configured)
    if phase_write is None:
        return normalized
    available = _mapped_supported_phase_selections(normalized, phase_write)
    mapped = tuple(phase_write.selection_map.keys())
    return available or mapped or ("P1",)


def _configured_supported_phase_selections(capabilities: configparser.SectionProxy | None) -> str:
    """Return the configured supported phase-selection text."""
    if capabilities is None:
        return "P1"
    return capabilities.get("SupportedPhaseSelections", "P1")


def _enable_uses_current_write(capabilities: configparser.SectionProxy | None) -> bool:
    """Return whether current writes should double as enable/disable control."""
    if capabilities is None:
        return False
    raw_value = str(capabilities.get("EnableUsesCurrentWrite")).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _enable_default_current_amps(capabilities: configparser.SectionProxy | None) -> float:
    """Return the fallback current used when enabling via current writes."""
    if capabilities is None:
        return 6.0
    configured = finite_float_or_none(capabilities.get("EnableDefaultCurrentAmps"))
    if configured is None or configured <= 0.0:
        return 6.0
    return float(configured)


def _mapped_supported_phase_selections(
    normalized: tuple[PhaseSelection, ...],
    phase_write: ModbusPhaseWrite,
) -> tuple[PhaseSelection, ...]:
    """Return supported selections that are also writable by the phase map."""
    return tuple(selection for selection in normalized if selection in phase_write.selection_map)


def load_generic_modbus_charger_profile(parser: configparser.ConfigParser) -> GenericModbusChargerProfile:
    """Return one generic register-schema profile from config sections."""
    capabilities = _optional_section(parser, "Capabilities")
    enable_uses_current_write = _enable_uses_current_write(capabilities)
    phase_write = _optional_phase_write(parser)
    supported_phase_selections = _supported_phase_selections(capabilities, phase_write)
    _validate_supported_phase_writes(supported_phase_selections, phase_write)
    enable_write = _validated_enable_section(parser, enable_uses_current_write)
    return GenericModbusChargerProfile(
        profile_name="generic",
        supported_phase_selections=supported_phase_selections,
        state_enabled=_optional_read_field(parser, "StateEnabled"),
        state_current=_optional_read_field(parser, "StateCurrent"),
        state_phase_selection=_optional_read_field(parser, "StatePhase"),
        state_actual_current=_optional_read_field(parser, "StateActualCurrent"),
        state_power_watts=_optional_read_field(parser, "StatePower"),
        state_energy_kwh=_optional_read_field(parser, "StateEnergy"),
        state_status=_optional_read_field(parser, "StateStatus"),
        state_fault=_optional_read_field(parser, "StateFault"),
        enable_write=_required_enable_write(parser) if enable_write is not None else None,
        current_write=_required_current_write(parser),
        phase_write=phase_write,
        enable_uses_current_write=enable_uses_current_write,
        enable_default_current_amps=_enable_default_current_amps(capabilities),
    )


def _validate_supported_phase_writes(
    supported_phase_selections: tuple[PhaseSelection, ...],
    phase_write: ModbusPhaseWrite | None,
) -> None:
    """Reject multi-phase profiles that cannot actually switch phases."""
    if len(supported_phase_selections) > 1 and phase_write is None:
        raise ValueError("Multi-phase Modbus charger profiles require [PhaseWrite]")


def _validated_enable_section(
    parser: configparser.ConfigParser,
    enable_uses_current_write: bool,
) -> configparser.SectionProxy | None:
    """Return one validated enable section or allow current-write emulation."""
    enable_write = _optional_section(parser, "EnableWrite")
    if enable_write is not None or enable_uses_current_write:
        return enable_write
    raise ValueError("Modbus charger backend requires [EnableWrite]")


def load_modbus_charger_profile(parser: configparser.ConfigParser) -> GenericModbusChargerProfile:
    """Return one Modbus charger profile selected by Adapter.Profile."""
    adapter = parser["Adapter"] if parser.has_section("Adapter") else parser["DEFAULT"]
    profile_name = _profile_name(adapter.get("Profile"))
    if profile_name != _DEFAULT_PROFILE_NAME:
        raise ValueError(f"Unsupported Modbus charger profile '{profile_name}'")
    return load_generic_modbus_charger_profile(parser)


def _default_read_data_type(register_type: str) -> str:
    """Return the implicit data type for one read register kind."""
    if register_type in {"coil", "discrete"}:
        return _DEFAULT_BIT_DATA_TYPE
    return _DEFAULT_REGISTER_DATA_TYPE


def _profile_name(raw_value: object) -> str:
    """Return one normalized profile name from Adapter.Profile."""
    return str(raw_value).strip().lower() if raw_value is not None and str(raw_value).strip() else _DEFAULT_PROFILE_NAME


__all__ = [
    "GenericModbusChargerProfile",
    "ModbusEnableWrite",
    "ModbusNumericWrite",
    "ModbusPhaseWrite",
    "ModbusReadField",
    "_normalized_data_type",
    "_normalized_register_type",
    "_normalized_scale",
    "_normalized_word_order",
    "_optional_bool",
    "_optional_float_value",
    "_optional_text_value",
    "_parsed_phase_selection_map",
    "_required_current_write",
    "_required_enable_write",
    "_required_int",
    "load_generic_modbus_charger_profile",
    "load_modbus_charger_profile",
]
