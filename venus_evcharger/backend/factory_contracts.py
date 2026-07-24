# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed boundaries used by the backend composition factory."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Protocol, TypeGuard, TypeVar

from venus_evcharger.topology.schema import EvChargerTopologyConfig

BackendT = TypeVar("BackendT")
BackendRoleCreator = Callable[[str, object, str], BackendT]


class BackendConfigSource(Protocol):
    """Service capability exposing canonical wallbox configuration."""

    config: configparser.ConfigParser


class BackendTopologySource(Protocol):
    """Service capability exposing a topology normalized during bootstrap."""

    _topology_config: EvChargerTopologyConfig


def is_backend_config_source(value: object) -> TypeGuard[BackendConfigSource]:
    """Return whether a boundary object exposes canonical wallbox config."""
    return isinstance(getattr(value, "config", None), configparser.ConfigParser)


def is_backend_topology_source(value: object) -> TypeGuard[BackendTopologySource]:
    """Return whether a boundary object exposes normalized topology config."""
    return isinstance(
        getattr(value, "_topology_config", None),
        EvChargerTopologyConfig,
    )


def config_from_backend_service(value: object) -> configparser.ConfigParser | None:
    """Return validated wallbox config from a service-like boundary object."""
    return value.config if is_backend_config_source(value) else None


def topology_from_backend_service(
    value: object,
) -> EvChargerTopologyConfig | None:
    """Return validated runtime topology from a service-like boundary object."""
    return value._topology_config if is_backend_topology_source(value) else None
