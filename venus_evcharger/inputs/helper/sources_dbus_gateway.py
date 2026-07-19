# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-only cache access for auto-input sources."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    GatewayClient,
    GatewayReadKey,
    dbus_path_key,
    gateway_paths,
    gateway_read_value,
    require_gateway_read_key,
)
from venus_evcharger.dbus_introspection import load_introspection_snapshot, path_children, path_unusable_until
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import GatewayCommandClientPort
from venus_evcharger.inputs.helper.payload_types import is_object_mapping

_CACHE_VALUE_MISSING = object()


class GatewayCacheReader:
    """Own gateway IPC, cache freshness, retries, and service discovery state."""

    def __init__(self, settings: AutoInputHelperSettings, client: GatewayCommandClientPort | None = None) -> None:
        self.settings = settings
        self._client = client
        self._list_backoff_until = 0.0
        self._list_failures = 0
        self._source_retry_after: dict[str, float] = {}
        self._introspection_snapshot: dict[str, object] = {}
        self._introspection_loaded_at = 0.0

    def cached_value(self, service_name: str, path: str) -> float | int | None:
        if self._introspection_says_skip(service_name, path):
            self.request_introspection(service_name, path, priority=80, reason="helper skipped known-unusable path")
            return None
        entry = DbusCacheStore.value_entry(self.cache_snapshot(), dbus_path_key(service_name, path))
        has_value, value = self._cached_numeric_value(entry)
        if has_value:
            return value
        if self._cached_error_recent(entry):
            logging.debug("Auto helper suppressing fresh DBus cache error for %s %s", service_name, path)
            return None
        self.request_value(service_name, path, priority=90, reason="helper DBus cache miss")
        return None

    def semantic_value(self, key: GatewayReadKey, *, reason: str) -> float | int | None:
        read_key = require_gateway_read_key(key)
        snapshot = self.cache_snapshot()
        cached = gateway_read_value(snapshot, read_key, max_age_seconds=self.cache_max_age_seconds())
        value = self._numeric_or_none(coerce_dbus_numeric(cached))
        if value is not None:
            return value
        entry = DbusCacheStore.value_entry(snapshot, read_key)
        if self._cached_error_recent(entry):
            logging.debug("Auto helper suppressing fresh DBus cache error for read key %s", read_key)
            return None
        self.request_read_key(read_key, reason=reason)
        return None

    def child_nodes(self, service_name: str, path: str) -> list[str]:
        children = path_children(self._fresh_introspection_snapshot(), service_name, path)
        if children:
            return children
        self.request_introspection(
            service_name,
            path,
            priority=60,
            reason="helper child-node discovery requested",
        )
        return []

    def request_introspection(
        self,
        service_name: str,
        path: str,
        *,
        priority: int,
        reason: str,
    ) -> None:
        try:
            self._command_client().enqueue_command(
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

    def request_value(self, service_name: str, path: str, *, priority: int, reason: str) -> None:
        try:
            self._command_client().enqueue_command(
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

    def request_read_key(self, key: GatewayReadKey, *, reason: str) -> None:
        try:
            self._command_client().request_read_key(
                key,
                priority="read",
                source="auto-input-helper",
                reason=reason,
            )
        except OSError:
            return

    def request_service_refresh(self) -> bool:
        try:
            self._command_client().enqueue_command(
                {
                    "kind": "refresh_services",
                    "source": "auto-input-helper",
                    "priority": "discovery",
                    "coalesce_key": "refresh-services",
                }
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logging.debug("Gateway service refresh request failed: %s", error)
            return False
        return True

    def _command_client(self) -> GatewayCommandClientPort:
        if self._client is None:
            self._client = GatewayClient(gateway_paths(self.settings.dbus_gateway_run_dir))
        return self._client

    def cache_snapshot(self) -> dict[str, object]:
        cache_path = self.settings.dbus_gateway_cache_path or self._command_client().paths.cache_path
        return DbusCacheStore.load_snapshot(cache_path, max_age_seconds=self.cache_max_age_seconds())

    def cache_max_age_seconds(self) -> float:
        return max(0.0, self.settings.dbus_gateway_max_age_seconds)

    def service_names(self) -> list[str]:
        now = time.time()
        if now < self._list_backoff_until:
            return []
        services_value: object = self.cache_snapshot().get("services")
        if is_object_mapping(services_value):
            self._list_failures = 0
            self._list_backoff_until = 0.0
            return [str(name) for name in services_value]
        self.request_service_refresh()
        self._list_failures += 1
        delay = self.settings.auto_dbus_backoff_base_seconds * (2 ** max(0, self._list_failures - 1))
        if self.settings.auto_dbus_backoff_max_seconds > 0.0:
            delay = min(delay, self.settings.auto_dbus_backoff_max_seconds)
        self._list_backoff_until = now + max(0.0, delay)
        return []

    def service_available(self, service_name: str) -> bool:
        return bool(service_name and service_name in self.service_names())

    def source_retry_ready(self, key: str) -> bool:
        return time.time() >= self._source_retry_after.get(key, 0.0)

    def delay_source_retry(self, key: str) -> None:
        delay = max(1.0, self.settings.auto_dbus_backoff_base_seconds or 5.0)
        self._source_retry_after[key] = time.time() + delay

    def _fresh_introspection_snapshot(self) -> dict[str, object]:
        current = time.time()
        if current - self._introspection_loaded_at > 5.0:
            self._introspection_snapshot = load_introspection_snapshot(
                self.settings.dbus_introspection_snapshot_path,
                max_age_seconds=self.settings.dbus_introspection_max_age_seconds,
                now=current,
            )
            self._introspection_loaded_at = current
        return self._introspection_snapshot

    def _introspection_says_skip(self, service_name: str, path: str) -> bool:
        skip, reason = path_unusable_until(self._fresh_introspection_snapshot(), service_name, path)
        if skip:
            logging.debug("Auto helper skipping %s %s from DBus introspection cache: %s", service_name, path, reason)
        return skip

    def _cached_error_recent(self, entry: Mapping[str, object] | None) -> bool:
        if entry is None or str(entry.get("status")) != "error":
            return False
        raw_error_at = coerce_dbus_numeric(entry.get("error_at"))
        error_at = float(raw_error_at) if isinstance(raw_error_at, (int, float)) else 0.0
        retry = max(1.0, min(300.0, self.settings.dbus_gateway_error_retry_seconds))
        return error_at > 0.0 and time.time() - error_at < retry

    @classmethod
    def _cached_numeric_value(cls, entry: Mapping[str, object] | None) -> tuple[bool, float | int | None]:
        value = cls._cached_value(entry)
        if value is _CACHE_VALUE_MISSING:
            return False, None
        return True, cls._numeric_or_none(value)

    @staticmethod
    def _cached_value(entry: Mapping[str, object] | None) -> object:
        if entry is None or str(entry.get("status")) != "fresh":
            return _CACHE_VALUE_MISSING
        return coerce_dbus_numeric(entry.get("value"))

    @staticmethod
    def _numeric_or_none(value: object) -> float | int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value
