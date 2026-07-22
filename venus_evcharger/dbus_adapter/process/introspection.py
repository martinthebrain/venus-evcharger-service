#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled DBus introspection owned by the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import time

import dbus

from venus_evcharger.core.shared import config_get_float
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.process.protocols.introspection import DbusAdapterIntrospectionContext
from venus_evcharger.dbus_adapter.process.runtime import DbusAdapterRuntime
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_gateway_core import float_or_default, float_or_zero
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.energy import ENERGY_REFRESH_COMMAND_KIND, EnergyRefreshRequest

OPTIONAL_INTROSPECTION_PRIORITY_MIN = 90
ENERGY_REFRESH_KEYS_BY_SCOPE: dict[str, tuple[str, ...]] = {
    "all": ("grid_power_w", "pv_power_w", "battery_soc"),
    "grid": ("grid_power_w",),
    "pv": ("pv_power_w",),
    "battery": ("battery_soc",),
}


class DbusAdapterIntrospection(DbusAdapterRuntime):
    def enqueue_introspection_command(
        self: DbusAdapterIntrospectionContext,
        service: str,
        path: str,
        *,
        priority: int,
        source: str,
        reason: str,
    ) -> None:
        self.commands.enqueue(
            {
                "kind": "introspect",
                "service": service,
                "path": path,
                "priority": "discovery" if priority < OPTIONAL_INTROSPECTION_PRIORITY_MIN else "optional",
                "source": source,
                "reason": reason,
                "timeout": config_get_float(self.config["DEFAULT"], "DbusIntrospectionTimeoutSeconds", 1.0),
                "coalesce_key": f"introspect:{service}:{path}",
            }
        )

    def enqueue_background_introspection_if_due(self: DbusAdapterIntrospectionContext) -> None:
        now = time.time()
        if not self.background_introspection_due(now):
            return
        self._last_introspection_full_scan_at = now
        for target in self.energy_discovery.introspection_targets():
            self.enqueue_introspection_command(
                target.service,
                target.path,
                priority=target.priority,
                source=target.source,
                reason=target.reason,
            )

    def background_introspection_due(self: DbusAdapterIntrospectionContext, now: float) -> bool:
        interval = max(
            60.0,
            config_get_float(self.config["DEFAULT"], "DbusIntrospectionFullScanIntervalSeconds", 21600.0),
        )
        return (
            self.dbus_introspection_enabled
            and now - self._last_introspection_full_scan_at >= interval
            and bool(self.cache.services)
            and self.circuit.allows_priority("discovery")
        )

    def process_non_write_command(self: DbusAdapterIntrospectionContext, command: CommandMapping) -> CommandOutcome:
        raw_kind = command.get("kind") or command.get("type")
        if not raw_kind:
            return "dropped"
        kind = str(raw_kind)
        handlers = {
            ENERGY_REFRESH_COMMAND_KIND: self.refresh_energy_inputs_command,
            "introspect": self.introspect_command_if_healthy,
        }
        return handlers.get(kind, _drop_command)(command)

    def refresh_energy_inputs_command(
        self: DbusAdapterIntrospectionContext,
        command: CommandMapping,
    ) -> CommandOutcome:
        try:
            request = EnergyRefreshRequest.from_command(command)
        except (KeyError, TypeError, ValueError):
            return "dropped"
        keys = self._energy_refresh_keys(request)
        if request.scope == "energy_source" and not keys:
            return "dropped"
        if request.scope in {"all", "topology"}:
            self.discovery.force_due()
            self._last_introspection_full_scan_at = 0.0
        self.read_scheduler.force_due(self._stale_refresh_keys(keys, request.max_age_seconds))
        return "applied"

    def _energy_refresh_keys(
        self: DbusAdapterIntrospectionContext,
        request: EnergyRefreshRequest,
    ) -> tuple[str, ...]:
        fixed_keys = ENERGY_REFRESH_KEYS_BY_SCOPE.get(request.scope)
        if fixed_keys is not None:
            return fixed_keys
        if request.scope != "energy_source" or request.source_id is None:
            return ()
        return self.energy_discovery.read_keys_for_source(request.source_id)

    def _stale_refresh_keys(
        self: DbusAdapterIntrospectionContext,
        keys: tuple[str, ...],
        max_age_seconds: float,
    ) -> tuple[str, ...]:
        now = time.time()
        return tuple(
            key
            for key in keys
            if now - float_or_zero(self.cache.values.get(key, {}).get("confirmed_at")) > max_age_seconds
        )

    def introspect_command_if_healthy(
        self: DbusAdapterIntrospectionContext,
        command: CommandMapping,
    ) -> CommandOutcome:
        if self.circuit.state() != "ok":
            return "deferred"
        return self.introspect_command(command)

    def introspect_command(self: DbusAdapterIntrospectionContext, command: CommandMapping) -> CommandOutcome:
        service = str(command.get("service") or "")
        path = str(command.get("path") or "/")
        if not service:
            return "dropped"
        timeout = float_or_default(command.get("timeout"), 1.0)
        outcome, xml_data = self.timed_introspection_result(service, path, timeout)
        if outcome != "applied":
            return outcome
        self.record_introspection_xml(service, path, xml_data)
        return "applied"

    def timed_introspection_result(
        self: DbusAdapterIntrospectionContext,
        service: str,
        path: str,
        timeout: float,
    ) -> tuple[CommandOutcome, object]:
        try:
            return "applied", self.timed_dbus_operation(
                "introspection", lambda: self.read_introspection_xml(service, path, timeout)
        )
        except DbusOperationDeferred:
            return "deferred", None
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            return self.drop_failed_introspection(service, path, error), None

    def read_introspection_xml(
        self: DbusAdapterIntrospectionContext,
        service: str,
        path: str,
        timeout: float,
    ) -> object:
        obj = self.connection.get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
        return iface.Introspect(timeout=timeout)

    def drop_failed_introspection(
        self: DbusAdapterIntrospectionContext,
        service: str,
        path: str,
        error: BaseException,
    ) -> CommandOutcome:
        self.cache.mark_error(
            f"introspection:{service}:{path}",
            source=f"{service}{path}",
            error=error,
            freshness_kind="diagnostic",
        )
        self._introspection_queue_depth = max(0, self._introspection_queue_depth - 1)
        logging.debug("Dropping failed DBus introspection command service=%s path=%s: %s", service, path, error)
        return "dropped"

    def record_introspection_xml(
        self: DbusAdapterIntrospectionContext,
        service: str,
        path: str,
        xml_data: object,
    ) -> None:
        self.cache.update_value(
            f"introspection:{service}:{path}",
            xml_data,
            source=f"{service}{path}",
            confidence=0.5,
            freshness_kind="diagnostic",
        )
        self._introspection_queue_depth = max(0, self._introspection_queue_depth - 1)


def _drop_command(_command: CommandMapping) -> CommandOutcome:
    return "dropped"
