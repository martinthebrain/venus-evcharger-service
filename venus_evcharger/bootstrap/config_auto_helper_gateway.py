# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway and introspection config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value


DBUS_GATEWAY_RUN_DIR_KEY = "DbusGatewayRunDir"
DBUS_GATEWAY_CACHE_PATH_KEY = "DbusGatewayCachePath"
DBUS_GATEWAY_HEALTH_PATH_KEY = "DbusGatewayHealthPath"
DBUS_GATEWAY_SOCKET_PATH_KEY = "DbusGatewaySocketPath"
DBUS_GATEWAY_COMMAND_DIR_KEY = "DbusGatewayCommandDir"
DBUS_GATEWAY_CORE_COMMAND_DIR_KEY = "DbusGatewayCoreCommandDir"
DBUS_GATEWAY_MAX_AGE_KEY = "DbusGatewayMaxAgeSeconds"
DBUS_INTROSPECTION_ENABLED_KEY = "DbusIntrospectionEnabled"
DBUS_INTROSPECTION_SNAPSHOT_PATH_KEY = "DbusIntrospectionSnapshotPath"
DBUS_INTROSPECTION_REQUEST_PATH_KEY = "DbusIntrospectionRequestPath"
DBUS_INTROSPECTION_MAX_AGE_KEY = "DbusIntrospectionMaxAgeSeconds"


def _enabled(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _default_gateway_path(svc: Any, filename: str) -> str:
    return f"{svc.dbus_gateway_run_dir}/{filename}"


def load_gateway_path_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    svc.dbus_gateway_run_dir = defaults.get(DBUS_GATEWAY_RUN_DIR_KEY, "/run/venus-evcharger").strip()
    svc.dbus_gateway_cache_path = defaults.get(
        DBUS_GATEWAY_CACHE_PATH_KEY,
        _default_gateway_path(svc, "dbus-cache.json"),
    ).strip()
    svc.dbus_gateway_health_path = defaults.get(
        DBUS_GATEWAY_HEALTH_PATH_KEY,
        _default_gateway_path(svc, "dbus-health.json"),
    ).strip()
    svc.dbus_gateway_socket_path = defaults.get(
        DBUS_GATEWAY_SOCKET_PATH_KEY,
        _default_gateway_path(svc, "gateway.sock"),
    ).strip()
    svc.dbus_gateway_command_dir = defaults.get(
        DBUS_GATEWAY_COMMAND_DIR_KEY,
        _default_gateway_path(svc, "dbus-commands"),
    ).strip()
    svc.dbus_gateway_core_command_dir = defaults.get(
        DBUS_GATEWAY_CORE_COMMAND_DIR_KEY,
        _default_gateway_path(svc, "core-commands"),
    ).strip()
    svc.dbus_gateway_max_age_seconds = max(
        0.0,
        float(_config_value(defaults, DBUS_GATEWAY_MAX_AGE_KEY, 10.0)),
    )


def load_introspection_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    svc.dbus_introspection_enabled = _enabled(_config_value(defaults, DBUS_INTROSPECTION_ENABLED_KEY, "1"))
    svc.dbus_introspection_snapshot_path = defaults.get(
        DBUS_INTROSPECTION_SNAPSHOT_PATH_KEY,
        f"/run/dbus-venus-evcharger-dbus-map-{svc.deviceinstance}.json",
    ).strip()
    svc.dbus_introspection_request_path = defaults.get(
        DBUS_INTROSPECTION_REQUEST_PATH_KEY,
        f"/run/dbus-venus-evcharger-dbus-map-requests-{svc.deviceinstance}.json",
    ).strip()
    svc.dbus_introspection_max_age_seconds = float(_config_value(defaults, DBUS_INTROSPECTION_MAX_AGE_KEY, 900))


def load_gateway_and_introspection_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    load_gateway_path_config(svc, defaults)
    load_introspection_config(svc, defaults)
