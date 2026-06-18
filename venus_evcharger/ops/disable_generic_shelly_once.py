#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Disable a matching generic dbus-shelly channel once after boot.

This helper is optional. It is meant for installations where a generic
dbus-shelly service and the dedicated wallbox service would otherwise talk to
the same physical Shelly relay and fight each other.
"""

from collections.abc import Callable, Sequence
import configparser
import logging
import os
import sys
import time
import xml.etree.ElementTree as xml_et
from typing import Any, cast

from venus_evcharger.dbus_gateway import DbusCacheStore, GatewayClient, dbus_path_key, gateway_paths


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")),
    "deploy",
    "venus",
    "config.venus_evcharger.ini",
)
DEFAULT_GENERIC_SHELLY_SERVICE = "com.victronenergy.shelly"


def _as_bool(value: object, default: bool = False) -> bool:
    """Convert a config value to bool."""
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: object, default: int) -> int:
    """Convert a config value to int, falling back for invalid input."""
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return int(default)


def _as_float(value: object, default: float) -> float:
    """Convert a config value to float, falling back for invalid input."""
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return float(default)


def _normalize_mac(value: object) -> str:
    """Normalize MAC-like strings for comparison."""
    return str(value or "").replace(":", "").replace("-", "").replace(" ", "").strip().upper()


def load_settings(config_path: str) -> dict[str, Any]:
    """Load helper settings from the shared wallbox config."""
    parser = configparser.ConfigParser()
    loaded = parser.read(config_path)
    if not loaded or "DEFAULT" not in parser:
        raise ValueError(f"Unable to read config file: {config_path}")

    section = parser["DEFAULT"]
    host = _required_host(section)
    channel = _normalized_channel(section.get("GenericShellyDisableChannel", "1"))
    delay_seconds = _normalized_delay_seconds(section.get("GenericShellyDisableDelaySeconds", "180"))

    return {
        "enabled": _as_bool(section.get("DisableGenericShellyDevice", "1"), True),
        "allow_persistent_disable": _as_bool(
            section.get("GenericShellyAllowPersistentDisable", "1"),
            True,
        ),
        "service": section.get("GenericShellyService", DEFAULT_GENERIC_SHELLY_SERVICE).strip()
        or DEFAULT_GENERIC_SHELLY_SERVICE,
        "target_ip": section.get("GenericShellyDisableIp", host).strip(),
        "target_mac": _normalize_mac(section.get("GenericShellyDisableMac", "")),
        "channel": channel,
        "delay_seconds": delay_seconds,
        "gateway_run_dir": section.get("DbusGatewayRunDir", "/run/venus-evcharger").strip(),
        "gateway_cache_path": section.get("DbusGatewayCachePath", "").strip(),
    }


def _required_host(section: configparser.SectionProxy) -> str:
    """Return the required default Host from the shared config."""
    host = section.get("Host", "").strip()
    if not host:
        raise ValueError("DEFAULT Host is required in the config")
    return host


def _normalized_channel(value: object) -> int:
    """Return a valid generic Shelly channel number."""
    channel = _as_int(value, 1)
    return channel if channel >= 1 else 1


def _normalized_delay_seconds(value: object) -> float:
    """Return a non-negative startup delay for the one-shot helper."""
    delay_seconds = _as_float(value, 180.0)
    return delay_seconds if delay_seconds >= 0 else 0.0


def matches_device(
    serial: object,
    ip_value: object,
    mac_value: object,
    target_ip: str,
    target_mac: str,
) -> bool:
    """Return True if the generic Shelly entry matches the configured target."""
    if target_ip:
        return str(ip_value or "").strip() == target_ip
    if target_mac:
        return _normalize_mac(mac_value or serial) == _normalize_mac(target_mac)
    return False


def get_system_bus() -> Any:
    """Direct DBus connections are forbidden outside the gateway."""
    raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")


def _bus_item_interface(bus: Any, service_name: str, path: str) -> Any:
    del bus, service_name, path
    raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")


def get_dbus_value(bus: Any, service_name: str, path: str, timeout: float = 1.0) -> Any:
    """Read a DBus value from the gateway cache."""
    del timeout
    cache_path = str(bus)
    entry = DbusCacheStore.value_entry(DbusCacheStore.load_snapshot(cache_path), dbus_path_key(service_name, path))
    return None if entry is None else entry.get("value")


def set_dbus_value(
    bus: Any,
    service_name: str,
    path: str,
    value: Any,
    timeout: float = 1.0,
) -> Any:
    """Queue a DBus write through the gateway."""
    del timeout
    run_dir = str(bus)
    GatewayClient(gateway_paths(run_dir or None)).enqueue_command(
        {
            "kind": "set_value",
            "source": "disable-generic-shelly-once",
            "service": service_name,
            "path": path,
            "value": value,
            "priority": "user",
            "coalesce_key": f"{service_name}:{path}",
        }
    )
    return True


def get_dbus_child_nodes(bus: Any, service_name: str, path: str, timeout: float = 1.0) -> list[str]:
    """Return child nodes from gateway-owned introspection cache."""
    del timeout
    run_dir = str(bus)
    paths = gateway_paths(run_dir or None)
    snapshot = DbusCacheStore.load_snapshot(paths.cache_path)
    entry = DbusCacheStore.value_entry(snapshot, f"introspection:{service_name}:{path}")
    if entry is None:
        GatewayClient(paths).enqueue_command(
            {
                "kind": "introspect",
                "source": "disable-generic-shelly-once",
                "service": service_name,
                "path": path,
                "priority": "discovery",
                "coalesce_key": f"introspect:{service_name}:{path}",
            }
        )
        return []
    xml_data = entry.get("value", "")
    root = xml_et.fromstring(str(xml_data))
    child_nodes: list[str] = []
    for node in root.findall("node"):
        name = node.attrib.get("name")
        if name:
            child_nodes.append(name)
    return child_nodes


def disable_matching_device(
    settings: dict[str, Any],
    list_nodes: Callable[[str, str], list[str]],
    get_value: Callable[[str, str], Any],
    set_value: Callable[[str, str, Any], Any],
    logger: Any | None = None,
) -> str:
    """Disable the first matching generic Shelly device and return a status label."""
    resolved_logger: Any = logging if logger is None else logger
    precondition_result = _disable_precondition_result(settings, resolved_logger)
    if precondition_result is not None:
        return precondition_result

    service_name = settings["service"]
    for serial in list_nodes(service_name, "/Devices"):
        if not _device_matches_target(settings, service_name, serial, get_value):
            continue
        return _disable_device_channel(settings, service_name, serial, get_value, set_value, resolved_logger)

    target_ip = settings.get("target_ip", "")
    target_mac = settings.get("target_mac", "")
    resolved_logger.info("No matching generic Shelly device found for IP %s MAC %s", target_ip, target_mac)
    return "not-found"


def _disable_precondition_result(settings: dict[str, Any], logger: Any) -> str | None:
    """Return an early result when helper configuration forbids any disable action."""
    if not settings.get("enabled", False):
        logger.info("Generic Shelly one-shot helper disabled by config")
        return "disabled-by-config"
    if not settings.get("allow_persistent_disable", True):
        logger.info("Generic Shelly one-shot helper blocked by config")
        return "persistent-disable-blocked"
    if _has_no_disable_target(settings):
        logger.warning("Generic Shelly one-shot helper has no target IP or MAC configured")
        return "no-target"
    return None


def _has_no_disable_target(settings: dict[str, Any]) -> bool:
    """Return whether neither target IP nor target MAC is configured."""
    return not settings.get("target_ip", "") and not settings.get("target_mac", "")


def _device_matches_target(
    settings: dict[str, Any],
    service_name: str,
    serial: str,
    get_value: Callable[[str, str], Any],
) -> bool:
    """Return whether one generic Shelly device entry matches the configured target."""
    ip_value = get_value(service_name, f"/Devices/{serial}/Ip")
    mac_value = get_value(service_name, f"/Devices/{serial}/Mac")
    return matches_device(
        serial,
        ip_value,
        mac_value,
        settings.get("target_ip", ""),
        settings.get("target_mac", ""),
    )


def _enabled_path(settings: dict[str, Any], serial: str) -> str:
    """Return the Enabled DBus path for one generic Shelly device/channel."""
    return f"/Devices/{serial}/{settings['channel']}/Enabled"


def _device_already_disabled(
    service_name: str,
    enabled_path: str,
    get_value: Callable[[str, str], Any],
) -> bool:
    """Return whether the target generic Shelly channel is already disabled."""
    enabled_value = get_value(service_name, enabled_path)
    return int(enabled_value or 0) == 0


def _disable_device_channel(
    settings: dict[str, Any],
    service_name: str,
    serial: str,
    get_value: Callable[[str, str], Any],
    set_value: Callable[[str, str, Any], Any],
    logger: Any,
) -> str:
    """Disable one matched generic Shelly device/channel and return its outcome label."""
    enabled_path = _enabled_path(settings, serial)
    if _device_already_disabled(service_name, enabled_path, get_value):
        logger.info("Generic Shelly device %s already disabled on %s", serial, enabled_path)
        return "already-disabled"
    set_value(service_name, enabled_path, 0)
    logger.info("Disabled generic Shelly device %s on %s", serial, enabled_path)
    return "disabled"


def run_once(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Execute the delayed one-shot disable check."""
    settings = load_settings(config_path)
    delay_seconds = settings.get("delay_seconds", 0.0)
    if delay_seconds > 0:
        logging.info("Waiting %.0f seconds before generic Shelly one-shot check", delay_seconds)
        time.sleep(delay_seconds)

    run_dir = str(settings.get("gateway_run_dir") or "/run/venus-evcharger")
    cache_path = str(settings.get("gateway_cache_path") or gateway_paths(run_dir or None).cache_path)
    timeout = 1.0
    return disable_matching_device(
        settings,
        lambda service_name, path: get_dbus_child_nodes(run_dir, service_name, path, timeout=timeout),
        lambda service_name, path: get_dbus_value(cache_path, service_name, path, timeout=timeout),
        lambda service_name, path, value: set_dbus_value(run_dir, service_name, path, value, timeout=timeout),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = argv[0] if argv else DEFAULT_CONFIG_PATH
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = run_once(config_path)
    except Exception as error:  # pylint: disable=broad-except
        logging.exception("Generic Shelly one-shot helper failed: %s", error)
        return 1
    logging.info("Generic Shelly one-shot helper finished: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
