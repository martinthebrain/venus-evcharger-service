# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-facing backend mode and role label helpers."""

from __future__ import annotations

import configparser

from venus_evcharger.topology.config import parse_topology_config

from .config_loader import load_runtime_backend_summary, topology_sections_present
from .config_normalization import _normalized_text_or_default
from .config_service_runtime import (
    configured_runtime_summary_from_service,
    runtime_backend_summary_from_service,
    topology_from_service_runtime,
)
from .config_topology import _runtime_summary_from_topology, _topology_backend_label
from .models import BackendRuntimeSummary


def backend_mode_for_service(service: object, default: str = "combined") -> str:
    """Return one outward backend mode preferring resolved runtime state."""
    runtime = runtime_backend_summary_from_service(service)
    if runtime is not None:
        return runtime.backend_mode
    configured_runtime = configured_runtime_summary_from_service(service)
    if configured_runtime is not None:
        return configured_runtime.backend_mode
    topology = topology_from_service_runtime(service)
    if topology is not None:
        return _runtime_summary_from_topology(topology).backend_mode
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return load_runtime_backend_summary(service_config).backend_mode
    return default


def backend_type_for_service(service: object, role: str, default: str = "") -> str:
    """Return one outward backend type preferring resolved runtime state."""
    normalized_role = role.strip().lower()
    runtime = runtime_backend_summary_from_service(service)
    if runtime is not None:
        return summary_role_value(runtime, normalized_role, default)
    configured_runtime = configured_runtime_summary_from_service(service)
    if configured_runtime is not None:
        return summary_role_value(configured_runtime, normalized_role, default)
    topology = topology_from_service_runtime(service)
    if topology is not None:
        return label_or_default(_topology_backend_label(topology, normalized_role), default)
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return type_from_service_config(service_config, normalized_role, default)
    return default


def summary_role_value(summary: BackendRuntimeSummary, role: str, default: str) -> str:
    """Return one normalized backend role value from a runtime summary."""
    values = {
        "meter": summary.meter_type,
        "switch": summary.switch_type,
        "charger": summary.charger_type,
    }
    return label_or_default(values.get(role), default)


def label_or_default(label: object, default: str) -> str:
    """Return trimmed label text or the provided default."""
    if label is None:
        return default
    return _normalized_text_or_default(label, default)


def type_from_service_config(
    service_config: configparser.ConfigParser,
    role: str,
    default: str,
) -> str:
    """Return one backend type value derived from service config state."""
    if topology_sections_present(service_config):
        return label_or_default(
            _topology_backend_label(parse_topology_config(service_config), role),
            default,
        )
    return summary_role_value(load_runtime_backend_summary(service_config), role, default)
