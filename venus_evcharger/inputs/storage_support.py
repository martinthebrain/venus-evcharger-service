# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for DBus input storage."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from venus_evcharger.core.shared import (
    discovery_cache_valid,
    first_matching_prefixed_service,
)
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS
from venus_evcharger.inputs.gateway_read import GatewayReadPort
from venus_evcharger.ports.dbus import DbusInputReaderPort, DbusInputService


def _service_name_or_none(value: object) -> str | None:
    """Return a valid cached DBus service name, or None for malformed values."""
    if not isinstance(value, str):
        return None
    service_name = value.strip()
    return service_name or None


def _energy_cache_entry(owner: DbusInputService, source_id: str) -> tuple[str, float] | None:
    service_name = _service_name_or_none(owner._resolved_auto_energy_services.get(source_id))
    if service_name is None:
        return None
    cached_scan_at = owner._auto_energy_last_scan.get(source_id)
    if cached_scan_at is None or isinstance(cached_scan_at, bool):
        return None
    return service_name, float(cached_scan_at)


def _matching_cached_service(
    owner: DbusInputService,
    source_id: str,
    expected_service: str | None,
) -> str | None:
    cached_service = _service_name_or_none(owner._resolved_auto_energy_services.get(source_id))
    if cached_service is None:
        return None
    if expected_service is None:
        return cached_service
    return cached_service if _service_name_or_none(expected_service) == cached_service else None


class EnergyServicePort(Protocol):
    """Service-resolution operations consumed by storage reads."""

    def resolve_energy_source_service(self, source: EnergySourceDefinition) -> str: ...
    def primary_energy_source(self) -> EnergySourceDefinition: ...
    def invalidate_auto_battery_service(self) -> None: ...
    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool: ...
    def introspection_says_skip(self, service_name: str, path: str, *, priority: int) -> bool: ...


