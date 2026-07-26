# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical configuration boundary for every gateway IPC path."""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping

from venus_evcharger.dbus_gateway_core import GatewayPaths, gateway_paths

__all__ = [
    "DBUS_GATEWAY_CACHE_PATH_KEY",
    "DBUS_GATEWAY_COMMAND_DIR_KEY",
    "DBUS_GATEWAY_CORE_COMMAND_DIR_KEY",
    "DBUS_GATEWAY_HEALTH_PATH_KEY",
    "DBUS_GATEWAY_RUN_DIR_KEY",
    "DBUS_GATEWAY_SOCKET_PATH_KEY",
    "GatewayPathConfigSource",
    "GatewayPaths",
    "configured_gateway_paths",
    "load_configured_gateway_paths",
]

DBUS_GATEWAY_RUN_DIR_KEY = "DbusGatewayRunDir"
DBUS_GATEWAY_CACHE_PATH_KEY = "DbusGatewayCachePath"
DBUS_GATEWAY_HEALTH_PATH_KEY = "DbusGatewayHealthPath"
DBUS_GATEWAY_SOCKET_PATH_KEY = "DbusGatewaySocketPath"
DBUS_GATEWAY_COMMAND_DIR_KEY = "DbusGatewayCommandDir"
DBUS_GATEWAY_CORE_COMMAND_DIR_KEY = "DbusGatewayCoreCommandDir"

GatewayPathConfigSource = configparser.ConfigParser | configparser.SectionProxy | Mapping[str, object]


def configured_gateway_paths(
    source: GatewayPathConfigSource,
    *,
    run_dir_override: str | None = None,
) -> GatewayPaths:
    """Derive one validated path contract from config or normalized values."""
    if run_dir_override is not None:
        normalized_override = _absolute_path(
            run_dir_override.strip(),
            DBUS_GATEWAY_RUN_DIR_KEY,
        )
        return gateway_paths(normalized_override)

    values = _normalized_values(_config_values(source))
    configured_run_dir = _optional_value(values, DBUS_GATEWAY_RUN_DIR_KEY)
    effective_run_dir = gateway_paths().run_dir if configured_run_dir is None else configured_run_dir
    normalized_run_dir = _absolute_path(
        effective_run_dir,
        DBUS_GATEWAY_RUN_DIR_KEY,
    )
    paths = gateway_paths(normalized_run_dir)
    return GatewayPaths(
        run_dir=paths.run_dir,
        socket_path=_configured_path(
            values,
            DBUS_GATEWAY_SOCKET_PATH_KEY,
            paths.socket_path,
        ),
        cache_path=_configured_path(
            values,
            DBUS_GATEWAY_CACHE_PATH_KEY,
            paths.cache_path,
        ),
        cache_sequence_path=paths.cache_sequence_path,
        health_path=_configured_path(
            values,
            DBUS_GATEWAY_HEALTH_PATH_KEY,
            paths.health_path,
        ),
        energy_inputs_path=paths.energy_inputs_path,
        energy_topology_path=paths.energy_topology_path,
        command_dir=_configured_path(
            values,
            DBUS_GATEWAY_COMMAND_DIR_KEY,
            paths.command_dir,
        ),
        core_command_dir=_configured_path(
            values,
            DBUS_GATEWAY_CORE_COMMAND_DIR_KEY,
            paths.core_command_dir,
        ),
    )


def load_configured_gateway_paths(
    config_path: str,
    *,
    run_dir_override: str | None = None,
) -> GatewayPaths:
    """Read one config file and derive its canonical gateway path contract."""
    parser = configparser.ConfigParser()
    loaded = parser.read(config_path)
    if not loaded:
        raise ValueError(f"Unable to read config file: {config_path}")
    return configured_gateway_paths(parser, run_dir_override=run_dir_override)


def _config_values(source: GatewayPathConfigSource) -> Mapping[str, object]:
    if isinstance(source, configparser.ConfigParser):
        return source["DEFAULT"]
    return source


def _normalized_values(source: Mapping[str, object]) -> dict[str, object]:
    return {str(key).casefold(): value for key, value in source.items()}


def _optional_value(values: Mapping[str, object], key: str) -> str | None:
    value = values.get(key.casefold())
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _configured_path(
    values: Mapping[str, object],
    key: str,
    default: str,
) -> str:
    configured = _optional_value(values, key)
    if configured is None:
        return default
    return _absolute_path(configured, key)


def _absolute_path(value: str, key: str) -> str:
    if not os.path.isabs(value):
        raise ValueError(f"{key} must be an absolute path: {value}")
    return os.path.normpath(value)
