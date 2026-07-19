# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-summary selection helpers for service-like objects."""

from __future__ import annotations

import configparser

from venus_evcharger.topology.schema import EvChargerTopologyConfig

from .config_loader import load_runtime_backend_summary
from .config_topology import _runtime_summary_from_topology
from .models import BackendRuntimeSummary


def runtime_summary_from_service(service: object) -> BackendRuntimeSummary:
    """Return one normalized runtime summary from the service's current truth source."""
    runtime = runtime_backend_summary_from_service(service)
    if runtime is not None:
        return runtime
    configured_runtime = configured_runtime_summary_from_service(service)
    if configured_runtime is not None:
        return configured_runtime
    topology = topology_from_service_runtime(service)
    if topology is not None:
        return _runtime_summary_from_topology(topology)
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return load_runtime_backend_summary(service_config)
    raise ValueError("service does not expose canonical backend configuration")


def runtime_backend_summary_from_service(service: object) -> BackendRuntimeSummary | None:
    """Return one runtime-facing backend summary already attached to the service."""
    bundle = getattr(service, "_backend_bundle", None)
    runtime = getattr(bundle, "runtime", None)
    return runtime if isinstance(runtime, BackendRuntimeSummary) else None


def configured_runtime_summary_from_service(service: object) -> BackendRuntimeSummary | None:
    """Return the backend summary normalized during bootstrap configuration."""
    runtime = getattr(service, "_backend_runtime_summary", None)
    return runtime if isinstance(runtime, BackendRuntimeSummary) else None


def topology_from_service_runtime(service: object) -> EvChargerTopologyConfig | None:
    """Return one normalized topology config already attached to the service."""
    topology = getattr(service, "_topology_config", None)
    return topology if isinstance(topology, EvChargerTopologyConfig) else None
