# SPDX-License-Identifier: GPL-3.0-or-later
"""Local service identity helpers for bootstrap."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class ServiceIdentitySource(Protocol):
    """Inputs required to build the transport-independent EVCS identity."""

    config: Mapping[str, object]
    custom_name_override: str
    deviceinstance: int


def apply_service_identity(
    service: object,
    *,
    read_version: Callable[[str], str],
) -> None:
    """Apply a stable EVCS identity without consulting external devices."""
    if not isinstance(service, ServiceIdentitySource):
        raise TypeError("bootstrap service does not implement ServiceIdentitySource")
    defaults = _config_defaults(service.config)
    product_name = _text(defaults, "ProductName", "Venus EV Charger Service")
    setattr(service, "product_name", product_name)
    setattr(service, "custom_name", service.custom_name_override or product_name)
    serial = _text(defaults, "ServiceSerial", f"venus-evcharger-{service.deviceinstance}")
    setattr(service, "serial", serial)
    setattr(service, "firmware_version", read_version("version.txt"))
    setattr(service, "hardware_version", _text(defaults, "HardwareVersion", "Virtual EV charger"))


def _config_defaults(config: Mapping[str, object]) -> Mapping[str, object]:
    try:
        defaults = config["DEFAULT"]
    except KeyError as error:
        raise TypeError("bootstrap config DEFAULT section is not a mapping") from error
    if not isinstance(defaults, Mapping):
        raise TypeError("bootstrap config DEFAULT section is not a mapping")
    return defaults


def _text(values: Mapping[str, object], key: str, fallback: str) -> str:
    value = str(values.get(key, fallback)).strip()
    return value or fallback


__all__ = ["ServiceIdentitySource", "apply_service_identity"]
