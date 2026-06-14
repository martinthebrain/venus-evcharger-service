#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus and energy-source resolution helpers for the auto input helper."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as xml_et
from typing import Any, cast

from venus_evcharger.core.shared import (
    coerce_dbus_numeric,
    discovery_cache_valid,
    first_matching_prefixed_service,
)
from venus_evcharger.dbus_gateway import DbusCacheStore, GatewayClient, dbus_path_key, gateway_paths
from venus_evcharger.dbus_introspection import (
    owner_path_children,
    owner_path_unusable,
)
from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.inputs.helper.capacity_persistence import (
    configured_estimated_capacity_payload,
    persist_estimated_capacity_if_ah_changed,
)


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
    @staticmethod
    def _dbus_module() -> Any:
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def _get_dbus_value(self: Any, service_name: str, path: str) -> float | int | None:
        if self._dbus_introspection_says_skip(service_name, path):
            self._request_dbus_introspection(service_name, path, priority=80, reason="helper skipped known-unusable path")
            return None
        cache_key = dbus_path_key(service_name, path)
        snapshot = self._gateway_cache_snapshot()
        entry = DbusCacheStore.value_entry(snapshot, cache_key)
        if entry is not None and str(entry.get("status", "")) in ("fresh", "stale"):
            return cast(float | int | None, coerce_dbus_numeric(entry.get("value")))
        self._request_gateway_value(service_name, path, priority=90, reason="helper DBus cache miss")
        return None

    def _get_dbus_child_nodes(self: Any, service_name: str, path: str) -> list[str]:
        children = owner_path_children(self, service_name, path)
        if children:
            return children
        self._request_dbus_introspection(service_name, path, priority=60, reason="helper child-node discovery requested")
        return []

    def _dbus_introspection_says_skip(self: Any, service_name: str, path: str) -> bool:
        skip, reason = owner_path_unusable(self, service_name, path)
        if skip:
            logging.debug("Auto helper skipping %s %s from DBus introspection cache: %s", service_name, path, reason)
        return bool(skip)

    def _request_dbus_introspection(
        self: Any,
        service_name: str,
        path: str,
        *,
        priority: int,
        reason: str,
    ) -> None:
        try:
            self._gateway_client().enqueue_command(
                {
                    "kind": "introspect",
                    "source": "auto-input-helper",
                    "service": service_name,
                    "path": path,
                    "priority": "discovery" if priority < 90 else "read",
                    "reason": reason,
                    "coalesce_key": f"introspect:{service_name}:{path}",
                }
            )
        except OSError:
            return

    def _request_gateway_value(self: Any, service_name: str, path: str, *, priority: int, reason: str) -> None:
        try:
            self._gateway_client().enqueue_command(
                {
                    "kind": "refresh_value",
                    "source": "auto-input-helper",
                    "service": service_name,
                    "path": path,
                    "priority": "read" if priority >= 80 else "optional",
                    "reason": reason,
                    "coalesce_key": f"refresh:{service_name}:{path}",
                }
            )
        except OSError:
            return

    def _gateway_client(self: Any) -> GatewayClient:
        client = getattr(self, "_gateway_client_instance", None)
        if client is None:
            client = GatewayClient(gateway_paths(getattr(self, "dbus_gateway_run_dir", "")))
            self._gateway_client_instance = client
        return cast(GatewayClient, client)

    def _gateway_cache_snapshot(self: Any) -> dict[str, Any]:
        return DbusCacheStore.load_snapshot(
            str(getattr(self, "dbus_gateway_cache_path", "") or self._gateway_client().paths.cache_path),
            max_age_seconds=max(0.0, float(getattr(self, "dbus_gateway_max_age_seconds", 10.0) or 10.0)),
        )

    def _dbus_retry_read(self: Any, service_name: str, path: str, label: str, read: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return read()
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                if _is_expected_missing_dbus_error(error):
                    logging.debug("DBus value missing for %s %s: %s", service_name, path, error)
                    raise
                self._reset_system_bus_after_retryable_error(attempt, label, service_name, path, error)
        assert last_error is not None
        raise last_error

    def _reset_system_bus_after_retryable_error(
        self: Any,
        attempt: int,
        label: str,
        service_name: str,
        path: str,
        error: Exception,
    ) -> None:
        self._reset_system_bus()
        if attempt == 0:
            logging.debug("%s retry for %s %s after error: %s", label, service_name, path, error)

    @staticmethod
    def _child_nodes_from_introspection(xml_data: object) -> list[str]:
        root = xml_et.fromstring(str(xml_data))
        return [str(name) for node in root.findall("node") if (name := node.attrib.get("name"))]

    def _list_dbus_services(self: Any) -> list[str]:
        now = time.time()
        if now < self._dbus_list_backoff_until:
            return []
        snapshot = self._gateway_cache_snapshot()
        services = snapshot.get("services")
        if isinstance(services, dict):
            self._dbus_list_failures = 0
            self._dbus_list_backoff_until = 0.0
            return [str(name) for name in services]
        self._gateway_client().enqueue_command(
            {
                "kind": "refresh_services",
                "source": "auto-input-helper",
                "priority": "discovery",
                "coalesce_key": "refresh-services",
            }
        )
        self._dbus_list_failures += 1
        delay = self.auto_dbus_backoff_base_seconds * (2 ** max(0, self._dbus_list_failures - 1))
        if self.auto_dbus_backoff_max_seconds > 0:
            delay = min(delay, self.auto_dbus_backoff_max_seconds)
        self._dbus_list_backoff_until = now + max(0.0, delay)
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

    def _primary_energy_chemistry(self: Any) -> str:
        return str(getattr(self, "auto_battery_chemistry", "lfp") or "lfp").strip().lower()

    def _primary_energy_capacity_auto_estimate(self: Any) -> bool:
        return bool(getattr(self, "auto_battery_capacity_auto_estimate", True))

    def _primary_energy_capacity_wh_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_capacity_wh_path", "") or "")

    def _primary_energy_capacity_ah_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_capacity_ah_path", "/InstalledCapacity") or "/InstalledCapacity")

    def _primary_energy_voltage_path(self: Any) -> str:
        return str(getattr(self, "auto_battery_voltage_path", "/Dc/0/Voltage") or "/Dc/0/Voltage")

    def _primary_energy_capacity_estimate_min_soc(self: Any) -> float:
        return max(0.0, float(getattr(self, "auto_battery_capacity_estimate_min_soc", 95.0) or 95.0))

    def _primary_energy_capacity_startup_recheck_seconds(self: Any) -> float:
        return max(0.0, float(getattr(self, "auto_battery_capacity_startup_recheck_seconds", 300.0) or 300.0))

    def _primary_energy_estimated_capacity_wh(self: Any) -> float | None:
        return self._positive_float_or_none(getattr(self, "auto_battery_capacity_estimated_wh", None))

    def _primary_energy_estimated_capacity_ah(self: Any) -> float | None:
        return self._positive_float_or_none(getattr(self, "auto_battery_capacity_estimated_ah", None))

    def _primary_energy_estimated_capacity_nominal_voltage(self: Any) -> float | None:
        return self._positive_float_or_none(getattr(self, "auto_battery_capacity_estimated_nominal_voltage", None))

    def _primary_energy_estimated_capacity_cell_count(self: Any) -> int | None:
        try:
            value = int(float(getattr(self, "auto_battery_capacity_estimated_cell_count", 0) or 0))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _positive_float_or_none(value: object) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric > 0.0 else None

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
            battery_chemistry=self._primary_energy_chemistry(),
            capacity_auto_estimate=self._primary_energy_capacity_auto_estimate(),
            capacity_wh_path=self._primary_energy_capacity_wh_path(),
            capacity_ah_path=self._primary_energy_capacity_ah_path(),
            voltage_path=self._primary_energy_voltage_path(),
            capacity_estimate_min_soc=self._primary_energy_capacity_estimate_min_soc(),
            capacity_startup_recheck_seconds=self._primary_energy_capacity_startup_recheck_seconds(),
            estimated_capacity_wh=self._primary_energy_estimated_capacity_wh(),
            estimated_capacity_ah=self._primary_energy_estimated_capacity_ah(),
            estimated_capacity_nominal_voltage_v=self._primary_energy_estimated_capacity_nominal_voltage(),
            estimated_capacity_cell_count=self._primary_energy_estimated_capacity_cell_count(),
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

    def _dbus_energy_source_snapshot_payload(
        self: Any,
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
        capacity_payload = self._dbus_energy_source_capacity_payload(source, service_name, soc_value, now)
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name,
            soc=soc_value,
            usable_capacity_wh=cast(float | None, capacity_payload.get("usable_capacity_wh")),
            usable_capacity_source=str(capacity_payload.get("usable_capacity_source", "")),
            installed_capacity_ah=cast(float | None, capacity_payload.get("installed_capacity_ah")),
            capacity_voltage_v=cast(float | None, capacity_payload.get("capacity_voltage_v")),
            capacity_nominal_voltage_v=cast(float | None, capacity_payload.get("capacity_nominal_voltage_v")),
            capacity_cell_count=cast(int | None, capacity_payload.get("capacity_cell_count")),
            battery_chemistry=source.battery_chemistry,
            net_battery_power_w=net_battery_power,
            ac_power_w=ac_power,
            pv_input_power_w=pv_input_power,
            grid_interaction_w=grid_interaction,
            operating_mode=operating_mode,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def _dbus_energy_source_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
        now: float,
    ) -> dict[str, object]:
        configured = self._configured_dbus_capacity_payload(source)
        if configured is not None:
            return configured
        cached = self._cached_dbus_capacity_payload(source, service_name)
        startup_recheck_due = self._dbus_capacity_startup_recheck_due(source, service_name, now)
        if self._dbus_cached_capacity_usable(cached, startup_recheck_due):
            return cached
        inferred = self._fresh_dbus_capacity_payload(source, service_name, soc_value, startup_recheck_due)
        return self._resolved_dbus_capacity_payload(inferred, cached)

    @staticmethod
    def _dbus_cached_capacity_usable(cached: dict[str, object] | None, startup_recheck_due: bool) -> bool:
        return cached is not None and not startup_recheck_due

    @staticmethod
    def _resolved_dbus_capacity_payload(
        inferred: dict[str, object] | None,
        cached: dict[str, object] | None,
    ) -> dict[str, object]:
        if inferred is not None:
            return inferred
        if cached is not None:
            return cached
        return {"usable_capacity_wh": None, "usable_capacity_source": ""}

    def _fresh_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
        startup_recheck_due: bool,
    ) -> dict[str, object] | None:
        inferred = self._infer_dbus_capacity_payload(source, service_name, soc_value)
        if inferred is None:
            return None
        self._store_dbus_capacity_payload(source, service_name, inferred, startup_recheck_done=startup_recheck_due)
        self._persist_dbus_capacity_payload_if_needed(source, inferred, startup_recheck_due)
        return inferred

    @staticmethod
    def _configured_dbus_capacity_payload(source: EnergySourceDefinition) -> dict[str, object] | None:
        if source.usable_capacity_wh is None or source.usable_capacity_wh <= 0.0:
            return None
        return {"usable_capacity_wh": source.usable_capacity_wh, "usable_capacity_source": "configured"}

    def _cached_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
    ) -> dict[str, object] | None:
        key = self._dbus_capacity_cache_key(source, service_name)
        estimates = getattr(self, "_auto_battery_capacity_estimates", {})
        cached = estimates.get(key) if isinstance(estimates, dict) else None
        if isinstance(cached, dict):
            return dict(cached)
        return configured_estimated_capacity_payload(source)

    def _persist_dbus_capacity_payload_if_needed(
        self: Any,
        source: EnergySourceDefinition,
        payload: dict[str, object],
        startup_recheck_done: bool,
    ) -> None:
        if not startup_recheck_done:
            return
        try:
            changed = persist_estimated_capacity_if_ah_changed(str(getattr(self, "config_path", "")), source, payload)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to persist auto-estimated battery capacity: %s", error)
            return
        if changed:
            logging.info("Persisted auto-estimated battery capacity for source %s", source.source_id)

    def _dbus_capacity_startup_recheck_due(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        now: float,
    ) -> bool:
        if not self._dbus_capacity_startup_recheck_time_due(source, now):
            return False
        return not self._dbus_capacity_startup_recheck_seen(source, service_name)

    def _dbus_capacity_startup_recheck_time_due(self: Any, source: EnergySourceDefinition, now: float) -> bool:
        recheck_at = float(getattr(self, "_auto_battery_capacity_startup_recheck_at", 0.0) or 0.0)
        return bool(source.capacity_startup_recheck_seconds > 0.0 and recheck_at > 0.0 and now >= recheck_at)

    def _dbus_capacity_startup_recheck_seen(self: Any, source: EnergySourceDefinition, service_name: str) -> bool:
        rechecked = getattr(self, "_auto_battery_capacity_startup_rechecked", {})
        key = self._dbus_capacity_cache_key(source, service_name)
        return bool(isinstance(rechecked, dict) and rechecked.get(key))

    def _store_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        payload: dict[str, object],
        *,
        startup_recheck_done: bool,
    ) -> None:
        if not isinstance(getattr(self, "_auto_battery_capacity_estimates", None), dict):
            self._auto_battery_capacity_estimates = {}
        key = self._dbus_capacity_cache_key(source, service_name)
        self._auto_battery_capacity_estimates[key] = dict(payload)
        if startup_recheck_done:
            if not isinstance(getattr(self, "_auto_battery_capacity_startup_rechecked", None), dict):
                self._auto_battery_capacity_startup_rechecked = {}
            self._auto_battery_capacity_startup_rechecked[key] = True

    @staticmethod
    def _dbus_capacity_cache_key(source: EnergySourceDefinition, service_name: str) -> str:
        return f"{source.source_id}:{service_name}"

    def _infer_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
    ) -> dict[str, object] | None:
        if not self._dbus_capacity_inference_allowed(source, soc_value):
            return None
        direct_capacity = self._read_positive_optional_energy_value(service_name, source.capacity_wh_path)
        if direct_capacity is not None:
            return self._direct_dbus_capacity_payload(direct_capacity)
        installed_capacity_ah = self._read_positive_optional_energy_value(service_name, source.capacity_ah_path)
        voltage = self._read_positive_optional_energy_value(service_name, source.voltage_path)
        return self._lfp_inferred_dbus_capacity_payload(installed_capacity_ah, voltage)

    @staticmethod
    def _dbus_capacity_inference_allowed(source: EnergySourceDefinition, soc_value: float | None) -> bool:
        if not source.capacity_auto_estimate or source.battery_chemistry.strip().lower() != "lfp":
            return False
        return bool(soc_value is not None and float(soc_value) >= float(source.capacity_estimate_min_soc))

    @staticmethod
    def _direct_dbus_capacity_payload(direct_capacity: float) -> dict[str, object]:
        return {
            "usable_capacity_wh": direct_capacity,
            "usable_capacity_source": "dbus_capacity_wh",
        }

    def _lfp_inferred_dbus_capacity_payload(
        self: Any,
        installed_capacity_ah: float | None,
        voltage: float | None,
    ) -> dict[str, object] | None:
        nominal_voltage, cell_count = self._lfp_nominal_voltage_from_full_voltage(voltage)
        if installed_capacity_ah is None or nominal_voltage is None or cell_count is None:
            return None
        return {
            "usable_capacity_wh": installed_capacity_ah * nominal_voltage,
            "usable_capacity_source": "dbus_lfp_inferred",
            "installed_capacity_ah": installed_capacity_ah,
            "capacity_voltage_v": voltage,
            "capacity_nominal_voltage_v": nominal_voltage,
            "capacity_cell_count": cell_count,
        }

    def _read_positive_optional_energy_value(self: Any, service_name: str, path: str) -> float | None:
        value = self._read_optional_energy_value(service_name, path)
        return value if value is not None and value > 0.0 else None

    @staticmethod
    def _lfp_nominal_voltage_from_full_voltage(voltage: float | None) -> tuple[float | None, int | None]:
        if voltage is None or voltage < 40.0 or voltage > 60.0:
            return None, None
        cell_count = 15 if voltage < 52.5 else 16
        return cell_count * 3.2, cell_count

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
