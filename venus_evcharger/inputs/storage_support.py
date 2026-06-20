# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for DBus input storage."""

from __future__ import annotations

import logging
import time
from typing import cast

from venus_evcharger.core.shared import (
    configured_grid_paths,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
)
from venus_evcharger.dbus_introspection import owner_path_unusable, request_owner_introspection
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _DbusInputStorageSupportMixin(_ComposableControllerMixin):
    def _configured_primary_energy_sources(self) -> tuple[EnergySourceDefinition, ...]:
        return tuple(getattr(self.service, "auto_energy_sources", ()) or ())

    @staticmethod
    def _primary_energy_source_id() -> str:
        return "primary_battery"

    @staticmethod
    def _primary_energy_source_role() -> str:
        return "battery"

    def _primary_energy_service_name(self) -> str:
        return str(getattr(self.service, "auto_battery_service", "") or "")

    def _primary_energy_service_prefix(self) -> str:
        return str(getattr(self.service, "auto_battery_service_prefix", "") or "")

    def _primary_energy_soc_path(self) -> str:
        return str(getattr(self.service, "auto_battery_soc_path", "/Soc") or "/Soc")

    def _primary_energy_capacity_wh(self) -> float | None:
        value = getattr(self.service, "auto_battery_capacity_wh", None)
        return float(value) if isinstance(value, (int, float)) else None

    def _primary_energy_battery_power_path(self) -> str:
        return str(getattr(self.service, "auto_battery_power_path", "") or "")

    def _primary_energy_ac_power_path(self) -> str:
        return str(getattr(self.service, "auto_battery_ac_power_path", "") or "")

    def _primary_energy_pv_power_path(self) -> str:
        return str(getattr(self.service, "auto_battery_pv_power_path", "") or "")

    def _primary_energy_grid_interaction_path(self) -> str:
        return str(getattr(self.service, "auto_battery_grid_interaction_path", "") or "")

    def _primary_energy_operating_mode_path(self) -> str:
        return str(getattr(self.service, "auto_battery_operating_mode_path", "") or "")

    def _default_primary_energy_source(self) -> EnergySourceDefinition:
        return EnergySourceDefinition(
            source_id=self._primary_energy_source_id(),
            role=self._primary_energy_source_role(),
            service_name=self._primary_energy_service_name(),
            service_prefix=self._primary_energy_service_prefix(),
            soc_path=self._primary_energy_soc_path(),
            usable_capacity_wh=self._primary_energy_capacity_wh(),
            battery_power_path=self._primary_energy_battery_power_path(),
            ac_power_path=self._primary_energy_ac_power_path(),
            pv_power_path=self._primary_energy_pv_power_path(),
            grid_interaction_path=self._primary_energy_grid_interaction_path(),
            operating_mode_path=self._primary_energy_operating_mode_path(),
        )

    def _primary_energy_source(self) -> EnergySourceDefinition:
        sources = self._configured_primary_energy_sources()
        if sources:
            return sources[0]
        return self._default_primary_energy_source()

    def _battery_service_has_soc(self, service_name: str) -> bool:
        try:
            if self._introspection_says_skip(service_name, self.service.auto_battery_soc_path, priority=80):
                return False
            soc_value = self.service.get_dbus_value(service_name, self.service.auto_battery_soc_path)
        except Exception:
            self._request_introspection(service_name, self.service.auto_battery_soc_path, priority=95, reason="battery SOC probe failed")
            return False
        return soc_value is not None

    def _energy_service_has_readable_field(self, service_name: str, path: str) -> bool:
        if not path:
            return False
        if self._introspection_says_skip(service_name, path, priority=85):
            return False
        try:
            return self.service.get_dbus_value(service_name, path) is not None
        except Exception:
            self._request_introspection(service_name, path, priority=95, reason="energy-source field probe failed")
            return False

    def _energy_source_has_readable_data(self, source: EnergySourceDefinition, service_name: str) -> bool:
        return any(
            (
                self._energy_service_has_readable_field(service_name, source.soc_path),
                self._energy_service_has_readable_field(service_name, source.battery_power_path),
                self._energy_service_has_readable_field(service_name, source.ac_power_path),
                self._energy_service_has_readable_field(service_name, source.pv_power_path),
                self._energy_service_has_readable_field(service_name, source.grid_interaction_path),
                self._energy_service_has_readable_field(service_name, source.operating_mode_path),
            )
        )

    def _resolve_battery_service_override(self) -> str | None:
        source = self._primary_energy_source()
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
        svc = self.service
        if discovery_cache_valid(
            svc._resolved_auto_battery_service,
            svc._auto_battery_last_scan,
            svc.auto_battery_scan_interval_seconds,
            now,
        ):
            return cast(str | None, svc._resolved_auto_battery_service)
        return None

    def _energy_cache_valid(self, source_id: str, now: float) -> str | None:
        svc = self.service
        resolved = getattr(svc, "_resolved_auto_energy_services", {})
        last_scan = getattr(svc, "_auto_energy_last_scan", {})
        cached_service = resolved.get(source_id) if isinstance(resolved, dict) else None
        cached_scan_at = last_scan.get(source_id, 0.0) if isinstance(last_scan, dict) else 0.0
        if discovery_cache_valid(
            cached_service,
            cached_scan_at,
            svc.auto_battery_scan_interval_seconds,
            now,
        ):
            return cast(str | None, cached_service)
        return None

    def _remember_energy_service(self, source_id: str, service_name: str, now: float) -> str:
        svc = self.service
        if not isinstance(getattr(svc, "_resolved_auto_energy_services", None), dict):
            svc._resolved_auto_energy_services = {}
        if not isinstance(getattr(svc, "_auto_energy_last_scan", None), dict):
            svc._auto_energy_last_scan = {}
        svc._resolved_auto_energy_services[source_id] = service_name
        svc._auto_energy_last_scan[source_id] = now
        return service_name

    def _primary_energy_source_service(self) -> str:
        return str(self.service.resolve_auto_battery_service())

    def _configured_energy_source_service(self, source: EnergySourceDefinition, now: float) -> str | None:
        if not source.service_name or not self._energy_source_has_readable_data(source, source.service_name):
            return None
        return self._remember_energy_service(source.source_id, source.service_name, now)

    def _discovered_energy_source_service(self, source: EnergySourceDefinition, now: float) -> str:
        if not source.service_prefix:
            raise ValueError(f"No readable DBus service configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self.service.list_dbus_services(),
            source.service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is None:
            raise ValueError(f"No DBus service found for energy source '{source.source_id}'")
        return self._remember_energy_service(source.source_id, service_name, now)

    def _resolve_energy_source_service(self, source: EnergySourceDefinition) -> str:
        now = time.time()
        if source.source_id == self._primary_energy_source().source_id:
            return self._primary_energy_source_service()
        configured_service = self._configured_energy_source_service(source, now)
        if configured_service is not None:
            return configured_service
        cached_service = self._energy_cache_valid(source.source_id, now)
        if cached_service is not None:
            return cached_service
        return self._discovered_energy_source_service(source, now)

    def _scan_auto_battery_service(self, now: float) -> str:
        svc = self.service
        source = self._primary_energy_source()
        battery_service_prefix = str(getattr(svc, "auto_battery_service_prefix", "") or "")
        service_name = first_matching_prefixed_service(
            svc.list_dbus_services(),
            source.service_prefix or battery_service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is not None:
            svc._resolved_auto_battery_service = service_name
            svc._auto_battery_last_scan = now
            self._remember_energy_service(source.source_id, service_name, now)
            logging.debug("Auto battery service resolved: %s", svc._resolved_auto_battery_service)
            return service_name
        raise ValueError(f"No DBus service found with prefix '{source.service_prefix or battery_service_prefix}'")

    def resolve_auto_battery_service(self) -> str:
        override_service = self._resolve_battery_service_override()
        if override_service is not None:
            return override_service
        now = time.time()
        cached_service = self._cached_auto_battery_service(now)
        if cached_service is not None:
            return cached_service
        return self._scan_auto_battery_service(now)

    def _configured_grid_paths(self) -> list[str]:
        svc = self.service
        return configured_grid_paths(svc.auto_grid_l1_path, svc.auto_grid_l2_path, svc.auto_grid_l3_path)

    def _read_grid_phase_values(self, configured_paths: list[str]) -> tuple[float, bool, list[str]]:
        total = 0.0
        seen_value = False
        missing_paths = []
        for path in configured_paths:
            numeric_value = self._read_one_grid_phase(path)
            if numeric_value is None:
                missing_paths.append(path)
                continue
            total += numeric_value
            seen_value = True
        return total, seen_value, missing_paths

    def _read_one_grid_phase(self, path: str) -> float | None:
        """Read one configured grid phase path as a numeric contribution."""
        svc = self.service
        if self._introspection_says_skip(svc.auto_grid_service, path, priority=85):
            return None
        try:
            value = svc.get_dbus_value(svc.auto_grid_service, path)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto grid read failed for %s %s: %s", svc.auto_grid_service, path, error)
            self._request_introspection(svc.auto_grid_service, path, priority=95, reason="grid phase read failed")
            return None
        return self._numeric_sum(value) if value is not None else None

    def _introspection_says_skip(self, service_name: str, path: str, *, priority: int) -> bool:
        skip, reason = owner_path_unusable(self.service, service_name, path)
        if skip:
            logging.debug("Skipping %s %s from DBus introspection cache: %s", service_name, path, reason)
            self._request_introspection(service_name, path, priority=priority, reason="known-unusable input path")
        return bool(skip)

    def _request_introspection(self, service_name: str, path: str, *, priority: int, reason: str) -> None:
        request_owner_introspection(
            self.service,
            service_name,
            path,
            priority=priority,
            reason=reason,
            source="evcharger-inputs",
        )

    def _grid_values_complete_enough(self, seen_value: bool, missing_paths: list[str]) -> bool:
        return grid_values_complete_enough(
            seen_value,
            missing_paths,
            self.service.auto_grid_require_all_phases,
        )

    def _handle_missing_grid_values(self, seen_value: bool, missing_paths: list[str], now: float) -> float | None:
        svc = self.service
        if seen_value and missing_paths:
            logging.debug(
                "Auto grid readings incomplete for %s, missing paths: %s",
                svc.auto_grid_service,
                ", ".join(missing_paths),
            )
        return cast(
            float | None,
            self._handle_source_failure(
                "grid",
                now,
                "grid-missing",
                svc.auto_pv_scan_interval_seconds,
                "Auto mode could not read grid power from %s.",
                svc.auto_grid_service,
            ),
        )

    def _finalize_grid_power(
        self,
        total: float,
        seen_value: bool,
        missing_paths: list[str],
        now: float,
    ) -> float | None:
        if self._grid_values_complete_enough(seen_value, missing_paths):
            self._mark_source_recovery("grid", "Grid readings recovered")
            return total
        return self._handle_missing_grid_values(seen_value, missing_paths, now)
