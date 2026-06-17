# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-resolution helpers for auto-input DBus sources."""

from __future__ import annotations

import time
from typing import Any, cast

from venus_evcharger.core.shared import discovery_cache_valid, first_matching_prefixed_service
from venus_evcharger.energy import EnergySourceDefinition


class _AutoInputHelperSourceDbusResolveMixin:
    def _resolve_auto_battery_service(self: Any) -> str:
        now = time.time()
        resolved = (
            self._configured_auto_battery_service(now)
            or self._cached_auto_battery_service(now)
            or self._discovered_auto_battery_service(now)
        )
        return cast(str, resolved)

    def _configured_auto_battery_service(self: Any, now: float) -> str | None:
        source = self._primary_energy_source()
        if not source.service_name:
            return None
        if not self._dbus_service_name_available(source.service_name):
            return None
        try:
            if self._energy_source_has_readable_data(source, source.service_name):
                self._cache_energy_service(source.source_id, source.service_name, now, primary=True)
                return str(self._resolved_auto_battery_service)
        except Exception:
            return None
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
        return cast(str, self._resolved_auto_battery_service)

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
            return cast(str | None, cached_service)
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
            return cast(str, self._resolve_auto_battery_service())
        configured_service = cast(str | None, self._configured_energy_source_service(source, now))
        if configured_service is not None:
            return configured_service
        cached_service = cast(str | None, self._cached_energy_service(source.source_id, now))
        if cached_service is not None:
            return cached_service
        return cast(str, self._discovered_energy_source_service(source, now))

    def _read_optional_energy_value(self: Any, service_name: str, path: str) -> float | None:
        if not path:
            return None
        return cast(float | None, self._battery_soc_numeric(self._get_dbus_value(service_name, path)))

    def _read_optional_energy_text(self: Any, service_name: str, path: str) -> str:
        if not path:
            return ""
        value = self._get_dbus_value(service_name, path)
        return "" if value is None else str(value).strip()

