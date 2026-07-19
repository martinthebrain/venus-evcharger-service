# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic gateway reads for main-service Auto inputs."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Protocol, TypeGuard

from venus_evcharger.core.contracts_basic import finite_float_or_none
from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    GatewayClient,
    GatewayReadKey,
    dbus_path_key,
    gateway_paths,
    require_gateway_read_key,
)
from venus_evcharger.dbus_gateway_command_types import CommandPayload
from venus_evcharger.ports.dbus import DbusInputReaderPort, DbusRawValue


def numeric_gateway_value(value: object) -> float | int | None:
    """Return a scalar numeric gateway value or None for unusable payloads."""
    coerced_value: object = coerce_dbus_numeric(value)
    if isinstance(coerced_value, (float, int)):
        return coerced_value
    return None


def raw_gateway_value(value: object) -> DbusRawValue:
    """Return one scalar raw value while preserving textual DBus payloads."""
    if isinstance(value, str):
        return value
    return numeric_gateway_value(value)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


class GatewayReadPort(Protocol):
    """Semantic and raw gateway reads consumed by input components."""

    def read_semantic_value(self, key: GatewayReadKey, *, reason: str) -> float | int | None: ...
    def get_dbus_value(self, service_name: str, path: str) -> DbusRawValue: ...
    def list_dbus_services(self) -> list[str]: ...


class SourceHealthPort(Protocol):
    """Retry and recovery policy consumed by semantic input readers."""

    def retry_ready(self, source_key: str, now: float) -> bool: ...
    def recovered(self, source_key: str, message: str, *args: object) -> None: ...
    def failed(
        self,
        source_key: str,
        now: float,
        warning_key: str,
        warning_interval: float,
        warning_message: str,
        *args: object,
    ) -> None: ...


class GatewayInputReader:
    """Read semantic Auto input values from the DBus gateway cache."""

    def __init__(self, port: DbusInputReaderPort) -> None:
        self._port = port

    @staticmethod
    def _coerce_dbus_value(value: object) -> float | int | None:
        """Convert raw DBus values to numbers where possible."""
        return numeric_gateway_value(value)

    @staticmethod
    def _mark_dbus_success(port: DbusInputReaderPort) -> None:
        """Record a successful gateway-backed DBus read."""
        port.service._last_dbus_ok_at = time.time()
        port.mark_recovery("dbus", "DBus reads recovered")

    def read_semantic_value(
        self,
        key: GatewayReadKey,
        *,
        reason: str,
    ) -> float | int | None:
        """Read one semantic gateway value without exposing DBus service/path details."""
        read_key = require_gateway_read_key(key)
        now = time.time()
        snapshot = self._gateway_snapshot(now=now)
        value = self._fresh_gateway_value(snapshot, read_key, now)
        numeric_value = self._coerce_dbus_value(value)
        if numeric_value is not None:
            self._mark_dbus_success(self._port)
            return numeric_value
        try:
            self._gateway_client().request_read_key(
                read_key,
                reason=reason,
                source="evcharger-inputs",
            )
        except OSError:
            pass
        self._port.mark_failure("dbus")
        return None

    def get_dbus_value(self, service_name: str, path: str) -> DbusRawValue:
        """Read one raw service/path value through the gateway cache."""
        now = time.time()
        snapshot = self._gateway_snapshot(now=now)
        value = self._fresh_gateway_value(snapshot, dbus_path_key(service_name, path), now)
        raw_value = raw_gateway_value(value)
        if raw_value is not None:
            self._mark_dbus_success(self._port)
            return raw_value
        try:
            self._gateway_client().request_raw_value(
                service_name,
                path,
                reason="main input cache miss",
                source="evcharger-inputs",
            )
        except OSError:
            pass
        self._port.mark_failure("dbus")
        return None

    def list_dbus_services(self) -> list[str]:
        """List services from the gateway cache with bounded refresh backoff."""
        now = time.time()
        state = self._port.service
        if now < state._dbus_list_backoff_until:
            raise RuntimeError("DBus list backoff active")
        names = self._list_dbus_names()
        if names:
            state._dbus_list_failures = 0
            state._dbus_list_backoff_until = 0.0
            self._mark_dbus_success(self._port)
            return names
        self._gateway_client().enqueue_command(
            {"kind": "refresh_services", "source": "evcharger-inputs", "priority": "read"}
        )
        state._dbus_list_failures += 1
        self._port.mark_failure("dbus")
        state._dbus_list_backoff_until = now + self._dbus_list_backoff_delay()
        return []

    def _list_dbus_names(self) -> list[str]:
        services = self._gateway_snapshot().get("services")
        if _is_object_list(services):
            return [str(name) for name in services]
        if _is_object_mapping(services):
            return [str(name) for name in services]
        return []

    def _dbus_list_backoff_delay(self) -> float:
        return float(
            min(
                self._port.service.auto_dbus_backoff_max_seconds,
                self._port.service.auto_dbus_backoff_base_seconds * (2 ** (self._port.service._dbus_list_failures - 1)),
            )
        )

    def _fresh_gateway_value(self, snapshot: CommandPayload, key: str, now: float) -> object:
        entry = DbusCacheStore.value_entry(snapshot, key)
        if entry is None:
            return None
        return entry.get("value") if self._gateway_entry_is_fresh(entry, now) else None

    def _gateway_entry_is_fresh(self, entry: CommandPayload, now: float) -> bool:
        if entry.get("status") not in ("fresh", "stale"):
            return False
        updated_at = finite_float_or_none(entry.get("updated_at"))
        if updated_at is None:
            return False
        age_seconds = float(now) - updated_at
        return 0.0 <= age_seconds <= self._gateway_cache_max_age_seconds()

    def _gateway_snapshot(self, *, now: float | None = None) -> CommandPayload:
        cache_path = self._port.gateway_cache_path()
        if not cache_path:
            run_dir = self._port.gateway_run_dir()
            cache_path = gateway_paths(run_dir or None).cache_path
        current = time.time() if now is None else float(now)
        return DbusCacheStore.load_snapshot(
            cache_path,
            max_age_seconds=self._gateway_cache_max_age_seconds(),
            now=current,
        )

    def _gateway_cache_max_age_seconds(self) -> float:
        return self._port.gateway_max_age_seconds()

    def _gateway_client(self) -> GatewayClient:
        run_dir = self._port.gateway_run_dir()
        return GatewayClient(gateway_paths(run_dir or None))


class InputSourceHealth:
    """Own retry, recovery, and failure policy shared by semantic inputs."""

    def __init__(self, port: DbusInputReaderPort) -> None:
        self._port = port

    def retry_ready(self, source_key: str, now: float) -> bool:
        return self._port.source_retry_ready(source_key, now)

    def recovered(self, source_key: str, message: str, *args: object) -> None:
        self._port.mark_recovery(source_key, message, *args)

    def failed(
        self,
        source_key: str,
        now: float,
        warning_key: str,
        warning_interval: float,
        warning_message: str,
        *args: object,
    ) -> None:
        self._port.mark_failure(source_key)
        self._port.delay_source_retry(source_key, now)
        self._port.warning_throttled(warning_key, warning_interval, warning_message, *args)
