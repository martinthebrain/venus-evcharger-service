# SPDX-License-Identifier: GPL-3.0-or-later
"""Device metadata helpers for service bootstrap."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from venus_evcharger.bootstrap.errors import BOOTSTRAP_DEVICE_INFO_ERRORS


def topology_configured(svc: Any) -> bool:
    """Return whether one service has a configured runtime topology."""
    if hasattr(svc, "topology_configured"):
        return bool(svc.topology_configured)
    if hasattr(svc, "host_configured"):
        return bool(svc.host_configured)
    return False


def primary_rpc_configured(svc: Any) -> bool:
    """Return whether one service still has a direct legacy RPC endpoint."""
    if hasattr(svc, "primary_rpc_configured"):
        return bool(svc.primary_rpc_configured)
    if hasattr(svc, "host_configured"):
        return bool(svc.host_configured)
    return False


def device_info_payload(payload: object) -> dict[str, Any]:
    """Return one plain device-info dict from an RPC payload."""
    return dict(payload) if isinstance(payload, Mapping) else {}


def fetch_device_info_with_fallback(svc: Any, sleep_func: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Try to fetch Shelly device info, but start with generic metadata if that fails."""
    last_error = None
    attempts = svc.startup_device_info_retries + 1
    sleep = time.sleep if sleep_func is None else sleep_func
    for attempt in range(attempts):
        try:
            return device_info_payload(svc.fetch_rpc("Shelly.GetDeviceInfo"))
        except BOOTSTRAP_DEVICE_INFO_ERRORS as error:
            last_error = error
            if _should_retry_device_info(attempt, attempts, svc.startup_device_info_retry_seconds):
                logging.warning(
                    "Shelly.GetDeviceInfo failed during startup (attempt %s/%s): %s",
                    attempt + 1,
                    attempts,
                    error,
                )
                sleep(svc.startup_device_info_retry_seconds)
    logging.warning(
        "Shelly.GetDeviceInfo unavailable during startup, continuing with generic metadata: %s",
        last_error,
    )
    return {}


def _should_retry_device_info(attempt: int, attempts: int, retry_seconds: float) -> bool:
    return attempt < (attempts - 1) and retry_seconds > 0


def apply_device_metadata(
    svc: Any,
    *,
    read_version: Callable[[str], str],
    fetch_device_info: Callable[[], dict[str, Any]],
) -> None:
    """Fetch Shelly metadata and apply UI-facing identity fields."""
    defaults = svc.config["DEFAULT"]
    svc.product_name = defaults.get("ProductName", "Venus EV Charger Service").strip()
    if not topology_configured(svc):
        apply_unconfigured_device_metadata(svc, read_version=read_version)
        return
    if not primary_rpc_configured(svc):
        apply_adapter_topology_device_metadata(svc, read_version=read_version)
        return
    apply_rpc_device_metadata(svc, read_version=read_version, fetch_device_info=fetch_device_info)


def apply_unconfigured_device_metadata(svc: Any, *, read_version: Callable[[str], str]) -> None:
    """Apply device metadata for an unconfigured topology."""
    svc.custom_name = svc.custom_name_override or "Venus EV Charger Service"
    svc.serial = f"unconfigured-{svc.deviceinstance}"
    svc.firmware_version = read_version("version.txt")
    svc.hardware_version = "Not configured"
    logging.info("No load topology is configured yet; starting without device metadata")


def apply_adapter_topology_device_metadata(svc: Any, *, read_version: Callable[[str], str]) -> None:
    """Apply generic metadata for adapter-only topologies without direct RPC."""
    svc.custom_name = svc.custom_name_override or "Venus EV Charger Service"
    svc.serial = f"topology-{svc.deviceinstance}"
    svc.firmware_version = read_version("version.txt")
    svc.hardware_version = "External adapter topology"
    logging.info("No direct legacy RPC endpoint is configured; starting with generic device metadata")


def apply_rpc_device_metadata(
    svc: Any,
    *,
    read_version: Callable[[str], str],
    fetch_device_info: Callable[[], dict[str, Any]],
) -> None:
    """Apply metadata fetched from the direct device RPC endpoint."""
    device_info = fetch_device_info()
    svc.custom_name = svc.custom_name_override or device_info.get("name") or "Venus EV Charger Service"
    svc.serial = device_info.get("mac", svc.host.replace(".", ""))
    svc.firmware_version = device_info.get("fw_id", read_version("version.txt"))
    svc.hardware_version = device_info.get("model", "Shelly 1PM Gen4")
