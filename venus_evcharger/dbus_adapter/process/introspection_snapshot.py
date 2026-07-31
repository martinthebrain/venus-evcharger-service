#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Introspection snapshot generation for the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TypeGuard
from xml.etree.ElementTree import Element

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.dbus_adapter.introspection_xml import parse_bounded_introspection_xml
from venus_evcharger.dbus_adapter.process.protocols.introspection import DbusAdapterIntrospectionSnapshotContext
from venus_evcharger.dbus_gateway_core import float_or_default
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload

DBUS_INTROSPECTION_SCHEMA_VERSION = 1


class DbusAdapterIntrospectionSnapshot:
    def __init__(self, context: DbusAdapterIntrospectionSnapshotContext) -> None:
        self._context = context

    def write_introspection_snapshot(self) -> None:
        context = self._context
        if not context.dbus_introspection_enabled or not context.dbus_introspection_snapshot_path:
            return
        now = time.time()
        payload = {
            "schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION,
            "captured_at": now,
            "heartbeat_at": now,
            "worker_state": "gateway",
            "writer_pid": os.getpid(),
            "queue_depth": context._introspection_queue_depth,
            "last_full_scan_at": context._last_introspection_full_scan_at,
            "services": self.introspection_services_snapshot(now),
        }
        try:
            write_text_atomically(context.dbus_introspection_snapshot_path, compact_json(payload))
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logging.debug(
                "Unable to write DBus introspection snapshot %s: %s",
                context.dbus_introspection_snapshot_path,
                error,
            )

    def introspection_services_snapshot(self, now: float) -> dict[str, CommandPayload]:
        services: dict[str, CommandPayload] = {}
        for key, entry in self.introspection_cache_entries():
            service, path = self.split_introspection_cache_key(key)
            self.add_introspection_service_entry(services, service, path, entry, now)
        return services

    def introspection_cache_entries(self) -> list[tuple[str, CommandPayload]]:
        return [
            (key, entry)
            for key, entry in self._context.cache.values.items()
            if key.startswith("introspection:")
        ]

    def add_introspection_service_entry(
        self,
        services: dict[str, CommandPayload],
        service: str,
        path: str,
        entry: CommandMapping,
        now: float,
    ) -> None:
        if not service:
            return
        service_payload = services.setdefault(service, {"paths": {}, "last_updated_at": now})
        paths = _paths_payload(service_payload)
        paths[path] = self.introspection_finding(entry, now)
        service_payload["last_updated_at"] = max(
            float_or_default(service_payload.get("last_updated_at"), 0.0),
            float_or_default(entry.get("updated_at"), now),
        )

    @staticmethod
    def split_introspection_cache_key(key: str) -> tuple[str, str]:
        remainder = key[len("introspection:") :]
        service, separator, path = remainder.partition(":")
        return service, path if separator else "/"

    @staticmethod
    def introspection_finding(entry: CommandMapping, now: float) -> CommandPayload:
        status = str(entry.get("status") or "")
        if status == "fresh":
            return _fresh_introspection_finding(entry, now)
        return _backoff_introspection_finding(entry, status, now)

    @staticmethod
    def parse_introspection_xml(xml_data: object) -> tuple[list[str], list[str]]:
        root = parse_bounded_introspection_xml(xml_data)
        if root is None:
            return [], []
        return _xml_names(root, "interface"), _xml_names(root, "node")


def _paths_payload(service_payload: CommandPayload) -> dict[str, object]:
    paths = service_payload.get("paths")
    if _is_string_object_dict(paths):
        return paths
    normalized: dict[str, object] = {}
    service_payload["paths"] = normalized
    return normalized


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    if not _is_object_dict(value):
        return False
    return all(isinstance(key, str) for key in value)


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _fresh_introspection_finding(entry: CommandMapping, now: float) -> CommandPayload:
    interfaces, children = DbusAdapterIntrospectionSnapshot.parse_introspection_xml(entry.get("value"))
    return {
        "status": "fresh",
        "confidence": entry.get("confidence", 0.8),
        "interfaces": interfaces,
        "children": children,
        "source": entry.get("source", "gateway"),
        "reason": "gateway-introspection",
        "last_success_at": entry.get("updated_at", now),
        "last_error": "",
        "retry_after": now,
    }


def _backoff_introspection_finding(entry: CommandMapping, status: str, now: float) -> CommandPayload:
    return {
        "status": "unresponsive-backoff" if status == "error" else status or "unknown",
        "confidence": 0.55,
        "interfaces": [],
        "children": [],
        "source": entry.get("source", "gateway"),
        "reason": "gateway-introspection",
        "last_success_at": None,
        "last_error": str(entry.get("last_error") or ""),
        "retry_after": now + 900.0,
    }


def _xml_names(root: Element, tag: str) -> list[str]:
    return [str(node.attrib["name"]) for node in root.findall(tag) if node.attrib.get("name")]
