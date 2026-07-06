# SPDX-License-Identifier: GPL-3.0-or-later
"""Parsing and validation helpers for the normalized topology schema."""

from __future__ import annotations

import configparser
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from venus_evcharger.core.contracts import optional_text

from .schema import (
    ActuatorConfig,
    ActuatorType,
    ChargerConfig,
    ChargerType,
    EvChargerTopologyConfig,
    MeasurementConfig,
    MeasurementType,
    PolicyMode,
    PolicyConfig,
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


@dataclass(frozen=True)
class _LegacyTopologyRuntime:
    """Normalized legacy topology values used to reconstruct one topology config."""

    defaults: Mapping[str, object]
    host: str
    meter_type: str
    switch_type: str
    charger_type_raw: str
    meter_path: str | None
    switch_path: str | None
    charger_path: str | None


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
    """Translate one legacy wallbox config into the normalized topology model."""
    runtime = _legacy_runtime_values(config)
    policy = PolicyConfig(
        mode=_legacy_policy_mode(runtime.defaults.get("Mode")),
        phase=_legacy_phase(runtime.defaults.get("Phase")),
    )
    charger = _legacy_charger(runtime.charger_type_raw, runtime.charger_path)
    parsed = _legacy_topology_config(runtime, charger, policy)
    validate_topology_config(parsed)
    return parsed


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
    return optional_text(value)


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _legacy_policy_mode(value: object) -> PolicyMode:
    normalized = str(value).strip()
    if normalized == "1":
        return "auto"
    if normalized == "2":
        return "scheduled"
    return "manual"


def _legacy_phase(value: object) -> str:
    return _optional_text(value) or "L1"


def _legacy_runtime_values(config: configparser.ConfigParser) -> _LegacyTopologyRuntime:
    """Return normalized legacy config fields used to build one topology."""
    defaults = _default_mapping(config)

    backends: Mapping[str, object] = config["Backends"] if config.has_section("Backends") else defaults

    return _LegacyTopologyRuntime(
        defaults=defaults,
        host=str(defaults.get("Host", "")).strip(),
        meter_type=_legacy_text_value(backends, "MeterType", "shelly_meter"),
        switch_type=_legacy_text_value(backends, "SwitchType", "shelly_contactor_switch"),
        charger_type_raw=_legacy_text_value(backends, "ChargerType", ""),
        meter_path=_optional_text(backends.get("MeterConfigPath")),
        switch_path=_optional_text(backends.get("SwitchConfigPath")),
        charger_path=_optional_text(backends.get("ChargerConfigPath")),
    )


def _default_mapping(config: configparser.ConfigParser) -> Mapping[str, object]:
    """Return the legacy DEFAULT section with ConfigParser lookup semantics."""
    if "DEFAULT" not in config:
        return {}
    return config["DEFAULT"]


def _legacy_charger(charger_type_raw: str, charger_path: str | None) -> ChargerConfig | None:
    """Return one legacy charger role when configured."""
    if not charger_type_raw:
        return None
    return ChargerConfig(type=_charger_type(charger_type_raw), config_path=charger_path)


def _legacy_topology_config(
    runtime: _LegacyTopologyRuntime,
    charger: ChargerConfig | None,
    policy: PolicyConfig,
) -> EvChargerTopologyConfig:
    """Return one normalized topology config reconstructed from legacy runtime fields."""
    if charger is None:
        return EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=_legacy_actuator_config(runtime.switch_type, runtime.switch_path, runtime.host),
            measurement=_legacy_measurement_config(runtime.meter_type, runtime.meter_path, runtime.host),
            policy=policy,
        )
    if runtime.switch_type == "none":
        return EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=_legacy_native_measurement_config(runtime.meter_path),
            charger=charger,
            policy=policy,
        )
    return EvChargerTopologyConfig(
        topology=TopologyConfig(type="hybrid_topology"),
        actuator=_legacy_actuator_config(runtime.switch_type, runtime.switch_path, runtime.host),
        measurement=_legacy_hybrid_measurement_config(
            runtime.meter_type,
            runtime.meter_path,
            runtime.charger_type_raw,
        ),
        charger=charger,
        policy=policy,
    )


def _legacy_switch_actuator_type(switch_type: str, host: str) -> ActuatorType:
    normalized = _legacy_switch_type(switch_type, host)
    alias = _legacy_switch_alias(normalized, host)
    if alias is not None:
        return alias
    if _known_legacy_switch_type(normalized):
        return _actuator_type(normalized)
    return "custom"


def _legacy_switch_type(switch_type: str, host: str) -> str:
    """Return the normalized legacy switch type or a direct-host default."""
    return switch_type or ("shelly_contactor_switch" if host else "")


def _legacy_switch_alias(normalized: str, host: str) -> ActuatorType | None:
    """Return a direct actuator alias for legacy switch labels."""
    if normalized == "shelly_combined" and host:
        return "shelly_contactor_switch"
    return None


def _known_legacy_switch_type(normalized: str) -> bool:
    """Return whether one legacy switch label maps directly to a known actuator type."""
    return normalized in _ACTUATOR_CHOICES and normalized != "custom"


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


def _legacy_text_value(mapping: Mapping[str, object], key: str, fallback: str) -> str:
    raw = mapping.get(key)
    if raw is None:
        return fallback
    return str(raw).strip().lower()


def _legacy_hybrid_measurement_config(meter_type: str, meter_path: str | None, charger_type: str) -> MeasurementConfig:
    if meter_type == "none":
        return MeasurementConfig(type="charger_native" if charger_type else "none")
    if meter_path is not None:
        return MeasurementConfig(type="external_meter", config_path=meter_path)
    return MeasurementConfig(type="actuator_native")


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
