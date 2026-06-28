#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter process mixins.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as xml_et
from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.dbus_adapter_process_protocol_introspection import DbusAdapterIntrospectionSnapshotContext
from venus_evcharger.dbus_introspection import DBUS_INTROSPECTION_SCHEMA_VERSION


class DbusAdapterIntrospectionSnapshotMixin:
    def write_introspection_snapshot(self: DbusAdapterIntrospectionSnapshotContext) -> None:
        if not self.dbus_introspection_enabled or not self.dbus_introspection_snapshot_path:
            return
        now = time.time()
        payload = {
            "schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION,
            "captured_at": now,
            "heartbeat_at": now,
            "worker_state": "gateway",
            "writer_pid": os.getpid(),
            "queue_depth": self._introspection_queue_depth,
            "last_full_scan_at": self._last_introspection_full_scan_at,
            "services": self.introspection_services_snapshot(now),
        }
        try:
            write_text_atomically(self.dbus_introspection_snapshot_path, compact_json(payload))
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logging.debug("Unable to write DBus introspection snapshot %s: %s", self.dbus_introspection_snapshot_path, error)

    def introspection_services_snapshot(self: DbusAdapterIntrospectionSnapshotContext, now: float) -> dict[str, Any]:
        services: dict[str, Any] = {}
        for key, entry in self.introspection_cache_entries():
            service, path = self.split_introspection_cache_key(key)
            self.add_introspection_service_entry(services, service, path, entry, now)
        return services

    def introspection_cache_entries(self: DbusAdapterIntrospectionSnapshotContext) -> list[tuple[str, dict[str, Any]]]:
        return [
            (key, entry)
            for key, entry in self.cache.values.items()
            if key.startswith("introspection:") and isinstance(entry, dict)
        ]

    def add_introspection_service_entry(
        self: DbusAdapterIntrospectionSnapshotContext,
        services: dict[str, Any],
        service: str,
        path: str,
        entry: Mapping[str, Any],
        now: float,
    ) -> None:
        if not service:
            return
        service_payload = services.setdefault(service, {"paths": {}, "last_updated_at": now})
        service_payload.setdefault("paths", {})[path] = self.introspection_finding(entry, now)
        service_payload["last_updated_at"] = max(
            float(service_payload.get("last_updated_at", 0.0) or 0.0),
            float(entry.get("updated_at", now) or now),
        )

    @staticmethod
    def split_introspection_cache_key(key: str) -> tuple[str, str]:
        remainder = key[len("introspection:") :]
        service, separator, path = remainder.partition(":")
        return service, path if separator else "/"

    @staticmethod
    def introspection_finding(entry: Mapping[str, Any], now: float) -> dict[str, Any]:
        status = str(entry.get("status", "") or "")
        if status == "fresh":
            return _fresh_introspection_finding(entry, now)
        return _backoff_introspection_finding(entry, status, now)

    @staticmethod
    def parse_introspection_xml(xml_data: object) -> tuple[list[str], list[str]]:
        try:
            root = xml_et.fromstring(str(xml_data))
        except xml_et.ParseError:
            return [], []
        return _xml_names(root, "interface"), _xml_names(root, "node")


def _fresh_introspection_finding(entry: Mapping[str, Any], now: float) -> dict[str, Any]:
    interfaces, children = DbusAdapterIntrospectionSnapshotMixin.parse_introspection_xml(entry.get("value", ""))
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


def _backoff_introspection_finding(entry: Mapping[str, Any], status: str, now: float) -> dict[str, Any]:
    return {
        "status": "unresponsive-backoff" if status == "error" else status or "unknown",
        "confidence": 0.55,
        "interfaces": [],
        "children": [],
        "source": entry.get("source", "gateway"),
        "reason": "gateway-introspection",
        "last_success_at": None,
        "last_error": str(entry.get("last_error", "") or ""),
        "retry_after": now + 900.0,
    }


def _xml_names(root: Any, tag: str) -> list[str]:
    return [str(node.attrib.get("name", "")) for node in root.findall(tag) if node.attrib.get("name")]
