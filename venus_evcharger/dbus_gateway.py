# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-gateway IPC primitives used by non-DBus EV charger processes.

This facade intentionally does not import ``dbus`` or ``vedbus``. It re-exports
the file/socket protocol used between the core service and the dedicated DBus
adapter process, plus a small ``VeDbusService``-like proxy for the core.
"""

from __future__ import annotations

import socket
from pathlib import Path

from venus_evcharger.dbus_gateway_cache import DbusCacheStore
from venus_evcharger.dbus_gateway_client import GatewayClient, GatewayDbusServiceProxy, gateway_value
from venus_evcharger.dbus_gateway_commands import DbusCommandInbox
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    DEFAULT_CORE_COMMAND_DIR_NAME,
    DEFAULT_DBUS_CACHE_NAME,
    DEFAULT_DBUS_CACHE_SEQUENCE_NAME,
    DEFAULT_DBUS_COMMAND_DIR_NAME,
    DEFAULT_DBUS_HEALTH_NAME,
    DEFAULT_GATEWAY_RUN_DIR,
    DEFAULT_GATEWAY_SOCKET_NAME,
    FAST_READ_KEYS,
    GUI_CRITICAL_PUBLISH_PATHS,
    PRIORITY_VALUES,
    PUBLISH_PATH_RANKS,
    GatewayPaths,
    dbus_path_key,
    gateway_paths,
    read_json_file,
    write_json_file,
)
from venus_evcharger.dbus_gateway_latency import LatencyWindow
from venus_evcharger.dbus_gateway_policy import command_allowed_by_backpressure, command_queue_class

__all__ = [
    "DBUS_GATEWAY_SCHEMA_VERSION",
    "DEFAULT_CORE_COMMAND_DIR_NAME",
    "DEFAULT_DBUS_CACHE_NAME",
    "DEFAULT_DBUS_CACHE_SEQUENCE_NAME",
    "DEFAULT_DBUS_COMMAND_DIR_NAME",
    "DEFAULT_DBUS_HEALTH_NAME",
    "DEFAULT_GATEWAY_RUN_DIR",
    "DEFAULT_GATEWAY_SOCKET_NAME",
    "FAST_READ_KEYS",
    "GUI_CRITICAL_PUBLISH_PATHS",
    "PRIORITY_VALUES",
    "PUBLISH_PATH_RANKS",
    "DbusCacheStore",
    "DbusCommandInbox",
    "GatewayClient",
    "GatewayDbusServiceProxy",
    "GatewayPaths",
    "LatencyWindow",
    "Path",
    "command_allowed_by_backpressure",
    "command_queue_class",
    "dbus_path_key",
    "gateway_paths",
    "gateway_value",
    "read_json_file",
    "socket",
    "write_json_file",
]
