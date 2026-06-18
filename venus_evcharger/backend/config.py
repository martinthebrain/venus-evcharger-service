# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for normalized runtime backend summaries from wallbox configuration.

The service supports legacy combined settings and newer topology-driven adapter
files. This module is the compatibility boundary that turns either input style
into one runtime summary used by controllers, diagnostics, and state contracts.
It also preserves the old backend-view shape for callers that still need it.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

from venus_evcharger.topology.config import legacy_topology_from_config, parse_topology_config
from venus_evcharger.topology.schema import EvChargerTopologyConfig

from .config_file import normalized_optional_lower_text, normalized_optional_path
from .models import BackendMode, BackendRuntimeSummary


DEFAULT_COMBINED_METER_TYPE = "shelly_meter"
DEFAULT_COMBINED_SWITCH_TYPE = "shelly_contactor_switch"


def _combined_role_fallback(role: str) -> str:
    """Return the normalized combined fallback label for one backend role."""
    return DEFAULT_COMBINED_METER_TYPE if role == "meter" else DEFAULT_COMBINED_SWITCH_TYPE


def _role_field_name(role: str) -> str:
    """Return the normalized runtime-summary field name for one backend role."""
    return f"{role.strip().lower()}_type"


def _normalized_text_or_default(value: object, default: str = "") -> str:
    """Return trimmed text or the provided default."""
    normalized = str(value).strip() if value is not None else ""
    return normalized or default


def normalize_backend_mode(value: object) -> BackendMode:
    """Return one supported backend mode string."""
    mode = str(value).strip().lower() if value is not None else ""
    return "split" if mode == "split" else "combined"


def normalize_backend_type(value: object, fallback: str) -> str:
    """Return one normalized backend type name."""
    normalized = str(value).strip().lower() if value is not None else ""
    return normalized or fallback


def normalize_optional_backend_type(value: object) -> str | None:
    """Return one optional backend type name."""
    return normalized_optional_lower_text(value)


def normalize_config_path(value: object) -> Path | None:
    """Return one normalized optional config path."""
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        return None
    return Path(normalized)


def _configured_text(value: object) -> str:
    """Return one normalized non-empty text payload or an empty string."""
    return str(value).strip() if value is not None else ""


def _topology_sections_present(config: configparser.ConfigParser) -> bool:
    """Return whether one config includes normalized topology sections."""
    return config.has_section("Topology")


def _backends_section(config: configparser.ConfigParser) -> configparser.SectionProxy:
    """Return the preferred legacy config section for backend settings."""
    return config["Backends"] if config.has_section("Backends") else config["DEFAULT"]


def _topology_path(value: str | None) -> Path | None:
    """Return one normalized optional topology config path."""
    return normalized_optional_path(value)


def _adapter_type_from_config_path(config_path: str | None) -> str | None:
    """Return the adapter type declared by one backend role config path."""
    path = _topology_path(config_path)
    if path is None:
        return None
    return _adapter_type_from_path(path)


def _adapter_type_from_path(path: Path) -> str | None:
    """Return the adapter type declared in one parsed adapter config path."""
    parser = configparser.ConfigParser()
    read_files = parser.read(path)
    if not read_files:
        return None
    return _adapter_type_from_parser(parser)


def _adapter_type_from_parser(parser: configparser.ConfigParser) -> str | None:
    """Return the normalized adapter type from one loaded adapter parser."""
    if parser.has_section("Adapter"):
        return _optional_lower_text(parser["Adapter"].get("Type", ""))
    return _optional_lower_text(parser["DEFAULT"].get("Type", ""))


def _optional_lower_text(value: object) -> str | None:
    """Return trimmed lowercase text or ``None``."""
    return normalized_optional_lower_text(value)


def _runtime_role_from_legacy(mode: BackendMode, role: str, value: object) -> str | None:
    """Return one runtime backend role reconstructed from legacy config values."""
    normalized = normalize_backend_type(value, _combined_role_fallback(role))
    if _split_none_role(mode, normalized):
        return None
    return _runtime_role_alias(role, normalized)


def _split_none_role(mode: BackendMode, normalized: str) -> bool:
    """Return whether one split runtime role explicitly disables a backend."""
    return mode == "split" and normalized == "none"


def _runtime_role_alias(role: str, normalized: str) -> str | None:
    """Return one normalized runtime backend role after alias expansion."""
    if normalized == "shelly_combined":
        return _combined_role_fallback(role)
    return normalized or None


