# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus gateway constants and JSON/path helpers."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from venus_evcharger.core.shared import compact_json, write_text_atomically

DBUS_GATEWAY_SCHEMA_VERSION = 1
DEFAULT_GATEWAY_RUN_DIR = "/run/venus-evcharger"
DEFAULT_GATEWAY_SOCKET_NAME = "gateway.sock"
DEFAULT_DBUS_CACHE_NAME = "dbus-cache.json"
DEFAULT_DBUS_CACHE_SEQUENCE_NAME = "dbus-cache.seq"
DEFAULT_DBUS_HEALTH_NAME = "dbus-health.json"
DEFAULT_DBUS_COMMAND_DIR_NAME = "dbus-commands"
DEFAULT_CORE_COMMAND_DIR_NAME = "core-commands"

PRIORITY_VALUES = {
    "safety": 0,
    "user": 1,
    "publish": 2,
    "read": 3,
    "optional": 4,
    "discovery": 5,
    "diagnostic": 6,
}

PUBLISH_PATH_RANKS = {
    "/Mode": 0,
    "/StartStop": 0,
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

FAST_READ_KEYS = {"grid_power_w", "pv_power_w", "battery_soc"}

@dataclass(frozen=True)
class GatewayPaths:
    """Runtime paths shared by gateway, core, and observer processes."""

    run_dir: str
    socket_path: str
    cache_path: str
    cache_sequence_path: str
    health_path: str
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
        command_dir=os.path.join(base, DEFAULT_DBUS_COMMAND_DIR_NAME),
        core_command_dir=os.path.join(base, DEFAULT_CORE_COMMAND_DIR_NAME),
    )


def _now() -> float:
    return time.time()


def _priority_rank(priority: object) -> int:
    return PRIORITY_VALUES.get(str(priority or "diagnostic").strip().lower(), PRIORITY_VALUES["diagnostic"])


def _json_ready(value: Any) -> Any:
    if _is_json_scalar(value):
        return value
    if isinstance(value, Mapping):
        return _json_ready_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return str(value)


def _is_json_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _json_ready_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {str(key): _json_ready(item) for key, item in value.items()}


def dbus_path_key(service_name: str, path: str) -> str:
    """Return the canonical cache key for one raw Victron DBus path."""
    return f"path:{service_name!s}{path!s}"


def read_json_file(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_file(path: str, payload: Mapping[str, Any]) -> None:
    write_text_atomically(path, compact_json(_json_ready(payload)) + "\n")


def _float_or_zero(value: object) -> float:
    if isinstance(value, (str, bytes, int, float)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    try:
        method = getattr(value, "__float__")  # noqa: B009 - object protocol probe accepted by mypy
    except AttributeError:
        return 0.0
    try:
        return float(method())
    except (TypeError, ValueError):
        return 0.0
