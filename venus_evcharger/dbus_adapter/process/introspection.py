#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled DBus introspection owned by the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import TypedDict

import dbus

from venus_evcharger.core.shared import compact_json, config_get_float, write_text_atomically
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.process.protocols.introspection import DbusAdapterIntrospectionContext
from venus_evcharger.dbus_adapter.process.runtime import DbusAdapterRuntime
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_gateway_command_types import CommandMapping, CommandPayload
from venus_evcharger.dbus_gateway_core import float_or_default

OPTIONAL_INTROSPECTION_PRIORITY_MIN = 90


class IntrospectionRequest(TypedDict):
    service: str
    path: str
    priority: int
    source: str
    reason: str


class DbusAdapterIntrospection(DbusAdapterRuntime):
    def process_introspection_requests_once(self: DbusAdapterIntrospectionContext) -> None:
        if not self.dbus_introspection_enabled:
            return
        payload = self.introspection_request_payload()
        accepted = self.enqueue_introspection_requests(payload)
        if accepted:
            self._introspection_queue_depth += accepted
            self.clear_introspection_request_payload()

    def enqueue_introspection_requests(self: DbusAdapterIntrospectionContext, payload: CommandMapping) -> int:
        accepted = 0
        for request in _valid_introspection_requests(payload):
            self.enqueue_introspection_command(
                request["service"],
                request["path"],
                priority=request["priority"],
                source=request["source"],
                reason=request["reason"],
            )
            accepted += 1
        return accepted

    def introspection_request_payload(self: DbusAdapterIntrospectionContext) -> CommandPayload:
        if not self.dbus_introspection_request_path:
            return {}
        try:
            with open(self.dbus_introspection_request_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def clear_introspection_request_payload(self: DbusAdapterIntrospectionContext) -> None:
        try:
            write_text_atomically(self.dbus_introspection_request_path, compact_json({"requests": []}))
        except (OSError, RuntimeError) as error:
            logging.debug(
                "Unable to clear DBus introspection request payload %s: %s",
                self.dbus_introspection_request_path,
                error,
            )

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
        for service, path, priority, source, reason in self.background_introspection_specs():
            self.enqueue_introspection_command(service, path, priority=priority, source=source, reason=reason)

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

    def background_introspection_specs(self: DbusAdapterIntrospectionContext) -> list[tuple[str, str, int, str, str]]:
        return self.grid_introspection_specs() + self.battery_introspection_specs() + self.pv_introspection_specs()

    def grid_introspection_specs(self: DbusAdapterIntrospectionContext) -> list[tuple[str, str, int, str, str]]:
        defaults = self.config["DEFAULT"]
        service = str(defaults.get("AutoGridService", "com.victronenergy.system")).strip()
        paths = (
            str(defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power")).strip(),
            str(defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power")).strip(),
            str(defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power")).strip(),
        )
        return [(service, path, 80, "grid", "configured-grid-path") for path in paths if service and path]

    def battery_introspection_specs(self: DbusAdapterIntrospectionContext) -> list[tuple[str, str, int, str, str]]:
        path = str(self.config["DEFAULT"].get("AutoBatterySocPath", "/Soc")).strip()
        services = self.configured_or_prefixed_services("AutoBatteryService", "AutoBatteryServicePrefix", "com.victronenergy.battery")
        return [(service, path, 70, "battery", "battery-service-discovery") for service in services if path]

    def pv_introspection_specs(self: DbusAdapterIntrospectionContext) -> list[tuple[str, str, int, str, str]]:
        path = str(self.config["DEFAULT"].get("AutoPvPath", "/Ac/Power")).strip()
        services = self.configured_or_prefixed_services("AutoPvService", "AutoPvServicePrefix", "com.victronenergy.pvinverter")
        return [(service, path, 30, "pv", "pv-service-discovery") for service in services if path]

    def configured_or_prefixed_services(
        self: DbusAdapterIntrospectionContext,
        explicit_key: str,
        prefix_key: str,
        default_prefix: str,
    ) -> list[str]:
        defaults = self.config["DEFAULT"]
        explicit = str(defaults.get(explicit_key, "")).strip()
        if explicit:
            return [explicit] if explicit in self.cache.services else []
        prefix = str(defaults.get(prefix_key, default_prefix)).strip()
        return sorted(name for name in self.cache.services if name.startswith(prefix))[:10]

    def process_non_write_command(self: DbusAdapterIntrospectionContext, command: CommandMapping) -> CommandOutcome:
        raw_kind = command.get("kind") or command.get("type")
        if not raw_kind:
            return "dropped"
        kind = str(raw_kind)
        handlers = {
            "refresh_value": self.read_executor.refresh_requested_value,
            "refresh_services": self.refresh_services_command,
            "introspect": self.introspect_command_if_healthy,
        }
        return handlers.get(kind, _drop_command)(command)

    def refresh_services_command(self: DbusAdapterIntrospectionContext, _command: CommandMapping) -> CommandOutcome:
        if self.circuit.state() != "ok":
            return "deferred"
        try:
            self.cache.update_services(self.list_services())
        except DbusOperationDeferred:
            return "deferred"
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            self.discovery.record_error(error, now=time.time())
            self.commands.remove_coalesced("refresh:services")
            return "dropped"
        self.commands.remove_coalesced("refresh:services")
        return "applied"

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


def _valid_introspection_requests(payload: CommandMapping) -> list[IntrospectionRequest]:
    requests = payload.get("requests")
    if not isinstance(requests, list):
        return []
    normalized: list[IntrospectionRequest] = []
    for request in requests:
        item = _normalized_introspection_request(request)
        if item is not None:
            normalized.append(item)
    return normalized


def _normalized_introspection_request(request: object) -> IntrospectionRequest | None:
    if not isinstance(request, dict):
        return None
    service, path = _introspection_request_target(request)
    if not service or not path:
        return None
    return _introspection_request_payload(request, service, path)


def _introspection_request_target(request: Mapping[str, object]) -> tuple[str, str]:
    return str(request.get("service") or "").strip(), str(request.get("path") or "").strip()


def _introspection_request_payload(request: Mapping[str, object], service: str, path: str) -> IntrospectionRequest:
    return {
        "service": service,
        "path": path,
        "priority": _introspection_request_priority(request),
        "source": str(request.get("source") or "request"),
        "reason": str(request.get("reason") or "requested"),
    }


def _introspection_request_priority(request: Mapping[str, object]) -> int:
    return _int_or_default(request.get("priority"), 100)


def _int_or_default(value: object, default: int) -> int:
    return int(float_or_default(value, float(default)))


def _drop_command(_command: CommandMapping) -> CommandOutcome:
    return "dropped"