def _legacy_none_backend_errors(mode: BackendMode, charger_type: str | None, field_name: str) -> list[str]:
    """Return legacy validation errors for one backend field set to ``none``."""
    errors: list[str] = []
    if mode != "split":
        errors.append(f"{field_name}=none is only supported in split backend mode")
    if charger_type is None:
        errors.append(f"{field_name}=none requires a configured charger backend")
    return errors


def _validate_legacy_backend_values(mode: BackendMode, meter_type: str, switch_type: str, charger_type: str | None) -> None:
    """Raise when legacy backend settings express one unsupported topology."""
    errors: list[str] = []
    if meter_type == "none":
        errors.extend(_legacy_none_backend_errors(mode, charger_type, "MeterType"))
    if switch_type == "none":
        errors.extend(_legacy_none_backend_errors(mode, charger_type, "SwitchType"))
    for message in errors:
        raise ValueError(message)


def _legacy_view_role_from_runtime(mode: BackendMode, role: str, value: object) -> str:
    """Return one compatibility backend label reconstructed from runtime data."""
    fallback = _combined_role_fallback(role)
    if value is None:
        return "none" if mode == "split" else fallback
    normalized = _normalized_text_or_default(str(value).lower())
    return normalized or ("none" if mode == "split" else fallback)


def _build_runtime_summary(
    *,
    backend_mode: BackendMode,
    meter_type: str | None,
    meter_config_path: Path | None,
    switch_type: str | None,
    switch_config_path: Path | None,
    charger_type: str | None,
    charger_config_path: Path | None,
    legacy_host: object = "",
    primary_rpc_configured: bool | None = None,
) -> BackendRuntimeSummary:
    """Return one normalized runtime summary with derived configured flags."""
    if primary_rpc_configured is None:
        primary_rpc_configured = _legacy_primary_rpc_configured(backend_mode, legacy_host)
    topology_configured = _topology_configured(
        backend_mode=backend_mode,
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        legacy_host=legacy_host,
    )
    return BackendRuntimeSummary(
        backend_mode=backend_mode,
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        topology_configured=topology_configured,
        primary_rpc_configured=bool(primary_rpc_configured),
    )


def _legacy_primary_rpc_configured(backend_mode: BackendMode, legacy_host: object) -> bool:
    """Return whether one combined-style setup still exposes a primary RPC host."""
    return backend_mode != "split" and bool(_configured_text(legacy_host))


def _topology_configured(
    *,
    backend_mode: BackendMode,
    meter_type: str | None,
    meter_config_path: Path | None,
    switch_type: str | None,
    switch_config_path: Path | None,
    charger_type: str | None,
    charger_config_path: Path | None,
    legacy_host: object,
) -> bool:
    """Return whether runtime role information represents a configured topology."""
    if backend_mode != "split":
        return bool(_configured_text(legacy_host))
    return any(
        (
            _configured_role(meter_type, meter_config_path),
            _configured_role(switch_type, switch_config_path),
            _configured_role(charger_type, charger_config_path),
        )
    )


def _configured_role(role_type: str | None, config_path: Path | None) -> bool:
    """Return whether one runtime backend role is fully configured."""
    return role_type is not None and config_path is not None


def _native_meter_type_for_actuator(actuator_type: str | None) -> str | None:
    """Return one native meter backend type implied by a switch actuator."""
    if actuator_type in {"shelly_switch", "shelly_contactor_switch"}:
        return DEFAULT_COMBINED_METER_TYPE
    return None


def _runtime_summary_from_topology(topology: EvChargerTopologyConfig) -> BackendRuntimeSummary:
    """Return one runtime backend summary from a normalized topology config."""
    switch_type = _topology_actuator_type(topology)
    switch_config_path = _topology_actuator_path(topology)
    charger_type = _topology_charger_type(topology)
    charger_config_path = _topology_charger_path(topology)
    meter_type, meter_config_path = _topology_measurement_role(topology, switch_type)
    return _build_runtime_summary(
        backend_mode="split",
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        primary_rpc_configured=False,
    )


def _topology_actuator_type(topology: EvChargerTopologyConfig) -> str | None:
    """Return one normalized actuator type from topology data."""
    return None if topology.actuator is None else topology.actuator.type


def _topology_actuator_path(topology: EvChargerTopologyConfig) -> Path | None:
    """Return one normalized actuator config path from topology data."""
    return None if topology.actuator is None else _topology_path(topology.actuator.config_path)


