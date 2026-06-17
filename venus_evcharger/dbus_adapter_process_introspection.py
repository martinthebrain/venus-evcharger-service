#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import select
import signal
import socket
import time
import xml.etree.ElementTree as xml_et
from typing import Any, Callable, Mapping

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from vedbus import VeDbusService

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.dbus_introspection import DBUS_INTROSPECTION_SCHEMA_VERSION
from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.dbus_gateway import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    FAST_READ_KEYS,
    GUI_CRITICAL_PUBLISH_PATHS,
    command_queue_class,
    dbus_path_key,
)

class DbusAdapterIntrospectionMixin:
    def _process_introspection_requests_once(self) -> None:
        if not self.dbus_introspection_enabled:
            return
        payload = self._read_introspection_request_payload()
        accepted = self._enqueue_introspection_requests(payload)
        if accepted:
            self._introspection_queue_depth += accepted
            self._clear_introspection_request_payload()

    def _enqueue_introspection_requests(self, payload: Mapping[str, Any]) -> int:
        accepted = 0
        for request in _valid_introspection_requests(payload):
            self._enqueue_introspection_command(
                request["service"],
                request["path"],
                priority=request["priority"],
                source=request["source"],
                reason=request["reason"],
            )
            accepted += 1
        return accepted

    def _read_introspection_request_payload(self) -> dict[str, Any]:
        if not self.dbus_introspection_request_path:
            return {}
        try:
            with open(self.dbus_introspection_request_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _clear_introspection_request_payload(self) -> None:
        try:
            write_text_atomically(self.dbus_introspection_request_path, compact_json({"requests": []}))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug(
                "Unable to clear DBus introspection request payload %s: %s",
                self.dbus_introspection_request_path,
                error,
            )

    def _enqueue_introspection_command(
        self,
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
                "priority": "discovery" if priority < 90 else "optional",
                "source": source,
                "reason": reason,
                "timeout": float(self.config["DEFAULT"].get("DbusIntrospectionTimeoutSeconds", 1.0) or 1.0),
                "coalesce_key": f"introspect:{service}:{path}",
            }
        )

    def _enqueue_background_introspection_if_due(self) -> None:
        now = time.time()
        if not self._background_introspection_due(now):
            return
        self._last_introspection_full_scan_at = now
        for service, path, priority, source, reason in self._background_introspection_specs():
            self._enqueue_introspection_command(service, path, priority=priority, source=source, reason=reason)

    def _background_introspection_due(self, now: float) -> bool:
        interval = max(60.0, float(self.config["DEFAULT"].get("DbusIntrospectionFullScanIntervalSeconds", 21600.0)))
        return (
            self.dbus_introspection_enabled
            and now - self._last_introspection_full_scan_at >= interval
            and bool(self.cache.services)
            and self.circuit.allows_priority("discovery")
        )

    def _background_introspection_specs(self) -> list[tuple[str, str, int, str, str]]:
        return self._grid_introspection_specs() + self._battery_introspection_specs() + self._pv_introspection_specs()

    def _grid_introspection_specs(self) -> list[tuple[str, str, int, str, str]]:
        defaults = self.config["DEFAULT"]
        service = str(defaults.get("AutoGridService", "com.victronenergy.system")).strip()
        paths = (
            str(defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power")).strip(),
            str(defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power")).strip(),
            str(defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power")).strip(),
        )
        return [(service, path, 80, "grid", "configured-grid-path") for path in paths if service and path]

    def _battery_introspection_specs(self) -> list[tuple[str, str, int, str, str]]:
        path = str(self.config["DEFAULT"].get("AutoBatterySocPath", "/Soc")).strip()
        services = self._configured_or_prefixed_services("AutoBatteryService", "AutoBatteryServicePrefix", "com.victronenergy.battery")
        return [(service, path, 70, "battery", "battery-service-discovery") for service in services if path]

    def _pv_introspection_specs(self) -> list[tuple[str, str, int, str, str]]:
        path = str(self.config["DEFAULT"].get("AutoPvPath", "/Ac/Power")).strip()
        services = self._configured_or_prefixed_services("AutoPvService", "AutoPvServicePrefix", "com.victronenergy.pvinverter")
        return [(service, path, 30, "pv", "pv-service-discovery") for service in services if path]

    def _configured_or_prefixed_services(self, explicit_key: str, prefix_key: str, default_prefix: str) -> list[str]:
        defaults = self.config["DEFAULT"]
        explicit = str(defaults.get(explicit_key, "")).strip()
        if explicit:
            return [explicit] if explicit in self.cache.services else []
        prefix = str(defaults.get(prefix_key, default_prefix)).strip()
        return sorted(name for name in self.cache.services if name.startswith(prefix))[:10]

    def _process_non_write_command(self, command: Mapping[str, Any]) -> CommandOutcome:
        kind = str(command.get("kind") or command.get("type") or "")
        handlers = {
            "refresh_value": self.read_executor.refresh_requested_value,
            "refresh_services": self._refresh_services_command,
            "introspect": self._introspect_command_if_healthy,
        }
        return handlers.get(kind, _drop_command)(command)

    def _refresh_services_command(self, _command: Mapping[str, Any]) -> CommandOutcome:
        if self.circuit.state() != "ok":
            return "deferred"
        self.cache.update_services(self._list_services())
        return "applied"

    def _introspect_command_if_healthy(self, command: Mapping[str, Any]) -> CommandOutcome:
        if self.circuit.state() != "ok":
            return "deferred"
        return self._introspect_command(command)

    def _introspect_command(self, command: Mapping[str, Any]) -> CommandOutcome:
        service = str(command.get("service") or "")
        path = str(command.get("path") or "/")
        if not service:
            return "dropped"
        timeout = float(command.get("timeout", 1.0))
        outcome, xml_data = self._timed_introspection_result(service, path, timeout)
        if outcome != "applied":
            return outcome
        self._record_introspection_xml(service, path, xml_data)
        return "applied"

    def _timed_introspection_result(self, service: str, path: str, timeout: float) -> tuple[CommandOutcome, Any]:
        try:
            return "applied", self._timed("introspection", lambda: self._read_introspection_xml(service, path, timeout))
        except DbusOperationDeferred:
            return "deferred", None
        except Exception as error:  # pylint: disable=broad-except
            return self._drop_failed_introspection(service, path, error), None

    def _read_introspection_xml(self, service: str, path: str, timeout: float) -> Any:
        obj = self.connection.bus().get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
        return iface.Introspect(timeout=timeout)

    def _drop_failed_introspection(self, service: str, path: str, error: BaseException) -> CommandOutcome:
        self.cache.mark_error(f"introspection:{service}:{path}", source=f"{service}{path}", error=error)
        self._introspection_queue_depth = max(0, self._introspection_queue_depth - 1)
        logging.debug("Dropping failed DBus introspection command service=%s path=%s: %s", service, path, error)
        return "dropped"

    def _record_introspection_xml(self, service: str, path: str, xml_data: Any) -> None:
        self.cache.update_value(f"introspection:{service}:{path}", xml_data, source=f"{service}{path}", confidence=0.5)
        self._introspection_queue_depth = max(0, self._introspection_queue_depth - 1)


def _valid_introspection_requests(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    requests = payload.get("requests", [])
    if not isinstance(requests, list):
        return []
    normalized: list[dict[str, Any]] = []
    for request in requests:
        item = _normalized_introspection_request(request)
        if item:
            normalized.append(item)
    return normalized


def _normalized_introspection_request(request: object) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    service, path = _introspection_request_target(request)
    if not service or not path:
        return {}
    return _introspection_request_payload(request, service, path)


def _introspection_request_target(request: Mapping[str, Any]) -> tuple[str, str]:
    return str(request.get("service", "") or "").strip(), str(request.get("path", "") or "").strip()


def _introspection_request_payload(request: Mapping[str, Any], service: str, path: str) -> dict[str, Any]:
    return {
        "service": service,
        "path": path,
        "priority": _introspection_request_priority(request),
        "source": str(request.get("source", "request") or "request"),
        "reason": str(request.get("reason", "requested") or "requested"),
    }


def _introspection_request_priority(request: Mapping[str, Any]) -> int:
    return int(request.get("priority", 100) or 100)


def _drop_command(_command: Mapping[str, Any]) -> CommandOutcome:
    return "dropped"