class EnergyServiceResolver:
    """Resolve battery and inverter services through gateway-backed discovery."""

    def __init__(self, port: DbusInputReaderPort, gateway: GatewayReadPort) -> None:
        self._port = port
        self._gateway = gateway

    def _configured_primary_energy_sources(self) -> tuple[EnergySourceDefinition, ...]:
        return self._port.service.auto_energy_sources

    def _default_primary_energy_source(self) -> EnergySourceDefinition:
        svc = self._port.service
        return EnergySourceDefinition(
            source_id="primary_battery",
            service_name=svc.auto_battery_service,
            service_prefix=svc.auto_battery_service_prefix,
            soc_path=svc.auto_battery_soc_path,
            usable_capacity_wh=svc.auto_battery_capacity_wh,
            battery_power_path=svc.auto_battery_power_path,
            ac_power_path=svc.auto_battery_ac_power_path,
            pv_power_path=svc.auto_battery_pv_power_path,
            grid_interaction_path=svc.auto_battery_grid_interaction_path,
            operating_mode_path=svc.auto_battery_operating_mode_path,
        )

    def primary_energy_source(self) -> EnergySourceDefinition:
        sources = self._configured_primary_energy_sources()
        if sources:
            return sources[0]
        return self._default_primary_energy_source()

    def _battery_service_has_soc(self, service_name: str) -> bool:
        try:
            if self.introspection_says_skip(service_name, self._port.service.auto_battery_soc_path, priority=80):
                return False
            soc_value = self._gateway.get_dbus_value(service_name, self._port.service.auto_battery_soc_path)
        except DBUS_INPUT_READ_ERRORS:
            self._request_introspection(
                service_name,
                self._port.service.auto_battery_soc_path,
                priority=95,
                reason="battery SOC probe failed",
            )
            return False
        return soc_value is not None

    def _energy_service_has_readable_field(self, service_name: str, path: str) -> bool:
        if not path:
            return False
        if self.introspection_says_skip(service_name, path, priority=85):
            return False
        try:
            return self._gateway.get_dbus_value(service_name, path) is not None
        except DBUS_INPUT_READ_ERRORS:
            self._request_introspection(service_name, path, priority=95, reason="energy-source field probe failed")
            return False

    def _energy_source_has_readable_data(self, source: EnergySourceDefinition, service_name: str) -> bool:
        return any(
            self._energy_service_has_readable_field(service_name, path)
            for path in (
                source.soc_path,
                source.battery_power_path,
                source.ac_power_path,
                source.pv_power_path,
                source.grid_interaction_path,
                source.operating_mode_path,
            )
        )

    def _resolve_battery_service_override(self) -> str | None:
        source = self.primary_energy_source()
        if not source.service_name:
            return None
        if self._battery_service_has_soc(source.service_name) or self._energy_source_has_readable_data(source, source.service_name):
            return source.service_name
        logging.debug(
            "Auto battery service override %s missing SOC, falling back to prefix scan.",
            source.service_name,
        )
        return None

    def _cached_auto_battery_service(self, now: float) -> str | None:
        svc = self._port.service
        cached_service = _service_name_or_none(svc._resolved_auto_battery_service)
        if cached_service is None:
            return None
        cached_scan_at = svc._auto_battery_last_scan
        if not discovery_cache_valid(cached_service, cached_scan_at, svc.auto_battery_scan_interval_seconds, now):
            return None
        return cached_service

    def _energy_cache_valid(self, source_id: str, now: float) -> str | None:
        svc = self._port.service
        cache_entry = _energy_cache_entry(svc, source_id)
        if cache_entry is None:
            return None
        service_name, cached_scan_at = cache_entry
        if not discovery_cache_valid(service_name, cached_scan_at, svc.auto_battery_scan_interval_seconds, now):
            return None
        return service_name

    def _remember_energy_service(self, source_id: str, service_name: str, now: float) -> str:
        svc = self._port.service
        svc._resolved_auto_energy_services[source_id] = service_name
        svc._auto_energy_last_scan[source_id] = now
        return service_name

    def _primary_energy_source_service(self) -> str:
        return self.resolve_auto_battery_service()

    def _configured_energy_source_service(self, source: EnergySourceDefinition, now: float) -> str | None:
        if not source.service_name or not self._energy_source_has_readable_data(source, source.service_name):
            return None
        return self._remember_energy_service(source.source_id, source.service_name, now)

    def _discovered_energy_source_service(self, source: EnergySourceDefinition, now: float) -> str:
        if not source.service_prefix:
            raise ValueError(f"No readable DBus service configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self._gateway.list_dbus_services(),
            source.service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is None:
            raise ValueError(f"No DBus service found for energy source '{source.source_id}'")
        return self._remember_energy_service(source.source_id, service_name, now)

    def resolve_energy_source_service(self, source: EnergySourceDefinition) -> str:
        now = time.time()
        if source.source_id == self.primary_energy_source().source_id:
            return self._primary_energy_source_service()
        configured_service = self._configured_energy_source_service(source, now)
        if configured_service is not None:
            return configured_service
        cached_service = self._energy_cache_valid(source.source_id, now)
        if cached_service is not None:
            if self._energy_source_has_readable_data(source, cached_service):
                return cached_service
            self.invalidate_energy_source_service(
                source.source_id,
                expected_service=cached_service,
            )
        return self._discovered_energy_source_service(source, now)

    def _scan_auto_battery_service(self, now: float) -> str:
        svc = self._port.service
        source = self.primary_energy_source()
        service_prefix = source.service_prefix or svc.auto_battery_service_prefix
        if not service_prefix:
            raise ValueError(f"No DBus service prefix configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self._gateway.list_dbus_services(),
            service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is not None:
            svc._resolved_auto_battery_service = service_name
            svc._auto_battery_last_scan = now
            self._remember_energy_service(source.source_id, service_name, now)
            logging.debug("Auto battery service resolved: %s", svc._resolved_auto_battery_service)
            return service_name
        raise ValueError(f"No DBus service found with prefix '{service_prefix}'")

    def resolve_auto_battery_service(self) -> str:
        override_service = self._resolve_battery_service_override()
        if override_service is not None:
            return override_service
        now = time.time()
        cached_service = self._cached_auto_battery_service(now)
        if cached_service is not None:
            return cached_service
        return self._scan_auto_battery_service(now)

    def invalidate_auto_battery_service(self) -> None:
        svc = self._port.service
        source_id = self.primary_energy_source().source_id
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0
        svc._resolved_auto_energy_services.pop(source_id, None)
        svc._auto_energy_last_scan.pop(source_id, None)

    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool:
        """Forget one unchanged discovery result and its scan timestamp."""
        svc = self._port.service
        cached_service = _matching_cached_service(svc, source_id, expected_service)
        if cached_service is None:
            return False
        svc._resolved_auto_energy_services.pop(source_id, None)
        svc._auto_energy_last_scan.pop(source_id, None)
        self._invalidate_primary_cache_if_matching(source_id, cached_service)
        return True

    def _invalidate_primary_cache_if_matching(self, source_id: str, cached_service: str) -> None:
        svc = self._port.service
        if source_id != self.primary_energy_source().source_id:
            return
        if _service_name_or_none(svc._resolved_auto_battery_service) != cached_service:
            return
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0

    def introspection_says_skip(self, service_name: str, path: str, *, priority: int) -> bool:
        skip, reason = self._port.path_unusable(service_name, path)
        if skip:
            logging.debug("Skipping %s %s from DBus introspection cache: %s", service_name, path, reason)
            self._request_introspection(service_name, path, priority=priority, reason="known-unusable input path")
        return bool(skip)

    def _request_introspection(self, service_name: str, path: str, *, priority: int, reason: str) -> None:
        self._port.request_introspection(
            service_name,
            path,
            priority=priority,
            reason=reason,
            source="evcharger-inputs",
        )
