#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus and energy-source resolution helpers for the auto input helper."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as xml_et
from typing import Any, cast

import dbus
from venus_evcharger.core.shared import (
    coerce_dbus_numeric,
    discovery_cache_valid,
    first_matching_prefixed_service,
)
from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot


_EXPECTED_MISSING_DBUS_ERROR_NAMES = frozenset(
    (
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownObject",
    )
)

_EXPECTED_MISSING_DBUS_ERROR_TEXT = (
    "NameHasNoOwner",
    "ServiceUnknown",
    "UnknownInterface",
    "UnknownMethod",
    "UnknownObject",
    "was not provided by any .service files",
)


def _dbus_error_name(error: BaseException) -> str:
    getter = getattr(error, "get_dbus_name", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:  # pragma: no cover - defensive for foreign DBus objects
            return ""
    return str(getattr(error, "_dbus_error_name", "") or "")


def _is_expected_missing_dbus_error(error: BaseException) -> bool:
    """Return whether a DBus error means absent data, not a broken connection."""
    error_name = _dbus_error_name(error)
    if error_name in _EXPECTED_MISSING_DBUS_ERROR_NAMES:
        return True
    error_text = str(error)
    return any(marker in error_text for marker in _EXPECTED_MISSING_DBUS_ERROR_TEXT)


class _AutoInputHelperSourceDbusMixin:
    def _get_dbus_value(self: Any, service_name: str, path: str) -> float | int | None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                obj = self._get_system_bus().get_object(service_name, path)
                interface = cast(Any, dbus).Interface(obj, "com.victronenergy.BusItem")
                return cast(float | int | None, coerce_dbus_numeric(interface.GetValue(timeout=self.dbus_method_timeout_seconds)))
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                if _is_expected_missing_dbus_error(error):
                    logging.debug("DBus value missing for %s %s: %s", service_name, path, error)
                    raise
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus read retry for %s %s after error: %s", service_name, path, error)
        assert last_error is not None
        raise last_error

    def _get_dbus_child_nodes(self: Any, service_name: str, path: str) -> list[str]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                obj = self._get_system_bus().get_object(service_name, path)
                interface = cast(Any, dbus).Interface(obj, "org.freedesktop.DBus.Introspectable")
                return cast(list[str], self._child_nodes_from_introspection(interface.Introspect(timeout=self.dbus_method_timeout_seconds)))
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                if _is_expected_missing_dbus_error(error):
                    logging.debug("DBus child nodes missing for %s %s: %s", service_name, path, error)
                    raise
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus introspection retry for %s %s after error: %s", service_name, path, error)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _child_nodes_from_introspection(xml_data: object) -> list[str]:
        root = xml_et.fromstring(str(xml_data))
        return [str(name) for node in root.findall("node") if (name := node.attrib.get("name"))]

    def _list_dbus_services(self: Any) -> list[str]:
        now = time.time()
        if now < self._dbus_list_backoff_until:
            return []
        try:
            dbus_proxy = self._get_system_bus().get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            dbus_iface = cast(Any, dbus).Interface(dbus_proxy, "org.freedesktop.DBus")
            names = list(dbus_iface.ListNames(timeout=self.dbus_method_timeout_seconds))
            self._dbus_list_failures = 0
            self._dbus_list_backoff_until = 0.0
            return names
        except Exception as error:  # pylint: disable=broad-except
            self._reset_system_bus()
            self._dbus_list_failures += 1
            delay = self.auto_dbus_backoff_base_seconds * (2 ** max(0, self._dbus_list_failures - 1))
            if self.auto_dbus_backoff_max_seconds > 0:
                delay = min(delay, self.auto_dbus_backoff_max_seconds)
            self._dbus_list_backoff_until = now + max(0.0, delay)
            self._warning_throttled(
                "auto-helper-list-dbus-failed",
                max(5.0, self.auto_dbus_backoff_base_seconds or 5.0),
                "Auto input helper could not list DBus services: %s",
                error,
            )
            return []

    def _dbus_service_name_available(self: Any, service_name: str) -> bool:
        return bool(service_name and service_name in self._list_dbus_services())

    def _source_retry_ready(self: Any, key: str) -> bool:
        return time.time() >= float(self._source_retry_after.get(key, 0.0))

    def _delay_source_retry(self: Any, key: str) -> None:
        self._source_retry_after[key] = time.time() + max(1.0, self.auto_dbus_backoff_base_seconds or 5.0)

    def _invalidate_auto_battery_service(self: Any) -> None:
        self._resolved_auto_battery_service = None
        self._auto_battery_last_scan = 0.0
        if isinstance(getattr(self, "_resolved_auto_energy_services", None), dict):
            self._resolved_auto_energy_services.pop("primary_battery", None)
        if isinstance(getattr(self, "_auto_energy_last_scan", None), dict):
            self._auto_energy_last_scan.pop("primary_battery", None)

    def _configured_primary_energy_sources(self: Any) -> tuple[EnergySourceDefinition, ...]:
        return tuple(getattr(self, "auto_energy_sources", ()) or ())

    @staticmethod
    def _primary_energy_source_id() -> str:
        return "primary_battery"

    @staticmethod
    def _primary_energy_source_role() -> str:
        return "battery"

    def _primary_energy_service_name(self: Any) -> str:
        return str(getattr(self, "auto_battery_service", "") or "")

    def _primary_energy_service_prefix(self: Any) -> str:
        return str(getattr(self, "auto_battery_service_prefix", "") or "")

    def _primary_energy_soc_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_soc_path", "/Soc") or "/Soc")

    def _primary_energy_capacity_wh(self: Any) -> float | None:
        value = getattr(self, "auto_battery_capacity_wh", None)
        return float(value) if isinstance(value, (int, float)) else None

    def _primary_energy_battery_power_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_power_path", "") or "")

    def _primary_energy_ac_power_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_ac_power_path", "") or "")

    def _primary_energy_pv_power_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_pv_power_path", "") or "")

    def _primary_energy_grid_interaction_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_grid_interaction_path", "") or "")

    def _primary_energy_operating_mode_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_operating_mode_path", "") or "")

    def _default_primary_energy_source(self: Any) -> EnergySourceDefinition:
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

    def _primary_energy_source(self: Any) -> EnergySourceDefinition:
        sources = cast(tuple[EnergySourceDefinition, ...], self._configured_primary_energy_sources())
        if sources:
            return sources[0]
        return cast(EnergySourceDefinition, self._default_primary_energy_source())

    def _battery_service_has_soc(self: Any, service_name: str) -> bool:
        try:
            return self._get_dbus_value(service_name, self.auto_battery_soc_path) is not None
        except Exception:
            return False

    def _energy_service_has_readable_field(self: Any, service_name: str, path: str) -> bool:
        if not path:
            return False
        try:
            return self._get_dbus_value(service_name, path) is not None
        except Exception:
            return False

    def _energy_source_has_readable_data(self: Any, source: EnergySourceDefinition, service_name: str) -> bool:
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

    def _read_dbus_energy_source_fields(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
    ) -> tuple[float | None, float | None, float | None, float | None, float | None, str]:
        return (
            self._read_optional_energy_value(service_name, source.soc_path),
            self._read_optional_energy_value(service_name, source.battery_power_path),
            self._read_optional_energy_value(service_name, source.ac_power_path),
            self._read_optional_energy_value(service_name, source.pv_power_path),
            self._read_optional_energy_value(service_name, source.grid_interaction_path),
            self._read_optional_energy_text(service_name, source.operating_mode_path),
        )

    def _read_dbus_energy_source_fields_with_primary_retry(
        self: Any,
        source: EnergySourceDefinition,
    ) -> tuple[str, tuple[float | None, float | None, float | None, float | None, float | None, str]]:
        service_name = self._resolve_energy_source_service(source)
        try:
            return service_name, self._read_dbus_energy_source_fields(source, service_name)
        except Exception:
            if source.source_id != self._primary_energy_source().source_id:
                raise
            self._invalidate_auto_battery_service()
            service_name = self._resolve_energy_source_service(source)
            return service_name, self._read_dbus_energy_source_fields(source, service_name)

    def _validated_energy_source_soc(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
    ) -> float | None:
        if soc_value is None or 0.0 <= soc_value <= 100.0:
            return soc_value
        self._warning_throttled(
            "auto-helper-battery-soc-invalid",
            max(5.0, self.auto_battery_scan_interval_seconds or 5.0),
            "Auto input helper ignored out-of-range battery SOC %s from %s %s",
            soc_value,
            service_name,
            source.soc_path,
        )
        self._delay_source_retry("battery")
        return None

    @staticmethod
    def _dbus_energy_source_snapshot_payload(
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
        net_battery_power: float | None,
        ac_power: float | None,
        pv_input_power: float | None,
        grid_interaction: float | None,
        operating_mode: str,
        now: float,
    ) -> EnergySourceSnapshot:
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name,
            soc=soc_value,
            usable_capacity_wh=source.usable_capacity_wh,
            net_battery_power_w=net_battery_power,
            ac_power_w=ac_power,
            pv_input_power_w=pv_input_power,
            grid_interaction_w=grid_interaction,
            operating_mode=operating_mode,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def _dbus_energy_source_snapshot(self: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
        service_name, fields = self._read_dbus_energy_source_fields_with_primary_retry(source)
        (
            soc_value,
            net_battery_power,
            ac_power,
            pv_input_power,
            grid_interaction,
            operating_mode,
        ) = fields
        validated_soc = self._validated_energy_source_soc(source, service_name, soc_value)
        return cast(
            EnergySourceSnapshot,
            self._dbus_energy_source_snapshot_payload(
            source,
            service_name,
            validated_soc,
            net_battery_power,
            ac_power,
            pv_input_power,
            grid_interaction,
            operating_mode,
            now,
            ),
        )
