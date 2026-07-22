# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned concrete service identities for semantic publications."""

from __future__ import annotations

import configparser
import re
import zlib
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.process.config import configured_device_instance
from venus_evcharger.ports.gateway_publication import CompanionServiceIdentity, CompanionServiceKind

_AGGREGATE_SERVICE_IDS = {
    "aggregate-battery": "battery",
    "aggregate-pv": "pv_inverter",
    "aggregate-grid": "grid",
}
_KIND_TOKEN = {
    "battery": "battery",
    "pv_inverter": "pvinverter",
    "grid": "grid",
}
_AGGREGATE_NAME_KEYS = {
    "battery": "CompanionBatteryServiceName",
    "pv_inverter": "CompanionPvInverterServiceName",
    "grid": "CompanionGridServiceName",
}
_AGGREGATE_INSTANCE_KEYS = {
    "battery": "CompanionBatteryDeviceInstance",
    "pv_inverter": "CompanionPvInverterDeviceInstance",
    "grid": "CompanionGridDeviceInstance",
}
_SOURCE_PREFIX_KEYS = {
    "battery": "CompanionSourceBatteryServicePrefix",
    "pv_inverter": "CompanionSourcePvInverterServicePrefix",
    "grid": "CompanionSourceGridServicePrefix",
}
_SOURCE_INSTANCE_KEYS = {
    "battery": "CompanionSourceBatteryDeviceInstanceBase",
    "pv_inverter": "CompanionSourcePvInverterDeviceInstanceBase",
    "grid": "CompanionSourceGridDeviceInstanceBase",
}
_AGGREGATE_OFFSETS = {"battery": 40, "pv_inverter": 41, "grid": 42}
_SOURCE_OFFSETS = {"battery": 140, "pv_inverter": 240, "grid": 340}


@dataclass(frozen=True, slots=True)
class ConcreteServiceIdentity:
    """Concrete DBus identity derived only inside the adapter boundary."""

    service_name: str
    device_instance: int


def companion_concrete_identity(
    defaults: configparser.SectionProxy,
    identity: CompanionServiceIdentity,
) -> ConcreteServiceIdentity:
    """Resolve an opaque companion id to one deterministic Venus identity."""
    if _AGGREGATE_SERVICE_IDS.get(identity.service_id) == identity.kind:
        return _aggregate_identity(defaults, identity.kind)
    return _source_identity(defaults, identity.kind, identity.service_id)


def _aggregate_identity(
    defaults: configparser.SectionProxy,
    kind: CompanionServiceKind,
) -> ConcreteServiceIdentity:
    base_instance = configured_device_instance(defaults)
    device_instance = _configured_int(
        defaults,
        _AGGREGATE_INSTANCE_KEYS[kind],
        base_instance + _AGGREGATE_OFFSETS[kind],
    )
    fallback = f"com.victronenergy.{_KIND_TOKEN[kind]}.external_{device_instance}"
    return ConcreteServiceIdentity(
        service_name=_configured_text(defaults, _AGGREGATE_NAME_KEYS[kind], fallback),
        device_instance=device_instance,
    )


def _source_identity(
    defaults: configparser.SectionProxy,
    kind: CompanionServiceKind,
    service_id: str,
) -> ConcreteServiceIdentity:
    base_instance = configured_device_instance(defaults)
    configured_base = _configured_int(
        defaults,
        _SOURCE_INSTANCE_KEYS[kind],
        base_instance + _SOURCE_OFFSETS[kind],
    )
    digest = zlib.crc32(service_id.encode("utf-8")) & 0xFFFFFFFF
    device_instance = configured_base + digest % 90
    default_prefix = f"com.victronenergy.{_KIND_TOKEN[kind]}.external"
    prefix = _configured_text(defaults, _SOURCE_PREFIX_KEYS[kind], default_prefix).rstrip(".")
    suffix = _sanitized_service_id(service_id)
    return ConcreteServiceIdentity(
        service_name=f"{prefix}.{suffix}_{digest:08x}",
        device_instance=device_instance,
    )


def _configured_text(defaults: configparser.SectionProxy, key: str, fallback: str) -> str:
    value = str(defaults.get(key, fallback)).strip()
    return value or fallback


def _configured_int(defaults: configparser.SectionProxy, key: str, fallback: int) -> int:
    value = str(defaults.get(key, str(fallback))).strip()
    try:
        return int(value)
    except ValueError:
        return fallback


def _sanitized_service_id(service_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", service_id).strip("_").lower()
    return normalized[:48] or "source"


__all__ = ["ConcreteServiceIdentity", "companion_concrete_identity"]
