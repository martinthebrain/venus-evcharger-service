# SPDX-License-Identifier: GPL-3.0-or-later
"""Topology-to-backend runtime mapping helpers."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

from venus_evcharger.topology.schema import EvChargerTopologyConfig

from .config_file import normalized_optional_lower_text, normalized_optional_path
from .config_normalization import DEFAULT_COMBINED_METER_TYPE
from .config_summary import _build_runtime_summary
from .models import BackendRuntimeSummary


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
        return _optional_lower_text(parser["Adapter"].get("Type"))
    return _optional_lower_text(parser["DEFAULT"].get("Type"))


def _optional_lower_text(value: object) -> str | None:
    """Return trimmed lowercase text or ``None``."""
    return normalized_optional_lower_text(value)


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
