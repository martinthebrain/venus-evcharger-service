# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-summary selection helpers for service-like objects."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.topology.schema import EvChargerTopologyConfig

from .config_loader import load_runtime_backend_summary
from .config_normalization import (
    DEFAULT_COMBINED_METER_TYPE,
    DEFAULT_COMBINED_SWITCH_TYPE,
    _runtime_role_alias,
    _split_none_role,
    _validate_legacy_backend_values,
    normalize_backend_mode,
    normalize_backend_type,
    normalize_config_path,
    normalize_optional_backend_type,
)
from .config_summary import _build_runtime_summary
from .config_topology import _runtime_summary_from_topology
from .models import BackendMode, BackendRuntimeSummary


LEGACY_BACKEND_ATTRS = (
    "backend_mode",
    "meter_backend_type",
    "switch_backend_type",
    "charger_backend_type",
    "meter_backend_config_path",
    "switch_backend_config_path",
    "charger_backend_config_path",
)


def runtime_summary_from_service(service: Any) -> BackendRuntimeSummary:
    """Return one normalized runtime summary from the service's current truth source."""
    runtime = runtime_backend_summary_from_service(service)
    if runtime is not None:
        return runtime
    topology = topology_from_service_runtime(service)
    if topology is not None:
        return _runtime_summary_from_topology(topology)
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return load_runtime_backend_summary(service_config)
    if service_has_legacy_backend_attrs(service):
        return runtime_summary_from_legacy_service_attrs(service)
    return _default_runtime_summary(service)


def runtime_backend_summary_from_service(service: Any) -> BackendRuntimeSummary | None:
    """Return one runtime-facing backend summary already attached to the service."""
    bundle = getattr(service, "_backend_bundle", None)
    runtime = getattr(bundle, "runtime", None)
    return runtime if runtime is not None else None


def topology_from_service_runtime(service: Any) -> EvChargerTopologyConfig | None:
    """Return one normalized topology config already attached to the service."""
    topology = getattr(service, "_topology_config", None)
    return topology if isinstance(topology, EvChargerTopologyConfig) else None


def service_has_legacy_backend_attrs(service: Any) -> bool:
    """Return whether one service explicitly carries legacy backend attrs."""
    return any(hasattr(service, attribute_name) for attribute_name in LEGACY_BACKEND_ATTRS)


def runtime_summary_from_legacy_service_attrs(service: Any) -> BackendRuntimeSummary:
    """Return one normalized runtime summary from explicit legacy service attrs."""
    mode = normalize_backend_mode(_service_attr(service, "backend_mode"))
    raw_meter_type = normalize_backend_type(
        _service_attr(service, "meter_backend_type"),
        DEFAULT_COMBINED_METER_TYPE,
    )
    raw_switch_type = normalize_backend_type(
        _service_attr(service, "switch_backend_type"),
        DEFAULT_COMBINED_SWITCH_TYPE,
    )
    charger_type = normalize_optional_backend_type(_service_attr(service, "charger_backend_type"))
    _validate_legacy_backend_values(mode, raw_meter_type, raw_switch_type, charger_type)
    return _build_runtime_summary(
        backend_mode=mode,
        meter_type=_runtime_meter_role_from_normalized_legacy(mode, raw_meter_type),
        meter_config_path=normalize_config_path(_service_attr(service, "meter_backend_config_path")),
        switch_type=_runtime_switch_role_from_normalized_legacy(mode, raw_switch_type),
        switch_config_path=normalize_config_path(_service_attr(service, "switch_backend_config_path")),
        charger_type=charger_type,
        charger_config_path=normalize_config_path(_service_attr(service, "charger_backend_config_path")),
        legacy_host=_service_attr(service, "host"),
    )


def _default_runtime_summary(service: Any) -> BackendRuntimeSummary:
    """Return the default combined runtime summary for a minimally shaped service."""
    return _build_runtime_summary(
        backend_mode="combined",
        meter_type=DEFAULT_COMBINED_METER_TYPE,
        meter_config_path=None,
        switch_type=DEFAULT_COMBINED_SWITCH_TYPE,
        switch_config_path=None,
        charger_type=None,
        charger_config_path=None,
        legacy_host=_service_attr(service, "host"),
    )


def _service_attr(service: Any, attribute_name: str) -> object:
    """Return one optional attribute value from a service-like object."""
    return getattr(service, attribute_name, None)


def _runtime_meter_role_from_normalized_legacy(mode: BackendMode, normalized: str) -> str | None:
    """Return the runtime meter role from an already-normalized legacy label."""
    return _runtime_role_from_normalized_legacy(mode, DEFAULT_COMBINED_METER_TYPE, normalized)


def _runtime_switch_role_from_normalized_legacy(mode: BackendMode, normalized: str) -> str | None:
    """Return the runtime switch role from an already-normalized legacy label."""
    return _runtime_role_from_normalized_legacy(mode, DEFAULT_COMBINED_SWITCH_TYPE, normalized)


def _runtime_role_from_normalized_legacy(mode: BackendMode, combined_fallback: str, normalized: str) -> str | None:
    """Return one runtime role from an already-normalized legacy backend label."""
    if _split_none_role(mode, normalized):
        return None
    return _runtime_role_alias(combined_fallback, normalized)
