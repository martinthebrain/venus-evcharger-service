# SPDX-License-Identifier: GPL-3.0-or-later
"""One-way migration from historical wallbox INI fields to typed config data.

This module is the only production boundary that understands the historical
``DEFAULT``/``Backends`` schema.  Callers must consume either ``topology`` or
the normalized backend fields on :class:`LegacyBackendConfigMigration`; raw
legacy values must not escape into runtime code.
"""

from __future__ import annotations

import configparser
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ActuatorType,
    ChargerConfig,
    ChargerType,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    PolicyMode,
    TopologyConfig,
)

from .config_normalization import (
    DEFAULT_COMBINED_METER_TYPE,
    DEFAULT_COMBINED_SWITCH_TYPE,
    _runtime_role_alias,
    _split_none_role,
    normalize_backend_mode,
    normalize_backend_type,
    normalize_config_path,
    normalize_optional_backend_type,
)
from .models import BackendMode


class LegacyConfigMigrationError(ValueError):
    """Raised when a historical field cannot map to the canonical schema."""


_LEGACY_ACTUATOR_TYPES: Mapping[str, ActuatorType] = {
    "cerbo_gx_relay_switch": "cerbo_gx_relay_switch",
    "shelly_switch": "shelly_switch",
    "shelly_contactor_switch": "shelly_contactor_switch",
    "template_switch": "template_switch",
    "tasmota_switch": "tasmota_switch",
    "tasmota_contactor_switch": "tasmota_contactor_switch",
    "tuya_switch": "tuya_switch",
    "tuya_contactor_switch": "tuya_contactor_switch",
    "switch_group": "switch_group",
}

_LEGACY_CHARGER_TYPES: Mapping[str, ChargerType] = {
    "goe_charger": "goe_charger",
    "simpleevse_charger": "simpleevse_charger",
    "smartevse_charger": "smartevse_charger",
    "modbus_charger": "modbus_charger",
    "template_charger": "template_charger",
    "custom": "custom",
}


@dataclass(frozen=True)
class LegacyBackendConfigMigration:
    """Typed result produced once at the historical configuration boundary."""

    topology: EvChargerTopologyConfig
    backend_mode: BackendMode
    host: str
    meter_type: str | None
    meter_config_path: Path | None
    switch_type: str | None
    switch_config_path: Path | None
    charger_type: str | None
    charger_config_path: Path | None


@dataclass(frozen=True)
class _LegacyConfigInput:
    """Normalized historical fields used to build canonical models."""

    defaults: Mapping[str, object]
    backend_mode: BackendMode
    host: str
    meter_type: str
    switch_type: str
    charger_type_raw: str
    meter_path: str | None
    switch_path: str | None
    charger_path: str | None


def migrate_legacy_backend_config(config: configparser.ConfigParser) -> LegacyBackendConfigMigration:
    """Parse historical fields once and return typed canonical projections."""
    legacy = _legacy_runtime_values(config)
    charger_type = normalize_optional_backend_type(legacy.charger_type_raw)
    topology = _legacy_topology(legacy)
    return LegacyBackendConfigMigration(
        topology=topology,
        backend_mode=legacy.backend_mode,
        host=legacy.host,
        meter_type=_runtime_meter_role_from_legacy(legacy.backend_mode, legacy.meter_type),
        meter_config_path=normalize_config_path(legacy.meter_path),
        switch_type=_runtime_switch_role_from_legacy(legacy.backend_mode, legacy.switch_type),
        switch_config_path=normalize_config_path(legacy.switch_path),
        charger_type=charger_type,
        charger_config_path=normalize_config_path(legacy.charger_path),
    )


def _legacy_runtime_values(config: configparser.ConfigParser) -> _LegacyConfigInput:
    """Read and normalize the historical INI fields exactly once."""
    defaults = _default_mapping(config)
    backends = _legacy_backend_section(config, defaults)
    return _LegacyConfigInput(
        defaults=defaults,
        backend_mode=normalize_backend_mode(backends.get("Mode")),
        host=str(defaults.get("Host", "")).strip(),
        meter_type=_legacy_text_value(backends, "MeterType", DEFAULT_COMBINED_METER_TYPE),
        switch_type=_legacy_text_value(backends, "SwitchType", DEFAULT_COMBINED_SWITCH_TYPE),
        charger_type_raw=_legacy_text_value(backends, "ChargerType", ""),
        meter_path=_optional_text(backends.get("MeterConfigPath")),
        switch_path=_optional_text(backends.get("SwitchConfigPath")),
        charger_path=_optional_text(backends.get("ChargerConfigPath")),
    )


def _legacy_backend_section(
    config: configparser.ConfigParser,
    defaults: Mapping[str, object],
) -> Mapping[str, object]:
    """Return the historical backend section with DEFAULT fallback semantics."""
    return config["Backends"] if config.has_section("Backends") else defaults


def _default_mapping(config: configparser.ConfigParser) -> Mapping[str, object]:
    """Return the DEFAULT section or an empty mapping for parser-like tests."""
    if "DEFAULT" not in config:
        return {}
    return config["DEFAULT"]


