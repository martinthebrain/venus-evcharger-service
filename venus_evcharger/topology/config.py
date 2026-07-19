# SPDX-License-Identifier: GPL-3.0-or-later
"""Parsing and validation helpers for the normalized topology schema."""

from __future__ import annotations

import configparser
from collections.abc import Mapping
from typing import Any, Protocol, TypeVar

from venus_evcharger.backend.config_migration import (
    LegacyConfigMigrationError,
    migrate_legacy_backend_config,
)
from venus_evcharger.core.contracts import optional_text

from .schema import (
    ActuatorConfig,
    ActuatorType,
    ChargerConfig,
    ChargerType,
    EvChargerTopologyConfig,
    MeasurementConfig,
    MeasurementType,
    PolicyConfig,
    PolicyMode,
    TopologyConfig,
    TopologyType,
)


class TopologyConfigError(ValueError):
    """Raised when one normalized topology configuration is invalid."""


_ChoiceT = TypeVar("_ChoiceT", bound=str)

_TOPOLOGY_CHOICES: Mapping[str, TopologyType] = {
    "simple_relay": "simple_relay",
    "native_device": "native_device",
    "hybrid_topology": "hybrid_topology",
    "custom_topology": "custom_topology",
}

_POLICY_CHOICES: Mapping[str, PolicyMode] = {
    "manual": "manual",
    "auto": "auto",
    "scheduled": "scheduled",
}

_ACTUATOR_CHOICES: Mapping[str, ActuatorType] = {
    "cerbo_gx_relay_switch": "cerbo_gx_relay_switch",
    "shelly_switch": "shelly_switch",
    "shelly_contactor_switch": "shelly_contactor_switch",
    "template_switch": "template_switch",
    "tasmota_switch": "tasmota_switch",
    "tasmota_contactor_switch": "tasmota_contactor_switch",
    "tuya_switch": "tuya_switch",
    "tuya_contactor_switch": "tuya_contactor_switch",
    "switch_group": "switch_group",
    "custom": "custom",
}

_MEASUREMENT_CHOICES: Mapping[str, MeasurementType] = {
    "actuator_native": "actuator_native",
    "charger_native": "charger_native",
    "external_meter": "external_meter",
    "fixed_reference": "fixed_reference",
    "learned_reference": "learned_reference",
    "none": "none",
}

_CHARGER_CHOICES: Mapping[str, ChargerType] = {
    "goe_charger": "goe_charger",
    "simpleevse_charger": "simpleevse_charger",
    "smartevse_charger": "smartevse_charger",
    "modbus_charger": "modbus_charger",
    "template_charger": "template_charger",
    "custom": "custom",
}


class _TopologySection(Protocol):
    """Minimal section surface used by the topology parser."""

    @property
    def name(self) -> str:
        """Return the source section name."""

    def get(self, key: str, fallback: Any = None) -> object | None:
        """Return one section value using the parser's key lookup rules."""


_TopologySectionLike = _TopologySection | configparser.SectionProxy


class _TopologyConfigSections(Protocol):
    """Minimal ConfigParser surface used by topology role parsers."""

    def has_section(self, name: str) -> bool:
        """Return whether a section exists."""

    def __getitem__(self, key: str) -> _TopologySectionLike:
        """Return one parsed config section."""


def parse_topology_config(config: configparser.ConfigParser) -> EvChargerTopologyConfig:
    """Parse one normalized topology config from INI sections."""
    topology_section = _required_section(config, "Topology")
    topology = TopologyConfig(type=_topology_type(_required_value(topology_section, "Type")))
    actuator = _optional_actuator(config)
    measurement = _optional_measurement(config)
    charger = _optional_charger(config)
    policy = _policy(config)
    parsed = EvChargerTopologyConfig(
        topology=topology,
        actuator=actuator,
        measurement=measurement,
        charger=charger,
        policy=policy,
    )
    validate_topology_config(parsed)
    return parsed


def legacy_topology_from_config(config: configparser.ConfigParser) -> EvChargerTopologyConfig:
    """Migrate one historical wallbox config at the public load boundary.

    The implementation deliberately lives in the backend migration module so
    the normalized topology parser remains unaware of historical INI fields.
    This function remains as the supported public migration entry point.
    """
    try:
        migrated = migrate_legacy_backend_config(config)
    except LegacyConfigMigrationError as error:
        raise TopologyConfigError(str(error)) from error
    return validate_topology_config(migrated.topology)


def validate_topology_config(config: EvChargerTopologyConfig) -> EvChargerTopologyConfig:
    """Validate one normalized topology configuration."""
    _validate_topology_requirements(config)
    if config.measurement is None:
        return config
    _validate_measurement(config)
    _validate_policy(config)
    return config


def _validate_measurement(config: EvChargerTopologyConfig) -> None:
    measurement = config.measurement
    if measurement is None:
        return
    _validate_measurement_config_path(measurement)
    _validate_measurement_reference(measurement)
    _validate_measurement_dependencies(config, measurement.type)


