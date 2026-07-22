#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Request one persistent disable of a matching generic Shelly channel."""

from __future__ import annotations

import configparser
import logging
import os
import sys
import time
from collections.abc import Sequence
from typing import Literal, TypedDict

from venus_evcharger.ops.disable_generic_shelly_config import (
    ALLOW_PERSISTENT_DISABLE_KEY,
    CHANNEL_KEY,
    DELAY_SECONDS_KEY,
    ENABLED_KEY,
    HOST_KEY,
    TARGET_IP_KEY,
    TARGET_MAC_KEY,
)
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyConfigurationPort,
    generic_shelly_device_selector,
    normalize_mac_address,
)

_CONFIG_SCALAR_TYPES = (str, bytes, bytearray, int, float)
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")),
    "deploy",
    "venus",
    "config.venus_evcharger.ini",
)

GENERIC_SHELLY_HELPER_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    configparser.Error,
)

DisableShellyRunResult = Literal[
    "accepted",
    "rejected",
    "disabled-by-config",
    "persistent-disable-blocked",
    "no-target",
]


class DisableShellySettings(TypedDict):
    """Normalized settings consumed by the semantic one-shot workflow."""

    enabled: bool
    allow_persistent_disable: bool
    target_ip: str
    target_mac: str
    channel: int
    delay_seconds: float


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: object, default: int) -> int:
    try:
        if isinstance(value, _CONFIG_SCALAR_TYPES):
            return int(value)
        return int(str(value))
    except (TypeError, ValueError):
        return int(default)


def _as_float(value: object, default: float) -> float:
    try:
        if isinstance(value, _CONFIG_SCALAR_TYPES):
            return float(value)
        return float(str(value))
    except (TypeError, ValueError):
        return float(default)


def _normalize_mac(value: object) -> str:
    raw = str(value or "").strip()
    return normalize_mac_address(raw) if raw else ""


def load_settings(config_path: str) -> DisableShellySettings:
    """Load and normalize the matching intent from the wallbox config."""
    parser = configparser.ConfigParser()
    loaded = parser.read(config_path)
    if not loaded or "DEFAULT" not in parser:
        raise ValueError(f"Unable to read config file: {config_path}")

    section = parser["DEFAULT"]
    host = _required_host(section)
    return {
        "enabled": _as_bool(section.get(ENABLED_KEY), True),
        "allow_persistent_disable": _as_bool(section.get(ALLOW_PERSISTENT_DISABLE_KEY), True),
        "target_ip": section.get(TARGET_IP_KEY, host).strip(),
        "target_mac": _normalize_mac(section.get(TARGET_MAC_KEY)),
        "channel": _normalized_channel(section.get(CHANNEL_KEY)),
        "delay_seconds": _normalized_delay_seconds(section.get(DELAY_SECONDS_KEY)),
    }


def _required_host(section: configparser.SectionProxy) -> str:
    host = section.get(HOST_KEY, "").strip()
    if not host:
        raise ValueError("DEFAULT Host is required in the config")
    return host


def _normalized_channel(value: object) -> int:
    return max(1, _as_int(value, 1))


def _normalized_delay_seconds(value: object) -> float:
    return max(0.0, _as_float(value, 180.0))


def matches_device(
    serial: object,
    ip_value: object,
    mac_value: object,
    target_ip: str,
    target_mac: str,
) -> bool:
    """Pure reference matcher retained for adapter contract tests."""
    if target_ip:
        return _matches_ip(ip_value, target_ip)
    if target_mac:
        return _matches_mac(serial, mac_value, target_mac)
    return False


def _matches_ip(ip_value: object, target_ip: str) -> bool:
    return str(ip_value or "").strip() == target_ip


def _matches_mac(serial: object, mac_value: object, target_mac: str) -> bool:
    candidate = str(mac_value or serial or "")
    try:
        return normalize_mac_address(candidate) == normalize_mac_address(target_mac)
    except (TypeError, ValueError):
        return False


def _precondition_result(settings: DisableShellySettings) -> DisableShellyRunResult | None:
    if not settings["enabled"]:
        logging.info("Generic Shelly one-shot helper disabled by config")
        return "disabled-by-config"
    if not settings["allow_persistent_disable"]:
        logging.info("Generic Shelly one-shot helper blocked by config")
        return "persistent-disable-blocked"
    if not settings["target_ip"] and not settings["target_mac"]:
        logging.warning("Generic Shelly one-shot helper has no target IP or MAC configured")
        return "no-target"
    return None


def run_once(
    config_path: str = DEFAULT_CONFIG_PATH,
    *,
    configuration_port: GenericShellyConfigurationPort,
) -> DisableShellyRunResult:
    """Submit one semantic request and report only asynchronous acceptance."""
    settings = load_settings(config_path)
    precondition = _precondition_result(settings)
    if precondition is not None:
        return precondition

    delay_seconds = settings["delay_seconds"]
    if delay_seconds > 0.0:
        logging.info("Waiting %.0f seconds before generic Shelly one-shot request", delay_seconds)
        time.sleep(delay_seconds)

    selector = generic_shelly_device_selector(
        target_ip=settings["target_ip"],
        target_mac=settings["target_mac"],
    )
    request = DisableMatchingGenericShellyOnceRequest(selector, settings["channel"])
    receipt = configuration_port.disable_matching_device_channel_once(request)
    if receipt.accepted:
        logging.info("Gateway accepted generic Shelly disable request %s", receipt.command_id)
        return "accepted"
    logging.warning("Gateway rejected generic Shelly disable request: %s", receipt.reason)
    return "rejected"


def main(
    argv: Sequence[str] | None = None,
    *,
    configuration_port: GenericShellyConfigurationPort | None = None,
) -> int:
    """CLI entry point; production composition must provide the semantic port."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    config_path = arguments[0] if arguments else DEFAULT_CONFIG_PATH
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if configuration_port is None:
        logging.error("Generic Shelly configuration port is not configured")
        return 1
    return _run_main(config_path, configuration_port)


def _run_main(config_path: str, configuration_port: GenericShellyConfigurationPort) -> int:
    try:
        result = run_once(config_path, configuration_port=configuration_port)
    except GENERIC_SHELLY_HELPER_ERRORS as error:
        logging.exception("Generic Shelly one-shot helper failed: %s", error)
        return 1
    logging.info("Generic Shelly one-shot helper finished: %s", result)
    return 1 if result == "rejected" else 0
