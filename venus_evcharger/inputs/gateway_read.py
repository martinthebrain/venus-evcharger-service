# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic gateway reads for main-service Auto inputs."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.core.controller_contracts import ControllerAssemblyContract
from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_gateway import (
    DbusCacheStore,
    GatewayClient,
    GatewayReadKey,
    gateway_paths,
    gateway_read_value,
    require_gateway_read_key,
)


def numeric_gateway_value(value: object) -> float | int | None:
    """Return a scalar numeric gateway value or None for unusable payloads."""
    coerced_value: object = coerce_dbus_numeric(value)
    if isinstance(coerced_value, bool):
        return None
    if isinstance(coerced_value, (float, int)):
        return coerced_value
    return None


class GatewayInputReader(ControllerAssemblyContract):
    """Read semantic Auto input values from the DBus gateway cache."""

    def _dbus_module(self) -> Any:
        """Direct DBus access is forbidden outside the gateway adapter."""
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    @staticmethod
    def _coerce_dbus_value(value: Any) -> float | int | None:
        """Convert raw DBus values to numbers where possible."""
        return numeric_gateway_value(value)

    @staticmethod
    def _mark_dbus_success(svc: Any) -> None:
        """Record a successful gateway-backed DBus read."""
        svc._last_dbus_ok_at = time.time()
        svc.mark_recovery("dbus", "DBus reads recovered")

    def get_gateway_read_value(
        self,
        key: GatewayReadKey,
        *,
        reason: str,
    ) -> float | int | None:
        """Read one semantic gateway value without exposing DBus service/path details."""
        read_key = require_gateway_read_key(key)
        snapshot = self._gateway_snapshot()
        value = gateway_read_value(snapshot, read_key, max_age_seconds=self._gateway_cache_max_age_seconds())
        numeric_value = self._coerce_dbus_value(value)
        if numeric_value is not None:
            self._mark_dbus_success(self.service)
            return numeric_value
        try:
            self._gateway_client().request_read_key(
                read_key,
                priority="read",
                reason=reason,
                source="evcharger-inputs",
            )
        except OSError:
            pass
        self.service.mark_failure("dbus")
        return None

    def _gateway_snapshot(self) -> dict[str, Any]:
        svc = self._gateway_owner()
        configured_cache_path = getattr(svc, "dbus_gateway_cache_path", None)
        cache_path = str(configured_cache_path).strip() if configured_cache_path else ""
        if not cache_path:
            configured_run_dir = getattr(svc, "dbus_gateway_run_dir", None)
            run_dir = str(configured_run_dir).strip() if configured_run_dir else ""
            cache_path = gateway_paths(run_dir or None).cache_path
        return DbusCacheStore.load_snapshot(cache_path)

    def _gateway_cache_max_age_seconds(self) -> float:
        owner = self._gateway_owner()
        configured = getattr(owner, "dbus_gateway_max_age_seconds", None)
        if not configured:
            return 10.0
        return max(0.0, float(configured))

    def _gateway_client(self) -> GatewayClient:
        svc = self._gateway_owner()
        configured_run_dir = getattr(svc, "dbus_gateway_run_dir", None)
        run_dir = str(configured_run_dir).strip() if configured_run_dir else ""
        return GatewayClient(gateway_paths(run_dir or None))

    def _gateway_owner(self) -> Any:
        port_or_service = self.service
        return getattr(port_or_service, "_service", port_or_service)
