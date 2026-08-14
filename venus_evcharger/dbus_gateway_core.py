# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus gateway constants and JSON/path helpers."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, SupportsFloat, SupportsIndex, TypeGuard

from venus_evcharger.core.shared import compact_json, write_text_atomically

DBUS_GATEWAY_SCHEMA_VERSION = 1
DEFAULT_GATEWAY_RUN_DIR = "/run/venus-evcharger"
DEFAULT_GATEWAY_SOCKET_NAME = "gateway.sock"
DEFAULT_DBUS_CACHE_NAME = "dbus-cache.json"
DEFAULT_DBUS_CACHE_SEQUENCE_NAME = "dbus-cache.seq"
DEFAULT_DBUS_HEALTH_NAME = "dbus-health.json"
DEFAULT_ENERGY_INPUTS_NAME = "energy-inputs.v4.bin"
DEFAULT_ENERGY_TOPOLOGY_NAME = "energy-topology.json"
DEFAULT_DBUS_COMMAND_DIR_NAME = "dbus-commands"
DEFAULT_CORE_COMMAND_DIR_NAME = "core-commands"

PUBLISH_PATH_RANKS = {
    "/UpdateIndex": 0,
    "/Connected": 0,
    "/Mode": 0,
    "/StartStop": 0,
    "/Enable": 0,
    "/AutoStart": 0,
    "/SetCurrent": 0,
    "/Current": 0,
    "/Status": 0,
    "/Ac/Power": 0,
    "/Ac/Current": 0,
    "/Session/Time": 0,
    "/Session/Energy": 0,
    "/ChargingTime": 0,
    "/Ac/L1/Power": 0,
    "/Ac/L1/Current": 0,
    "/Ac/Energy/Forward": 2,
    "/Ac/L1/Energy/Forward": 2,
    "/Ac/L2/Power": 3,
    "/Ac/L2/Current": 3,
    "/Ac/L2/Energy/Forward": 3,
    "/Ac/L3/Power": 3,
    "/Ac/L3/Current": 3,
    "/Ac/L3/Energy/Forward": 3,
}

GUI_CRITICAL_PUBLISH_PATHS = {
    "/UpdateIndex",
    "/Connected",
    "/Mode",
    "/StartStop",
    "/Enable",
    "/AutoStart",
    "/Status",
    "/SetCurrent",
    "/Ac/Power",
    "/Ac/Current",
    "/Ac/Energy/Forward",
    "/Session/Time",
    "/Session/Energy",
}

CacheFreshnessKind = Literal["external_read", "local_owned", "static", "diagnostic"]
CacheSourceState = Literal["active", "unavailable", "error"]

@dataclass(frozen=True)
class GatewayPaths:
    """Runtime paths shared by gateway, core, and observer processes."""

    run_dir: str
    socket_path: str
    cache_path: str
    cache_sequence_path: str
    health_path: str
    energy_inputs_path: str
    energy_topology_path: str
    command_dir: str
    core_command_dir: str


def gateway_paths(run_dir: str | None = None) -> GatewayPaths:
    base = str(run_dir or os.environ.get("VENUS_EVCHARGER_GATEWAY_RUN_DIR") or DEFAULT_GATEWAY_RUN_DIR).strip()
    return GatewayPaths(
        run_dir=base,
        socket_path=os.path.join(base, DEFAULT_GATEWAY_SOCKET_NAME),
        cache_path=os.path.join(base, DEFAULT_DBUS_CACHE_NAME),
        cache_sequence_path=os.path.join(base, DEFAULT_DBUS_CACHE_SEQUENCE_NAME),
        health_path=os.path.join(base, DEFAULT_DBUS_HEALTH_NAME),
        energy_inputs_path=os.path.join(base, DEFAULT_ENERGY_INPUTS_NAME),
        energy_topology_path=os.path.join(base, DEFAULT_ENERGY_TOPOLOGY_NAME),
        command_dir=os.path.join(base, DEFAULT_DBUS_COMMAND_DIR_NAME),
        core_command_dir=os.path.join(base, DEFAULT_CORE_COMMAND_DIR_NAME),
    )


def _now() -> float:
    return time.time()


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Narrow an untrusted JSON value to a mapping with explicit object types."""
    return isinstance(value, Mapping)


def normalized_object_mapping(value: object) -> dict[str, object] | None:
    """Normalize an untrusted mapping to the gateway's string-key contract."""
    if not is_object_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def _is_object_sequence(value: object) -> TypeGuard[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def _json_ready(value: object) -> object:
    if _is_json_scalar(value):
        return value
    if is_object_mapping(value):
        return _json_ready_mapping(value)
    if _is_object_sequence(value):
        return [_json_ready(item) for item in value]
    return str(value)


def _is_json_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _json_ready_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return {str(key): _json_ready(item) for key, item in value.items()}


def dbus_path_key(service_name: str, path: str) -> str:
    """Return the canonical cache key for one raw Victron DBus path."""
    return f"path:{service_name!s}{path!s}"


def read_json_file(path: str, default: object = None) -> object:
    try:
        with open(path, encoding="utf-8") as handle:
            payload: object = json.load(handle)
            return payload
    except (OSError, json.JSONDecodeError):
        return default


def write_json_file(path: str, payload: Mapping[str, object]) -> None:
    write_text_atomically(path, compact_json(_json_ready(payload)) + "\n")


def float_or_default(value: object, default: float) -> float:
    if not isinstance(value, (str, bytes, SupportsFloat, SupportsIndex)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def float_or_zero(value: object) -> float:
    return float_or_default(value, 0.0)