def _topology_charger_type(topology: EvChargerTopologyConfig) -> str | None:
    """Return one normalized charger type from topology data."""
    return None if topology.charger is None else topology.charger.type


def _topology_charger_path(topology: EvChargerTopologyConfig) -> Path | None:
    """Return one normalized charger config path from topology data."""
    return None if topology.charger is None else _topology_path(topology.charger.config_path)


def _topology_measurement_role(
    topology: EvChargerTopologyConfig,
    switch_type: str | None,
) -> tuple[str | None, Path | None]:
    """Return runtime meter role data derived from the normalized measurement role."""
    measurement = topology.measurement
    if measurement is None:
        return None, None
    if measurement.type == "external_meter":
        return (
            _adapter_type_from_config_path(measurement.config_path),
            _topology_path(measurement.config_path),
        )
    if measurement.type == "actuator_native":
        return _native_meter_type_for_actuator(switch_type), None
    if measurement.type == "charger_native":
        return None, None
    return None, None


def _topology_backend_label(topology: EvChargerTopologyConfig, role: str) -> str | None:
    """Return one outward-facing backend label from a normalized topology."""
    normalized_role = role.strip().lower()
    if normalized_role == "meter":
        return _measurement_backend_label(topology)
    if normalized_role == "switch":
        return _topology_actuator_type(topology)
    if normalized_role == "charger":
        return _topology_charger_type(topology)
    return None


def _measurement_backend_label(topology: EvChargerTopologyConfig) -> str | None:
    """Return the outward-facing measurement backend label for one topology."""
    measurement = topology.measurement
    if measurement is None:
        return None
    simple_label = _simple_measurement_backend_label(measurement.type)
    if simple_label is not None:
        return simple_label
    return _dynamic_measurement_backend_label(topology, measurement)


def _simple_measurement_backend_label(measurement_type: str) -> str | None:
    """Return direct backend labels for measurement modes without role lookups."""
    if measurement_type in {"fixed_reference", "learned_reference", "none"}:
        return measurement_type
    return None


def _dynamic_measurement_backend_label(
    topology: EvChargerTopologyConfig,
    measurement: Any,
) -> str | None:
    """Return backend labels for measurement modes that depend on role wiring."""
    if measurement.type == "external_meter":
        return _adapter_type_from_config_path(measurement.config_path)
    if measurement.type == "actuator_native":
        return _native_meter_type_for_actuator(_topology_actuator_type(topology))
    if measurement.type == "charger_native":
        return _topology_charger_type(topology)
    return None


def _runtime_summary_from_legacy_config(config: configparser.ConfigParser) -> BackendRuntimeSummary:
    """Return one runtime backend summary from legacy combined/split config sections."""
    section = _backends_section(config)
    legacy_host = config["DEFAULT"].get("Host", "") if "DEFAULT" in config else ""
    mode = normalize_backend_mode(section.get("Mode", "combined"))
    raw_meter_type = normalize_backend_type(section.get("MeterType", DEFAULT_COMBINED_METER_TYPE), DEFAULT_COMBINED_METER_TYPE)
    raw_switch_type = normalize_backend_type(section.get("SwitchType", DEFAULT_COMBINED_SWITCH_TYPE), DEFAULT_COMBINED_SWITCH_TYPE)
    charger_type = normalize_optional_backend_type(section.get("ChargerType", ""))
    _validate_legacy_backend_values(mode, raw_meter_type, raw_switch_type, charger_type)
    return _build_runtime_summary(
        backend_mode=mode,
        meter_type=_runtime_role_from_legacy(mode, "meter", raw_meter_type),
        meter_config_path=normalize_config_path(section.get("MeterConfigPath", "")),
        switch_type=_runtime_role_from_legacy(mode, "switch", raw_switch_type),
        switch_config_path=normalize_config_path(section.get("SwitchConfigPath", "")),
        charger_type=charger_type,
        charger_config_path=normalize_config_path(section.get("ChargerConfigPath", "")),
        legacy_host=legacy_host,
    )


