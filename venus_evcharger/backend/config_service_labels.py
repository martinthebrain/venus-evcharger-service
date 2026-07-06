# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-facing backend mode and role label helpers."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.topology.config import parse_topology_config

from .config_loader import load_runtime_backend_summary, topology_sections_present
from .config_normalization import _normalized_text_or_default, _role_field_name
from .config_service_runtime import runtime_backend_summary_from_service, topology_from_service_runtime
from .config_topology import _runtime_summary_from_topology, _topology_backend_label
from .models import BackendRuntimeSummary


def backend_mode_for_service(service: Any, default: str = "combined") -> str:
    """Return one outward backend mode preferring resolved runtime state."""
    runtime = runtime_backend_summary_from_service(service)
    if runtime is not None:
        return label_or_default(getattr(runtime, "backend_mode", None), default)
    topology = topology_from_service_runtime(service)
    if topology is not None:
        return _runtime_summary_from_topology(topology).backend_mode
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return load_runtime_backend_summary(service_config).backend_mode
    return label_or_default(getattr(service, "backend_mode", None), default)


def backend_type_for_service(service: Any, role: str, default: str = "") -> str:
    """Return one outward backend type preferring resolved runtime state."""
    normalized_role = role.strip().lower()
    runtime = runtime_backend_summary_from_service(service)
    if runtime is not None:
        return summary_role_value(runtime, normalized_role, default)
    topology = topology_from_service_runtime(service)
    if topology is not None:
        return label_or_default(_topology_backend_label(topology, normalized_role), default)
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return type_from_service_config(service_config, normalized_role, default)
    return legacy_service_role_value(service, normalized_role, default)


def summary_role_value(summary: BackendRuntimeSummary, role: str, default: str) -> str:
    """Return one normalized backend role value from a runtime summary."""
    return label_or_default(getattr(summary, _role_field_name(role), None), default)


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


def legacy_service_role_value(service: Any, role: str, default: str) -> str:
    """Return one backend role value from explicit legacy service attrs."""
    attribute_name = f"{role}_backend_type"
    return label_or_default(getattr(service, attribute_name, None), default)