def _validate_topology_requirements(config: EvChargerTopologyConfig) -> None:
    """Validate required top-level roles for one topology kind."""
    topology_type = config.topology.type
    if topology_type == "simple_relay":
        _require_actuator(config, "simple_relay requires an actuator")
        return
    if topology_type == "native_device":
        _require_charger(config, "native_device requires a charger")
        return
    if topology_type == "hybrid_topology":
        _require_actuator(config, "hybrid_topology requires both charger and actuator")
        _require_charger(config, "hybrid_topology requires both charger and actuator")


def _require_actuator(config: EvChargerTopologyConfig, message: str) -> None:
    """Require an actuator role in one topology."""
    if config.actuator is None:
        raise TopologyConfigError(message)


def _require_charger(config: EvChargerTopologyConfig, message: str) -> None:
    """Require a charger role in one topology."""
    if config.charger is None:
        raise TopologyConfigError(message)


def _validate_measurement_config_path(measurement: MeasurementConfig) -> None:
    """Validate config-path requirements for one measurement role."""
    if measurement.type == "external_meter" and not measurement.config_path:
        raise TopologyConfigError("external_meter requires Measurement.ConfigPath")


def _validate_measurement_reference(measurement: MeasurementConfig) -> None:
    """Validate reference-power requirements for one measurement role."""
    if measurement.type == "fixed_reference" and measurement.reference_watts is None:
        raise TopologyConfigError("fixed_reference requires Measurement.ReferenceWatts")


def _validate_measurement_dependencies(config: EvChargerTopologyConfig, measurement_type: MeasurementType) -> None:
    """Validate cross-role requirements for one measurement mode."""
    if measurement_type == "charger_native" and config.charger is None:
        raise TopologyConfigError("charger_native measurement requires a charger")
    if measurement_type == "actuator_native" and config.actuator is None:
        raise TopologyConfigError("actuator_native measurement requires an actuator")


def _validate_policy(config: EvChargerTopologyConfig) -> None:
    measurement = config.measurement
    if config.policy.mode == "auto" and (measurement is None or measurement.type == "none"):
        raise TopologyConfigError("auto policy requires a non-empty measurement mode")


def _required_section(config: _TopologyConfigSections, name: str) -> _TopologySectionLike:
    if not config.has_section(name):
        raise TopologyConfigError(f"missing required section [{name}]")
    return config[name]


def _required_value(section: _TopologySectionLike, key: str) -> str:
    value = _optional_text(section.get(key))
    if value is None:
        raise TopologyConfigError(f"missing required key {section.name}.{key}")
    return value


def _optional_actuator(config: _TopologyConfigSections) -> ActuatorConfig | None:
    if not config.has_section("Actuator"):
        return None
    section = config["Actuator"]
    return ActuatorConfig(
        type=_actuator_type(_required_value(section, "Type")),
        config_path=_optional_text(section.get("ConfigPath")),
    )


def _optional_measurement(config: _TopologyConfigSections) -> MeasurementConfig | None:
    if not config.has_section("Measurement"):
        return None
    section = config["Measurement"]
    reference_text = _optional_text(section.get("ReferenceWatts"))
    return MeasurementConfig(
        type=_measurement_type(_required_value(section, "Type")),
        config_path=_optional_text(section.get("ConfigPath")),
        reference_watts=None if reference_text is None else float(reference_text),
        allow_auto_estimate=_as_bool(section.get("AllowAutoEstimate")),
    )


def _optional_charger(config: _TopologyConfigSections) -> ChargerConfig | None:
    if not config.has_section("Charger"):
        return None
    section = config["Charger"]
    return ChargerConfig(
        type=_charger_type(_required_value(section, "Type")),
        config_path=_optional_text(section.get("ConfigPath")),
    )


def _policy(config: _TopologyConfigSections) -> PolicyConfig:
    if not config.has_section("Policy"):
        return PolicyConfig()
    section = config["Policy"]
    mode_text = _optional_text(section.get("Mode"))
    mode = "manual" if mode_text is None else _policy_mode(mode_text)
    phase = _optional_text(section.get("Phase")) or "L1"
    return PolicyConfig(mode=mode, phase=phase)


def _optional_text(value: object) -> str | None:
    normalized: str | None = optional_text(value)
    return normalized


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _topology_type(value: str) -> TopologyType:
    return _literal_choice(value=value, allowed=_TOPOLOGY_CHOICES, label="Topology.Type")


def _policy_mode(value: str) -> PolicyMode:
    return _literal_choice(value=value, allowed=_POLICY_CHOICES, label="Policy.Mode")


def _actuator_type(value: str) -> ActuatorType:
    return _literal_choice(value=value, allowed=_ACTUATOR_CHOICES, label="Actuator.Type")


def _measurement_type(value: str) -> MeasurementType:
    return _literal_choice(value=value, allowed=_MEASUREMENT_CHOICES, label="Measurement.Type")


def _charger_type(value: str) -> ChargerType:
    return _literal_choice(value=value, allowed=_CHARGER_CHOICES, label="Charger.Type")


def _literal_choice(value: str, allowed: Mapping[str, _ChoiceT], label: str) -> _ChoiceT:
    normalized = value.strip().lower()
    selected = allowed.get(normalized)
    if selected is None:
        choices = ", ".join(sorted(allowed))
        raise TopologyConfigError(f"invalid {label}: {value!r} (expected one of: {choices})")
    return selected