def _runtime_summary_from_legacy_service_attrs(service: Any) -> BackendRuntimeSummary:
    """Return one normalized runtime summary from explicit legacy service attrs."""
    mode = normalize_backend_mode(getattr(service, "backend_mode", "combined"))
    raw_meter_type = normalize_backend_type(getattr(service, "meter_backend_type", DEFAULT_COMBINED_METER_TYPE), DEFAULT_COMBINED_METER_TYPE)
    raw_switch_type = normalize_backend_type(getattr(service, "switch_backend_type", DEFAULT_COMBINED_SWITCH_TYPE), DEFAULT_COMBINED_SWITCH_TYPE)
    charger_type = normalize_optional_backend_type(getattr(service, "charger_backend_type", None))
    _validate_legacy_backend_values(mode, raw_meter_type, raw_switch_type, charger_type)
    return _build_runtime_summary(
        backend_mode=mode,
        meter_type=_runtime_role_from_legacy(mode, "meter", raw_meter_type),
        meter_config_path=normalize_config_path(getattr(service, "meter_backend_config_path", "")),
        switch_type=_runtime_role_from_legacy(mode, "switch", raw_switch_type),
        switch_config_path=normalize_config_path(getattr(service, "switch_backend_config_path", "")),
        charger_type=charger_type,
        charger_config_path=normalize_config_path(getattr(service, "charger_backend_config_path", "")),
        legacy_host=getattr(service, "host", ""),
    )


def _service_has_legacy_backend_attrs(service: Any) -> bool:
    """Return whether one service explicitly carries legacy backend attrs."""
    return any(
        hasattr(service, attribute_name)
        for attribute_name in (
            "backend_mode",
            "meter_backend_type",
            "switch_backend_type",
            "charger_backend_type",
            "meter_backend_config_path",
            "switch_backend_config_path",
            "charger_backend_config_path",
        )
    )


def _summary_role_value(summary: BackendRuntimeSummary, role: str, default: str) -> str:
    """Return one normalized backend role value from a runtime summary."""
    return _label_or_default(getattr(summary, _role_field_name(role), default), default)


def _label_or_default(label: object, default: str) -> str:
    """Return trimmed label text or the provided default."""
    if label is None:
        return default
    return _normalized_text_or_default(label, default)


def _type_from_service_config(
    service_config: configparser.ConfigParser,
    role: str,
    default: str,
) -> str:
    """Return one backend type value derived from service config state."""
    if _topology_sections_present(service_config):
        return _label_or_default(
            _topology_backend_label(parse_topology_config(service_config), role),
            default,
        )
    return _summary_role_value(load_runtime_backend_summary(service_config), role, default)


def _legacy_service_role_value(service: Any, role: str, default: str) -> str:
    """Return one backend role value from explicit legacy service attrs."""
    attribute_name = f"{role}_backend_type"
    return _label_or_default(getattr(service, attribute_name, default), default)


def runtime_summary_is_configured(summary: BackendRuntimeSummary, *, legacy_host: object = "") -> bool:
    """Return whether one runtime summary represents a configured load topology."""
    if summary.backend_mode != "split":
        return bool(_configured_text(legacy_host)) or summary.topology_configured
    return bool(summary.topology_configured)


def runtime_summary_uses_legacy_primary_rpc(summary: BackendRuntimeSummary, *, legacy_host: object = "") -> bool:
    """Return whether one runtime summary still uses the legacy direct Shelly RPC host."""
    return bool(summary.primary_rpc_configured) or (summary.backend_mode != "split" and bool(_configured_text(legacy_host)))


def load_runtime_backend_summary(config: configparser.ConfigParser) -> BackendRuntimeSummary:
    """Return one normalized runtime backend summary from wallbox config."""
    if _topology_sections_present(config):
        return _runtime_summary_from_topology(parse_topology_config(config))
    return _runtime_summary_from_legacy_config(config)


from .config_service import (  # noqa: E402
    backend_mode_for_service,
    backend_type_for_service,
    compat_legacy_backend_view_from_config,
    compat_legacy_backend_view_from_runtime,
    runtime_summary_from_service,
)


__all__ = [
    "DEFAULT_COMBINED_METER_TYPE",
    "DEFAULT_COMBINED_SWITCH_TYPE",
    "backend_mode_for_service",
    "backend_type_for_service",
    "compat_legacy_backend_view_from_config",
    "compat_legacy_backend_view_from_runtime",
    "load_runtime_backend_summary",
    "normalize_backend_mode",
    "normalize_backend_type",
    "normalize_config_path",
    "normalize_optional_backend_type",
    "runtime_summary_from_service",
    "runtime_summary_is_configured",
    "runtime_summary_uses_legacy_primary_rpc",
]
