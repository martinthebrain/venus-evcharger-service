# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory for normalized meter/switch/charger backend objects."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from .base import ChargerBackend, MeterBackend, SwitchBackend
from .config import (
    runtime_summary_from_service,
)
from .config_file import normalized_optional_path
from .models import BackendRuntimeSummary
from .registry import (
    create_charger_backend,
    create_meter_backend,
    create_switch_backend,
)
from venus_evcharger.topology.config import parse_topology_config
from venus_evcharger.topology.schema import EvChargerTopologyConfig

_BackendT = TypeVar("_BackendT")
_ADAPTER_SECTION = "Adapter"
_DEFAULT_SECTION = "DEFAULT"
_TOPOLOGY_SECTION = "Topology"
_TYPE_OPTION_LOWER = "type"


@dataclass(frozen=True)
class ResolvedBackends:
    """One resolved backend bundle created from wallbox config."""

    runtime: BackendRuntimeSummary
    meter: MeterBackend | None
    switch: SwitchBackend | None
    charger: ChargerBackend | None


@dataclass(frozen=True)
class _TopologyBackendRoles:
    """Direct backend roles resolved from one normalized topology config."""

    meter_type: str | None
    meter_config_path: Path | None
    switch_type: str | None
    switch_config_path: Path | None
    charger_type: str | None
    charger_config_path: Path | None


def _config_path_arg(config_path: object) -> str:
    """Return one backend-constructor argument from an optional normalized path."""
    return "" if config_path is None else str(config_path)


def _normalized_path(config_path: str | None) -> Path | None:
    """Return one normalized optional config path."""
    return normalized_optional_path(config_path)


def _adapter_type_from_config_path(config_path: str | None) -> str:
    """Return one adapter type from a backend config file."""
    path = _normalized_path(config_path)
    if path is None:
        raise ValueError("Adapter-backed topology role requires ConfigPath")
    parser = configparser.ConfigParser()
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(str(path))
    if parser.has_section(_ADAPTER_SECTION):
        return _section_option_text(parser[_ADAPTER_SECTION], _TYPE_OPTION_LOWER)
    return _section_option_text(parser[_DEFAULT_SECTION], _TYPE_OPTION_LOWER)


def _section_option_text(section: configparser.SectionProxy, option_lower: str) -> str:
    """Return one normalized option value using ConfigParser's case-insensitive key model."""
    for key, value in section.items():
        if key.strip().lower() == option_lower:
            return str(value).strip().lower()
    return ""


def _topology_from_service(service: Any) -> EvChargerTopologyConfig | None:
    """Return one normalized topology config from runtime state or config."""
    runtime_topology = getattr(service, "_topology_config", None)
    if isinstance(runtime_topology, EvChargerTopologyConfig):
        return runtime_topology
    service_config = getattr(service, "config", None)
    if not isinstance(service_config, configparser.ConfigParser) or not service_config.has_section(_TOPOLOGY_SECTION):
        return None
    return parse_topology_config(service_config)


def _topology_backend_roles(topology: EvChargerTopologyConfig) -> _TopologyBackendRoles | None:
    """Return directly supported runtime backend roles from one topology config."""
    base_roles = _base_topology_roles(topology)
    measurement = topology.measurement
    if measurement is None:
        return _measurementless_topology_roles(topology, base_roles)
    if measurement.type == "external_meter":
        return _TopologyBackendRoles(
            meter_type=_adapter_type_from_config_path(measurement.config_path),
            meter_config_path=_normalized_path(measurement.config_path),
            switch_type=base_roles.switch_type,
            switch_config_path=base_roles.switch_config_path,
            charger_type=base_roles.charger_type,
            charger_config_path=base_roles.charger_config_path,
        )
    if _uses_native_or_empty_measurement(measurement.type):
        return _measurementless_topology_roles(topology, base_roles)
    return None


def _base_topology_roles(topology: EvChargerTopologyConfig) -> _TopologyBackendRoles:
    """Return topology role data without a resolved meter backend."""
    actuator = topology.actuator
    charger = topology.charger
    return _TopologyBackendRoles(
        meter_type=None,
        meter_config_path=None,
        switch_type=None if actuator is None else actuator.type,
        switch_config_path=None if actuator is None else _normalized_path(actuator.config_path),
        charger_type=None if charger is None else charger.type,
        charger_config_path=None if charger is None else _normalized_path(charger.config_path),
    )


def _measurementless_topology_roles(
    topology: EvChargerTopologyConfig,
    base_roles: _TopologyBackendRoles,
) -> _TopologyBackendRoles | None:
    """Return directly supported roles for topologies without an external meter backend."""
    if _is_native_device_topology(topology):
        return _native_device_roles(base_roles)
    if _is_hybrid_topology(topology):
        return base_roles
    return None


def _is_native_device_topology(topology: EvChargerTopologyConfig) -> bool:
    """Return whether one topology is a charger-native device without a switch role."""
    return topology.topology.type == "native_device" and topology.charger is not None


