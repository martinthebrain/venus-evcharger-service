# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-gateway IPC primitives used by non-DBus EV charger processes.

This facade intentionally does not import ``dbus`` or ``vedbus``. It re-exports
the file/socket protocol used between the core service and the dedicated DBus
adapter process, plus a small ``VeDbusService``-like proxy for the core.
"""

from __future__ import annotations

import socket
from pathlib import Path

from venus_evcharger.dbus_gateway_cache import CacheValueMetadata, DbusCacheStore
from venus_evcharger.dbus_gateway_client import (
    GatewayClient,
    GatewayDbusServiceProxy,
    gateway_read_value,
    gateway_value,
)
from venus_evcharger.dbus_gateway_commands import DbusCommandInbox
from venus_evcharger.dbus_gateway_core import (
    BATTERY_SOC_READ_KEY,
    DBUS_GATEWAY_SCHEMA_VERSION,
    DEFAULT_CORE_COMMAND_DIR_NAME,
    DEFAULT_DBUS_CACHE_NAME,
    DEFAULT_DBUS_CACHE_SEQUENCE_NAME,
    DEFAULT_DBUS_COMMAND_DIR_NAME,
    DEFAULT_DBUS_HEALTH_NAME,
    DEFAULT_GATEWAY_RUN_DIR,
    DEFAULT_GATEWAY_SOCKET_NAME,
    FAST_READ_KEYS,
    GRID_POWER_READ_KEY,
    GUI_CRITICAL_PUBLISH_PATHS,
    PRIORITY_VALUES,
    PUBLISH_PATH_RANKS,
    PV_POWER_READ_KEY,
    CacheFreshnessKind,
    GatewayPaths,
    GatewayReadKey,
    dbus_path_key,
    gateway_paths,
    read_json_file,
    require_gateway_read_key,
    write_json_file,
)
from venus_evcharger.dbus_gateway_latency import LatencyWindow
from venus_evcharger.dbus_gateway_policy import command_allowed_by_backpressure, command_queue_class
from venus_evcharger.dbus_gateway_surface import (
    EVCS_ENERGY_TIME_FIELDS,
    EVCS_FIELD_TO_PATH,
    EVCS_LIVE_MEASUREMENT_FIELDS,
    EVCS_PATH_TO_FIELD,
    VENUS_EV_CHARGER_REQUIRED_CONTRACTS,
    VENUS_EV_CHARGER_WRITABLE_PATHS,
    VenusDbusPathContract,
    evcs_fields_to_paths,
    evcs_path_freshness_kind,
    evcs_path_to_field,
    mismatched_venus_writeability,
    missing_required_venus_paths,
    venus_path_writeable,
)

__all__ = [
    "BATTERY_SOC_READ_KEY",
    "DBUS_GATEWAY_SCHEMA_VERSION",
    "DEFAULT_CORE_COMMAND_DIR_NAME",
    "DEFAULT_DBUS_CACHE_NAME",
    "DEFAULT_DBUS_CACHE_SEQUENCE_NAME",
    "DEFAULT_DBUS_COMMAND_DIR_NAME",
    "DEFAULT_DBUS_HEALTH_NAME",
    "DEFAULT_GATEWAY_RUN_DIR",
    "DEFAULT_GATEWAY_SOCKET_NAME",
    "EVCS_ENERGY_TIME_FIELDS",
    "EVCS_FIELD_TO_PATH",
    "EVCS_LIVE_MEASUREMENT_FIELDS",
    "EVCS_PATH_TO_FIELD",
    "FAST_READ_KEYS",
    "GRID_POWER_READ_KEY",
    "GUI_CRITICAL_PUBLISH_PATHS",
    "PRIORITY_VALUES",
    "PUBLISH_PATH_RANKS",
    "PV_POWER_READ_KEY",
    "VENUS_EV_CHARGER_REQUIRED_CONTRACTS",
    "VENUS_EV_CHARGER_WRITABLE_PATHS",
    "CacheFreshnessKind",
    "CacheValueMetadata",
    "DbusCacheStore",
    "DbusCommandInbox",
    "GatewayClient",
    "GatewayDbusServiceProxy",
    "GatewayPaths",
    "GatewayReadKey",
    "LatencyWindow",
    "Path",
    "VenusDbusPathContract",
    "command_allowed_by_backpressure",
    "command_queue_class",
    "dbus_path_key",
    "evcs_fields_to_paths",
    "evcs_path_freshness_kind",
    "evcs_path_to_field",
    "gateway_paths",
    "gateway_read_value",
    "gateway_value",
    "mismatched_venus_writeability",
    "missing_required_venus_paths",
    "read_json_file",
    "require_gateway_read_key",
    "socket",
    "venus_path_writeable",
    "write_json_file",
]
