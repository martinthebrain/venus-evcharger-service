# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway/cache access helpers for auto-input DBus sources."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as xml_et
from collections.abc import Mapping
from typing import Any, cast

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_gateway import DbusCacheStore, GatewayClient, dbus_path_key, gateway_paths
from venus_evcharger.dbus_introspection import owner_path_children, owner_path_unusable
from venus_evcharger.inputs.helper.sources_dbus_common import _is_expected_missing_dbus_error

_CACHE_VALUE_MISSING = object()


class _AutoInputHelperSourceDbusGatewayMixin:
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
        cached_value = self._cached_gateway_value(entry)
        if cached_value is not _CACHE_VALUE_MISSING:
            return cast(float | int | None, cached_value)
        if self._cached_gateway_error_recent(entry):
            logging.debug("Auto helper suppressing fresh DBus cache error for %s %s", service_name, path)
            return None
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

    @staticmethod
    def _cached_gateway_value(entry: Mapping[str, Any] | None) -> object:
        if entry is None or str(entry.get("status", "")) not in ("fresh", "stale"):
            return _CACHE_VALUE_MISSING
        return coerce_dbus_numeric(entry.get("value"))

    def _cached_gateway_error_recent(self: Any, entry: Mapping[str, Any] | None) -> bool:
        return bool(entry is not None and self._gateway_error_recent(entry))

    def _gateway_error_recent(self: Any, entry: Mapping[str, Any]) -> bool:
        if str(entry.get("status", "")) != "error":
            return False
        error_at = float(entry.get("error_at", 0.0) or 0.0)
        return error_at > 0.0 and time.time() - error_at < self._gateway_error_retry_seconds()

    def _gateway_error_retry_seconds(self: Any) -> float:
        configured = float(getattr(self, "dbus_gateway_error_retry_seconds", 30.0) or 30.0)
        return max(1.0, min(300.0, configured))

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
