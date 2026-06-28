# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-resolution helpers for auto-input DBus sources."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.core.shared import coerce_dbus_numeric, discovery_cache_valid, first_matching_prefixed_service
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.helper.sources_dbus_common import (
    DBUS_SOURCE_READ_ERRORS,
    _ResolvedAutoBatteryServiceState,
)


def _service_name_or_none(value: object) -> str | None:
    if value is None:
        return None
    service_name = str(value).strip()
    return service_name or None


def _required_service_name(value: object, label: str) -> str:
    service_name = _service_name_or_none(value)
    if service_name is None:
        raise ValueError(f"No DBus service resolved for {label}")
    return service_name


class _AutoInputHelperSourceDbusResolveMixin(_ResolvedAutoBatteryServiceState):
    def _resolve_auto_battery_service(self: Any) -> str:
        now = time.time()
        configured: object = self._configured_auto_battery_service(now)
        if (service_name := _service_name_or_none(configured)) is not None:
            return service_name
        cached: object = self._cached_auto_battery_service(now)
        if (service_name := _service_name_or_none(cached)) is not None:
            return service_name
        discovered: object = self._discovered_auto_battery_service(now)
        return _required_service_name(discovered, "auto battery")

    def _configured_auto_battery_service(self: Any, now: float) -> str | None:
        source = self._primary_energy_source()
        if not source.service_name:
            return None
        if not self._dbus_service_name_available(source.service_name):
            return None
        try:
            readable = self._energy_source_has_readable_data(source, source.service_name)
        except DBUS_SOURCE_READ_ERRORS:
            return None
        if readable:
            self._cache_energy_service(source.source_id, source.service_name, now, primary=True)
            return str(self._resolved_auto_battery_service)
        return None

    def _cached_auto_battery_service(self: Any, now: float) -> str | None:
        if discovery_cache_valid(
            self._resolved_auto_battery_service,
            self._auto_battery_last_scan,
            self.auto_battery_scan_interval_seconds,
            now,
        ):
            return str(self._resolved_auto_battery_service)
        return None

    def _discovered_auto_battery_service(self: Any, now: float) -> str:
        source = self._primary_energy_source()
        battery_service_prefix = str(getattr(self, "auto_battery_service_prefix", "") or "")
        service_name = first_matching_prefixed_service(
            self._list_dbus_services(),
            source.service_prefix or battery_service_prefix,
            self._battery_service_has_soc,
        )
        if service_name is None:
            raise ValueError(f"No DBus service found with prefix '{source.service_prefix or battery_service_prefix}'")
        self._cache_energy_service(source.source_id, service_name, now, primary=True)
        return service_name

    def _cache_energy_service(self: Any, source_id: str, service_name: str, now: float, *, primary: bool = False) -> None:
        if not isinstance(getattr(self, "_resolved_auto_energy_services", None), dict):
            self._resolved_auto_energy_services = {}
        if not isinstance(getattr(self, "_auto_energy_last_scan", None), dict):
            self._auto_energy_last_scan = {}
        self._resolved_auto_energy_services[source_id] = service_name
        self._auto_energy_last_scan[source_id] = now
        if primary:
            self._resolved_auto_battery_service = service_name
            self._auto_battery_last_scan = now

    def _cached_energy_service(self: Any, source_id: str, now: float) -> str | None:
        resolved = getattr(self, "_resolved_auto_energy_services", {})
        scans = getattr(self, "_auto_energy_last_scan", {})
        cached_service = resolved.get(source_id) if isinstance(resolved, dict) else None
        cached_at = scans.get(source_id, 0.0) if isinstance(scans, dict) else 0.0
        if discovery_cache_valid(cached_service, cached_at, self.auto_battery_scan_interval_seconds, now):
            return _service_name_or_none(cached_service)
        return None

    def _configured_energy_source_service(self: Any, source: EnergySourceDefinition, now: float) -> str | None:
        if not source.service_name or not self._dbus_service_name_available(source.service_name):
            return None
        if not self._energy_source_has_readable_data(source, source.service_name):
            return None
        self._cache_energy_service(source.source_id, source.service_name, now)
        return source.service_name

    def _discovered_energy_source_service(self: Any, source: EnergySourceDefinition, now: float) -> str:
        if not source.service_prefix:
            raise ValueError(f"No readable DBus service configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self._list_dbus_services(),
            source.service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is None:
            raise ValueError(f"No DBus service found for energy source '{source.source_id}'")
        self._cache_energy_service(source.source_id, service_name, now)
        return service_name

    def _resolve_energy_source_service(self: Any, source: EnergySourceDefinition) -> str:
        now = time.time()
        if source.source_id == self._primary_energy_source().source_id:
            primary_service: object = self._resolve_auto_battery_service()
            return _required_service_name(primary_service, "primary energy source")
        configured_service: object = self._configured_energy_source_service(source, now)
        if (service_name := _service_name_or_none(configured_service)) is not None:
            return service_name
        cached_service: object = self._cached_energy_service(source.source_id, now)
        if (service_name := _service_name_or_none(cached_service)) is not None:
            return service_name
        discovered_service: object = self._discovered_energy_source_service(source, now)
        return _required_service_name(discovered_service, source.source_id)

    def _read_optional_energy_value(self: Any, service_name: str, path: str) -> float | None:
        if not path:
            return None
        numeric_value = coerce_dbus_numeric(self._get_dbus_value(service_name, path))
        if isinstance(numeric_value, bool) or not isinstance(numeric_value, (int, float)):
            return None
        return float(numeric_value)

    def _read_optional_energy_text(self: Any, service_name: str, path: str) -> str:
        if not path:
            return ""
        value = self._get_dbus_value(service_name, path)
        return "" if value is None else str(value).strip()
