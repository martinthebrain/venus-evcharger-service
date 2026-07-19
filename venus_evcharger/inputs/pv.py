# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import time
from typing import TypeGuard

from venus_evcharger.core.shared import (
    discovery_cache_valid,
    prefixed_service_names,
)
from venus_evcharger.dbus_gateway import PV_POWER_READ_KEY
from venus_evcharger.inputs.gateway_read import GatewayReadPort, SourceHealthPort
from venus_evcharger.ports.dbus import DbusInputReaderPort


def _service_name_or_none(value: object) -> str | None:
    """Return one valid DBus service name from cached discovery data."""
    if not isinstance(value, str):
        return None
    service_name = value.strip()
    return service_name or None


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _service_name_list(value: object) -> list[str] | None:
    """Return a non-empty list of service names from cached discovery data."""
    if not _is_object_list(value):
        return None
    service_names: list[str] = []
    for item in value:
        service_name = _service_name_or_none(item)
        if service_name is not None:
            service_names.append(service_name)
    return service_names or None


class PvInputReader:
    """Resolve and read PV input through the semantic gateway contract."""

    def __init__(self, port: DbusInputReaderPort, gateway: GatewayReadPort, health: SourceHealthPort) -> None:
        self._port = port
        self._gateway = gateway
        self._health = health

    def invalidate_auto_pv_services(self) -> None:
        """Clear cached PV service discovery so the next read performs a fresh scan."""
        svc = self._port.service
        svc._resolved_auto_pv_services = []
        svc._auto_pv_last_scan = 0.0

    def resolve_auto_pv_services(self) -> list[str]:
        """Resolve AC PV services (or use explicit override) for Auto mode."""
        svc = self._port.service
        if svc.auto_pv_service:
            return [svc.auto_pv_service]

        now = time.time()
        cached_services = _service_name_list(svc._resolved_auto_pv_services)
        if cached_services is not None and discovery_cache_valid(
            cached_services,
            svc._auto_pv_last_scan,
            svc.auto_pv_scan_interval_seconds,
            now,
        ):
            return cached_services

        service_names = prefixed_service_names(
            self._gateway.list_dbus_services(),
            svc.auto_pv_service_prefix,
            max_services=svc.auto_pv_max_services,
            sort_names=True,
        )
        svc._resolved_auto_pv_services = service_names
        svc._auto_pv_last_scan = now
        if not svc._resolved_auto_pv_services:
            raise ValueError(f"No DBus service found with prefix '{svc.auto_pv_service_prefix}'")
        return service_names

    def get_pv_power(self) -> float | None:
        """Return summed PV power from all discovered AC/DC PV sources."""
        svc = self._port.service
        now = time.time()
        if not self._health.retry_ready("pv", now):
            return None
        semantic_value = self._gateway.read_semantic_value(PV_POWER_READ_KEY, reason="main semantic PV power read")
        if semantic_value is not None:
            svc._last_pv_missing_warning = None
            self._health.recovered("pv", "PV readings recovered")
            return float(semantic_value)
        self._health.failed(
            "pv",
            now,
            "pv-missing",
            svc.auto_pv_scan_interval_seconds,
            "Auto mode could not read PV power from the DBus gateway read contract.",
        )
        return None
