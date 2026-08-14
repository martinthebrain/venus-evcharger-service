#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled DBus introspection owned by the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import time

from venus_evcharger.core.shared import config_get_float
from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall, dbus_call_operation
from venus_evcharger.dbus_adapter.contracts import (
    CommandCompletion,
    CommandExecution,
    CommandOutcome,
)
from venus_evcharger.dbus_adapter.process.protocols.introspection import DbusAdapterIntrospectionContext
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_adapter.read.keys import SEMANTIC_ENERGY_READ_KEYS
from venus_evcharger.dbus_gateway_core import float_or_default, float_or_zero
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.energy import ENERGY_REFRESH_COMMAND_KIND, EnergyRefreshRequest

OPTIONAL_INTROSPECTION_PRIORITY_MIN = 90
ENERGY_REFRESH_KEYS_BY_SCOPE: dict[str, tuple[str, ...]] = {
    "all": SEMANTIC_ENERGY_READ_KEYS,
    "grid": ("grid_power_w",),
    "pv": ("pv_power_w",),
    "battery": tuple(key for key in SEMANTIC_ENERGY_READ_KEYS if key.startswith("battery_")),
}


def _introspection_call(
    command: CommandMapping,
    command_file: str,
) -> DbusMethodCall | None:
    service = str(command.get("service") or "")
    if not service:
        return None
    path = str(command.get("path") or "/")
    return DbusMethodCall(
        service=service,
        path=path,
        interface="org.freedesktop.DBus.Introspectable",
        method_name="Introspect",
        signature="",
        rate_kind="introspection",
        metric_kind="introspection",
        source=f"{service}{path}",
        priority=str(command.get("priority") or "diagnostic"),
        timeout_seconds=float_or_default(command.get("timeout"), 1.0),
        owner_path=command_file,
    )


class DbusAdapterIntrospection:
    def __init__(self, context: DbusAdapterIntrospectionContext) -> None:
        self._context = context

    def enqueue_introspection_command(
        self,
        service: str,
        path: str,
        *,
        priority: int,
        source: str,
        reason: str,
    ) -> None:
        context = self._context
        context.commands.enqueue(
            {
                "kind": "introspect",
                "service": service,
                "path": path,
                "priority": "discovery" if priority < OPTIONAL_INTROSPECTION_PRIORITY_MIN else "optional",
                "source": source,
                "reason": reason,
                "timeout": config_get_float(
                    context.config["DEFAULT"],
                    "DbusIntrospectionTimeoutSeconds",
                    1.0,
                ),
                "coalesce_key": f"introspect:{service}:{path}",
            }
        )

    def enqueue_background_introspection_if_due(self) -> None:
        context = self._context
        now = time.time()
        if not self.background_introspection_due(now):
            return
        context._last_introspection_full_scan_at = now
        for target in context.energy_discovery.introspection_targets():
            self.enqueue_introspection_command(
                target.service,
                target.path,
                priority=target.priority,
                source=target.source,
                reason=target.reason,
            )

    def background_introspection_due(self, now: float) -> bool:
        context = self._context
        interval = max(
            60.0,
            config_get_float(
                context.config["DEFAULT"],
                "DbusIntrospectionFullScanIntervalSeconds",
                21600.0,
            ),
        )
        return (
            context.dbus_introspection_enabled
            and now - context._last_introspection_full_scan_at >= interval
            and bool(context.cache.services)
            and context.circuit.allows_priority("discovery")
        )

    def schedule_non_write_command(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        raw_kind = command.get("kind") or command.get("type")
        if not raw_kind:
            return CommandExecution.immediate("dropped")
        kind = str(raw_kind)
        handlers = {
            ENERGY_REFRESH_COMMAND_KIND: lambda: CommandExecution.immediate(
                self.refresh_energy_inputs_command(command)
            ),
            "introspect": lambda: self.schedule_introspection_if_healthy(
                command,
                command_file,
                completion,
            ),
        }
        handler = handlers.get(kind)
        return CommandExecution.immediate("dropped") if handler is None else handler()

    def refresh_energy_inputs_command(
        self,
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
            self._context.discovery.force_due()
            self._context._last_introspection_full_scan_at = 0.0
        self._context.read_scheduler.force_due(self._stale_refresh_keys(keys, request.max_age_seconds))
        return "applied"

    def _energy_refresh_keys(
        self,
        request: EnergyRefreshRequest,
    ) -> tuple[str, ...]:
        fixed_keys = ENERGY_REFRESH_KEYS_BY_SCOPE.get(request.scope)
        if fixed_keys is not None:
            configured = self._context.read_scheduler.specs
            return tuple(key for key in fixed_keys if key in configured)
        source_id = request.source_id
        if source_id is None:
            return ()
        return self._context.energy_discovery.read_keys_for_source(source_id)

    def _stale_refresh_keys(
        self,
        keys: tuple[str, ...],
        max_age_seconds: float,
    ) -> tuple[str, ...]:
        now = time.time()
        return tuple(
            key
            for key in keys
            if now - float_or_zero(self._context.cache.values.get(key, {}).get("confirmed_at")) > max_age_seconds
        )

    def schedule_introspection_if_healthy(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        if self._context.circuit.state() != "ok":
            return CommandExecution.immediate("deferred")
        return self.schedule_introspection(command, command_file, completion)

    def schedule_introspection(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        try:
            call = _introspection_call(command, command_file)
        except ValueError:
            return CommandExecution.immediate("dropped")
        if call is None:
            return CommandExecution.immediate("dropped")
        return self._submit_introspection(call, completion)

    def _submit_introspection(
        self,
        call: DbusMethodCall,
        completion: CommandCompletion,
    ) -> CommandExecution:
        try:
            self._context.operation_broker.submit(
                dbus_call_operation(
                    self._context.connection,
                    call,
                    on_success=lambda xml_data: completion(
                        self._record_introspection_outcome(
                            call.service,
                            call.path,
                            xml_data,
                        )
                    ),
                    on_error=lambda error: completion(
                        self.drop_failed_introspection(
                            call.service,
                            call.path,
                            error,
                        )
                    ),
                )
            )
            return CommandExecution.pending()
        except DbusOperationDeferred:
            return CommandExecution.immediate("deferred")
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            return CommandExecution.immediate(self.drop_failed_introspection(call.service, call.path, error))

    def _record_introspection_outcome(
        self,
        service: str,
        path: str,
        xml_data: object,
    ) -> CommandOutcome:
        self.record_introspection_xml(service, path, xml_data)
        return "applied"

    def drop_failed_introspection(
        self,
        service: str,
        path: str,
        error: BaseException,
    ) -> CommandOutcome:
        context = self._context
        context.cache.mark_error(
            f"introspection:{service}:{path}",
            source=f"{service}{path}",
            error=error,
            freshness_kind="diagnostic",
        )
        context._introspection_queue_depth = max(0, context._introspection_queue_depth - 1)
        logging.debug("Dropping failed DBus introspection command service=%s path=%s: %s", service, path, error)
        return "dropped"

    def record_introspection_xml(
        self,
        service: str,
        path: str,
        xml_data: object,
    ) -> None:
        context = self._context
        context.cache.update_value(
            f"introspection:{service}:{path}",
            xml_data,
            source=f"{service}{path}",
            confidence=0.5,
            freshness_kind="diagnostic",
        )
        context._introspection_queue_depth = max(0, context._introspection_queue_depth - 1)