def _is_hybrid_topology(topology: EvChargerTopologyConfig) -> bool:
    """Return whether one topology carries both charger and actuator roles."""
    return (
        topology.topology.type == "hybrid_topology"
        and topology.actuator is not None
        and topology.charger is not None
    )


def _native_device_roles(base_roles: _TopologyBackendRoles) -> _TopologyBackendRoles:
    """Return direct backend roles for a charger-native topology."""
    return _TopologyBackendRoles(
        meter_type=None,
        meter_config_path=None,
        switch_type=None,
        switch_config_path=None,
        charger_type=base_roles.charger_type,
        charger_config_path=base_roles.charger_config_path,
    )


def _uses_native_or_empty_measurement(measurement_type: str) -> bool:
    """Return whether one topology measurement role does not create a direct meter backend."""
    return measurement_type in {"charger_native", "none"}


def _runtime_from_topology_roles(roles: _TopologyBackendRoles) -> BackendRuntimeSummary:
    """Return one runtime-facing backend summary from direct topology roles."""
    return BackendRuntimeSummary(
        backend_mode="split",
        meter_type=roles.meter_type,
        meter_config_path=roles.meter_config_path,
        switch_type=roles.switch_type,
        switch_config_path=roles.switch_config_path,
        charger_type=roles.charger_type,
        charger_config_path=roles.charger_config_path,
        topology_configured=any(
            (
                roles.charger_type is not None and roles.charger_config_path is not None,
                roles.meter_type is not None and roles.meter_config_path is not None,
                roles.switch_type is not None and roles.switch_config_path is not None,
            )
        ),
        primary_rpc_configured=False,
    )


def _backend_from_role(
    role_type: str | None,
    config_path: object,
    service: Any,
    creator: Callable[[str, Any, str], _BackendT],
) -> _BackendT | None:
    """Instantiate one backend from normalized role data."""
    if role_type is None:
        return None
    return creator(role_type, service, _config_path_arg(config_path))


def _direct_meter_backend(role_type: str | None, config_path: Path | None, service: Any) -> MeterBackend | None:
    """Instantiate one meter backend directly from topology-resolved role data."""
    return _backend_from_role(role_type, config_path, service, create_meter_backend)


def _direct_switch_backend(role_type: str | None, config_path: Path | None, service: Any) -> SwitchBackend | None:
    """Instantiate one switch backend directly from topology-resolved role data."""
    return _backend_from_role(role_type, config_path, service, create_switch_backend)


def _direct_charger_backend(role_type: str | None, config_path: Path | None, service: Any) -> ChargerBackend | None:
    """Instantiate one charger backend directly from topology-resolved role data."""
    return _backend_from_role(role_type, config_path, service, create_charger_backend)


def _resolved_from_topology(service: Any) -> ResolvedBackends | None:
    """Return one directly resolved backend bundle from normalized topology config."""
    topology = _topology_from_service(service)
    if topology is None:
        return None
    roles = _topology_backend_roles(topology)
    if roles is None:
        return None
    runtime = _runtime_from_topology_roles(roles)
    meter = _direct_meter_backend(roles.meter_type, roles.meter_config_path, service)
    switch = _direct_switch_backend(roles.switch_type, roles.switch_config_path, service)
    charger = _direct_charger_backend(roles.charger_type, roles.charger_config_path, service)
    return ResolvedBackends(runtime=runtime, meter=meter, switch=switch, charger=charger)


def _resolved_meter_backend(runtime: BackendRuntimeSummary, service: Any) -> MeterBackend | None:
    """Return the configured meter backend or validate that meterless mode is allowed."""
    return _backend_from_role(runtime.meter_type, runtime.meter_config_path, service, create_meter_backend)


def _resolved_switch_backend(runtime: BackendRuntimeSummary, service: Any) -> SwitchBackend | None:
    """Return the configured switch backend or validate that switchless mode is allowed."""
    return _backend_from_role(runtime.switch_type, runtime.switch_config_path, service, create_switch_backend)


def _resolved_charger_backend(runtime: BackendRuntimeSummary, service: Any) -> ChargerBackend | None:
    """Return the configured charger backend when present."""
    return _backend_from_role(runtime.charger_type, runtime.charger_config_path, service, create_charger_backend)


def build_service_backends(service: Any) -> ResolvedBackends:
    """Instantiate one normalized backend bundle from service config attrs.
    """
    topology_resolved = _resolved_from_topology(service)
    if topology_resolved is not None:
        return topology_resolved
    runtime = runtime_summary_from_service(service)
    meter = _resolved_meter_backend(runtime, service)
    switch = _resolved_switch_backend(runtime, service)
    charger = _resolved_charger_backend(runtime, service)
    return ResolvedBackends(
        runtime=runtime,
        meter=meter,
        switch=switch,
        charger=charger,
    )
