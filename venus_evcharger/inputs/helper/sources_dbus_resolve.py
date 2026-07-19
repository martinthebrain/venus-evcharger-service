# SPDX-License-Identifier: GPL-3.0-or-later
"""Energy-service resolution through the gateway service cache."""

from __future__ import annotations

import time

from venus_evcharger.core.shared import discovery_cache_valid, first_matching_prefixed_service
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import EnergySourceCatalogPort, GatewayReaderPort


class EnergyServiceResolver:
    """Resolve configured or discovered services without direct DBus access."""

    def __init__(
        self,
        settings: AutoInputHelperSettings,
        gateway: GatewayReaderPort,
        catalog: EnergySourceCatalogPort,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.catalog = catalog
        self._resolved_primary: str | None = None
        self._primary_scan_at = 0.0
        self._resolved: dict[str, str] = {}
        self._scanned_at: dict[str, float] = {}

    def resolve(self, source: EnergySourceDefinition) -> str:
        if source.source_id == self.catalog.primary_source().source_id:
            return self._resolve_primary()
        now = time.time()
        configured = self._configured_service(source, now)
        if configured is not None:
            return configured
        cached = self._cached_service(source.source_id, now)
        if cached is not None:
            return cached
        return self._discover_service(source, now)

    def invalidate_primary(self) -> None:
        source_id = self.catalog.primary_source().source_id
        self._resolved_primary = None
        self._primary_scan_at = 0.0
        self._resolved.pop(source_id, None)
        self._scanned_at.pop(source_id, None)

    def _resolve_primary(self) -> str:
        now = time.time()
        source = self.catalog.primary_source()
        configured = self._configured_primary(source, now)
        if configured is not None:
            return configured
        cached = self._cached_primary(now)
        if cached is not None:
            return cached
        return self._discover_primary(source, now)

    def _cached_primary(self, now: float) -> str | None:
        if discovery_cache_valid(
            self._resolved_primary,
            self._primary_scan_at,
            self.settings.auto_battery_scan_interval_seconds,
            now,
        ):
            return self._resolved_primary
        return None

    def _discover_primary(self, source: EnergySourceDefinition, now: float) -> str:
        service_name = first_matching_prefixed_service(
            self.gateway.service_names(),
            source.service_prefix or self.catalog.primary_service_prefix(),
            self.catalog.battery_service_has_soc,
        )
        if service_name is None:
            prefix = source.service_prefix or self.catalog.primary_service_prefix()
            raise ValueError(f"No DBus service found with prefix '{prefix}'")
        self._cache(source.source_id, service_name, now, primary=True)
        return service_name

    def _configured_primary(self, source: EnergySourceDefinition, now: float) -> str | None:
        if not source.service_name or not self.gateway.service_available(source.service_name):
            return None
        try:
            readable = self.catalog.source_has_readable_data(source, source.service_name)
        except DBUS_INPUT_READ_ERRORS:
            return None
        if not readable:
            return None
        self._cache(source.source_id, source.service_name, now, primary=True)
        return source.service_name

    def _configured_service(self, source: EnergySourceDefinition, now: float) -> str | None:
        if not source.service_name or not self.gateway.service_available(source.service_name):
            return None
        if not self.catalog.source_has_readable_data(source, source.service_name):
            return None
        self._cache(source.source_id, source.service_name, now)
        return source.service_name

    def _cached_service(self, source_id: str, now: float) -> str | None:
        cached = self._resolved.get(source_id)
        cached_at = self._scanned_at.get(source_id, 0.0)
        if discovery_cache_valid(cached, cached_at, self.settings.auto_battery_scan_interval_seconds, now):
            return cached
        return None

    def _discover_service(self, source: EnergySourceDefinition, now: float) -> str:
        if not source.service_prefix:
            raise ValueError(f"No readable DBus service configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self.gateway.service_names(),
            source.service_prefix,
            lambda candidate: self.catalog.source_has_readable_data(source, candidate),
        )
        if service_name is None:
            raise ValueError(f"No DBus service found for energy source '{source.source_id}'")
        self._cache(source.source_id, service_name, now)
        return service_name

    def _cache(self, source_id: str, service_name: str, now: float, *, primary: bool = False) -> None:
        self._resolved[source_id] = service_name
        self._scanned_at[source_id] = now
        if primary:
            self._resolved_primary = service_name
            self._primary_scan_at = now
