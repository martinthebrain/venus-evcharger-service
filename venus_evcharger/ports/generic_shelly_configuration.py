# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral configuration contract for generic Shelly devices."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

GenericShellySelectorKind = Literal["ip", "mac"]

_MAC_SEPARATORS = re.compile(r"[:.\-\s]")
_CANONICAL_MAC = re.compile(r"[0-9A-F]{12}")


@dataclass(frozen=True, slots=True)
class GenericShellyDeviceSelector:
    """Canonical identity used to match exactly one generic Shelly device."""

    kind: GenericShellySelectorKind
    value: str

    def __post_init__(self) -> None:
        normalized = _normalized_selector_value(self.kind, self.value)
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class DisableMatchingGenericShellyOnceRequest:
    """Persistent one-shot disable intent for one channel of a matched device."""

    selector: GenericShellyDeviceSelector
    channel: int

    def __post_init__(self) -> None:
        if not isinstance(self.selector, GenericShellyDeviceSelector):
            raise TypeError("generic Shelly selector must be a GenericShellyDeviceSelector")
        if type(self.channel) is not int or self.channel < 1:
            raise ValueError("generic Shelly channel must be a positive integer")


@dataclass(frozen=True, slots=True)
class GenericShellyConfigurationReceipt:
    """Immediate acceptance result for an asynchronous configuration operation."""

    accepted: bool
    command_id: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        _validate_receipt_types(self.accepted, self.command_id, self.reason)
        command_id = self.command_id.strip()
        reason = self.reason.strip()
        if self.accepted:
            _validate_accepted_receipt(command_id, reason)
        else:
            _validate_rejected_receipt(command_id, reason)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "reason", reason)


@runtime_checkable
class GenericShellyConfigurationPort(Protocol):  # pragma: no cover
    """Device-configuration intent accepted by a gateway implementation."""

    def disable_matching_device_channel_once(
        self,
        request: DisableMatchingGenericShellyOnceRequest,
    ) -> GenericShellyConfigurationReceipt: ...


def generic_shelly_device_selector(
    *,
    target_ip: str,
    target_mac: str,
) -> GenericShellyDeviceSelector:
    """Select by IP when configured, otherwise by MAC."""
    normalized_ip = target_ip.strip()
    if normalized_ip:
        return GenericShellyDeviceSelector("ip", normalized_ip)
    normalized_mac = target_mac.strip()
    if normalized_mac:
        return GenericShellyDeviceSelector("mac", normalized_mac)
    raise ValueError("generic Shelly target requires an IP or MAC address")


def normalize_mac_address(value: str) -> str:
    """Return a canonical twelve-digit uppercase MAC address."""
    if not isinstance(value, str):
        raise TypeError("generic Shelly MAC selector must be a string")
    normalized = _MAC_SEPARATORS.sub("", value).upper()
    if _CANONICAL_MAC.fullmatch(normalized) is None:
        raise ValueError("generic Shelly MAC selector must contain twelve hexadecimal digits")
    return normalized


def _normalized_selector_value(kind: GenericShellySelectorKind, value: str) -> str:
    if kind == "ip":
        return _normalized_ip_address(value)
    if kind == "mac":
        return normalize_mac_address(value)
    raise ValueError("generic Shelly selector kind must be ip or mac")


def _normalized_ip_address(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("generic Shelly IP selector must be a string")
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as error:
        raise ValueError("generic Shelly IP selector must be a valid IP address") from error


def _validate_accepted_receipt(command_id: str, reason: str) -> None:
    if not command_id:
        raise ValueError("an accepted operation requires a command_id")
    if reason:
        raise ValueError("an accepted operation cannot carry a rejection reason")


def _validate_receipt_types(accepted: object, command_id: object, reason: object) -> None:
    if type(accepted) is not bool:
        raise TypeError("accepted must be bool")
    if not isinstance(command_id, str) or not isinstance(reason, str):
        raise TypeError("command_id and reason must be strings")


def _validate_rejected_receipt(command_id: str, reason: str) -> None:
    if command_id:
        raise ValueError("a rejected operation cannot carry a command_id")
    if not reason:
        raise ValueError("a rejected operation requires a reason")
