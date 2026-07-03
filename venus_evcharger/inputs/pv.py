# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.core.shared import (
    discovery_cache_valid,
    prefixed_service_names,
)
from venus_evcharger.dbus_gateway import (
    PV_POWER_READ_KEY,
    DbusCacheStore,
    dbus_path_key,
)
from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS
from venus_evcharger.inputs.gateway_read import GatewayInputReader


def _service_name_or_none(value: object) -> str | None:
    """Return one valid DBus service name from cached discovery data."""
    if not isinstance(value, str):
        return None
    service_name = value.strip()
    return service_name or None


def _service_name_list(value: object) -> list[str] | None:
    """Return a non-empty list of service names from cached discovery data."""
    if not isinstance(value, list):
        return None
    service_names: list[str] = []
    for item in value:
        service_name = _service_name_or_none(item)
        if service_name is not None:
            service_names.append(service_name)
    return service_names or None


class _DbusInputPv(GatewayInputReader):
    def _source_retry_ready(self, source_key: str, now: float) -> bool:
        """Return whether a logical input source may currently be queried."""
        return bool(self.service.source_retry_ready(source_key, now))

    def _mark_source_recovery(self, source_key: str, message: str, *args: Any) -> None:
        """Record that one logical input source has recovered."""
        self.service.mark_recovery(source_key, message, *args)

    def _handle_source_failure(
        self,
        source_key: str,
        now: float,
        warning_key: str,
        warning_interval: float,
        warning_message: str,
        *args: Any,
    ) -> float | None:
        """Apply the standard failure/backoff/warning flow for one input source."""
        svc = self.service
        svc.mark_failure(source_key)
        svc.delay_source_retry(source_key, now)
        svc.warning_throttled(warning_key, warning_interval, warning_message, *args)
        return None

    def get_dbus_value(self, service_name: str, path: str) -> float | int | None:
        """Read one value from the gateway cache and request refresh when needed."""
        svc = self.service
        snapshot = self._gateway_snapshot()
        entry = DbusCacheStore.value_entry(snapshot, dbus_path_key(service_name, path))
        if entry is not None:
            if entry.get("status") == "fresh":
                value = entry.get("value")
                self._mark_dbus_success(svc)
                return self._coerce_dbus_value(value)
        self._gateway_client().request_raw_value(
            service_name,
            path,
            priority="read",
            reason="main input cache miss",
            source="evcharger-inputs",
        )
        svc.mark_failure("dbus")
        return None

    def list_dbus_services(self) -> list[str]:
        """List DBus services from the gateway cache."""
        svc = self.service
        self._ensure_dbus_list_state()
        now = time.time()
        if now < svc._dbus_list_backoff_until:
            raise RuntimeError("DBus list backoff active")
        names = self._list_dbus_names()
        if names:
            svc._dbus_list_failures = 0
            svc._dbus_list_backoff_until = 0.0
            self._mark_dbus_success(svc)
            return names
        self._gateway_client().enqueue_command({"kind": "refresh_services", "source": "evcharger-inputs", "priority": "read"})
        svc._dbus_list_failures += 1
        svc.mark_failure("dbus")
        svc._dbus_list_backoff_until = now + self._dbus_list_backoff_delay()
        return []

    def _ensure_dbus_list_state(self) -> None:
        """Populate DBus-list retry/backoff defaults used by list_dbus_services()."""
        svc = self.service
        self._ensure_service_attr(svc, "_dbus_list_backoff_until", 0.0)
        self._ensure_service_attr(svc, "_dbus_list_failures", 0)
        self._ensure_service_attr(svc, "auto_dbus_backoff_base_seconds", 5.0)
        self._ensure_service_attr(svc, "auto_dbus_backoff_max_seconds", 60.0)
        self._ensure_service_attr(svc, "dbus_method_timeout_seconds", 1.0)

    @staticmethod
    def _ensure_service_attr(svc: Any, attr_name: str, default: object) -> None:
        """Populate one service attribute when it is missing."""
        if not hasattr(svc, attr_name):
            setattr(svc, attr_name, default)

    def _list_dbus_names(self) -> list[str]:
        """Return DBus names from the gateway cache."""
        services = self._gateway_snapshot().get("services")
        if isinstance(services, list):
            return [str(name) for name in services]
        if isinstance(services, dict):
            return [str(name) for name in services]
        return []

    def _dbus_list_backoff_delay(self) -> float:
        """Return the current exponential-backoff delay for DBus name listing."""
        svc = self.service
        return float(min(
            svc.auto_dbus_backoff_max_seconds,
            svc.auto_dbus_backoff_base_seconds * (2 ** (svc._dbus_list_failures - 1)),
        ))

    def invalidate_auto_pv_services(self) -> None:
        """Clear cached PV service discovery so the next read performs a fresh scan."""
        svc = self.service
        svc._resolved_auto_pv_services = []
        svc._auto_pv_last_scan = 0.0

    def invalidate_auto_battery_service(self) -> None:
        """Clear cached battery service discovery so the next read performs a fresh scan."""
        svc = self.service
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0

    def resolve_auto_pv_services(self) -> list[str]:
        """Resolve AC PV services (or use explicit override) for Auto mode."""
        svc = self.service
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
            svc.list_dbus_services(),
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
        svc = self.service
        now = time.time()
        if not self._source_retry_ready("pv", now):
            return None
        semantic_value = self.get_gateway_read_value(PV_POWER_READ_KEY, reason="main semantic PV power read")
        if semantic_value is not None:
            svc._last_pv_missing_warning = None
            self._mark_source_recovery("pv", "PV readings recovered")
            return float(semantic_value)
        self._handle_source_failure(
            "pv",
            now,
            "pv-missing",
            svc.auto_pv_scan_interval_seconds,
            "Auto mode could not read PV power from the DBus gateway read contract.",
        )
        return None