def _legacy_topology(legacy: _LegacyConfigInput) -> EvChargerTopologyConfig:
    """Build the canonical topology projection from normalized legacy fields."""
    policy = PolicyConfig(
        mode=_legacy_policy_mode(legacy.defaults.get("Mode")),
        phase=_legacy_phase(legacy.defaults.get("Phase")),
    )
    charger = _legacy_charger(legacy.charger_type_raw, legacy.charger_path)
    return _legacy_topology_config(legacy, charger, policy)


def _legacy_policy_mode(value: object) -> PolicyMode:
    normalized = str(value).strip()
    if normalized == "1":
        return "auto"
    if normalized == "2":
        return "scheduled"
    return "manual"


def _legacy_phase(value: object) -> str:
    return _optional_text(value) or "L1"


def _legacy_charger(charger_type: str, charger_path: str | None) -> ChargerConfig | None:
    if not charger_type:
        return None
    normalized = charger_type.strip().lower()
    selected = _LEGACY_CHARGER_TYPES.get(normalized)
    if selected is None:
        choices = ", ".join(sorted(_LEGACY_CHARGER_TYPES))
        raise LegacyConfigMigrationError(
            f"invalid Charger.Type: {charger_type!r} (expected one of: {choices})"
        )
    return ChargerConfig(type=selected, config_path=charger_path)


def _legacy_topology_config(
    legacy: _LegacyConfigInput,
    charger: ChargerConfig | None,
    policy: PolicyConfig,
) -> EvChargerTopologyConfig:
    if charger is None:
        return EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=_legacy_actuator_config(legacy.switch_type, legacy.switch_path, legacy.host),
            measurement=_legacy_measurement_config(legacy.meter_type, legacy.meter_path, legacy.host),
            policy=policy,
        )
    if legacy.switch_type == "none":
        return EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=_legacy_native_measurement_config(legacy.meter_path),
            charger=charger,
            policy=policy,
        )
    return EvChargerTopologyConfig(
        topology=TopologyConfig(type="hybrid_topology"),
        actuator=_legacy_actuator_config(legacy.switch_type, legacy.switch_path, legacy.host),
        measurement=_legacy_hybrid_measurement_config(
            legacy.meter_type,
            legacy.meter_path,
            legacy.charger_type_raw,
        ),
        charger=charger,
        policy=policy,
    )


def _legacy_switch_actuator_type(switch_type: str, host: str) -> ActuatorType:
    normalized = _legacy_switch_type(switch_type, host)
    alias = _legacy_switch_alias(normalized, host)
    if alias is not None:
        return alias
    selected = _LEGACY_ACTUATOR_TYPES.get(normalized)
    if selected is not None:
        return selected
    return "custom"


def _legacy_switch_type(switch_type: str, host: str) -> str:
    return switch_type or (DEFAULT_COMBINED_SWITCH_TYPE if host else "")


def _legacy_switch_alias(normalized: str, host: str) -> ActuatorType | None:
    if normalized == "shelly_combined" and host:
        return "shelly_contactor_switch"
    return None


def _known_legacy_switch_type(normalized: str) -> bool:
    return normalized in _LEGACY_ACTUATOR_TYPES


def _legacy_actuator_config(switch_type: str, switch_path: str | None, host: str) -> ActuatorConfig | None:
    if switch_type == "none" and switch_path is None and not host:
        return None
    return ActuatorConfig(type=_legacy_switch_actuator_type(switch_type, host), config_path=switch_path)


def _legacy_measurement_config(meter_type: str, meter_path: str | None, host: str) -> MeasurementConfig:
    if meter_type == "none":
        return MeasurementConfig(type="none")
    if meter_path is not None:
        return MeasurementConfig(type="external_meter", config_path=meter_path)
    if host:
        return MeasurementConfig(type="actuator_native")
    return MeasurementConfig(type="none")


def _legacy_native_measurement_config(meter_path: str | None) -> MeasurementConfig:
    if meter_path is not None:
        return MeasurementConfig(type="external_meter", config_path=meter_path)
    return MeasurementConfig(type="charger_native")


def _legacy_hybrid_measurement_config(
    meter_type: str,
    meter_path: str | None,
    charger_type: str,
) -> MeasurementConfig:
    if meter_type == "none":
        return MeasurementConfig(type="charger_native" if charger_type else "none")
    if meter_path is not None:
        return MeasurementConfig(type="external_meter", config_path=meter_path)
    return MeasurementConfig(type="actuator_native")


def _runtime_meter_role_from_legacy(mode: BackendMode, value: object) -> str | None:
    return _runtime_role_from_legacy(mode, DEFAULT_COMBINED_METER_TYPE, value)


def _runtime_switch_role_from_legacy(mode: BackendMode, value: object) -> str | None:
    return _runtime_role_from_legacy(mode, DEFAULT_COMBINED_SWITCH_TYPE, value)


def _runtime_role_from_legacy(mode: BackendMode, combined_fallback: str, value: object) -> str | None:
    normalized = normalize_backend_type(value, combined_fallback)
    if _split_none_role(mode, normalized):
        return None
    return _runtime_role_alias(combined_fallback, normalized)


def _legacy_text_value(mapping: Mapping[str, object], key: str, fallback: str) -> str:
    raw = mapping.get(key)
    return fallback if raw is None else str(raw).strip().lower()


def _optional_text(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None
