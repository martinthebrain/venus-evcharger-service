#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Advisory DBus introspection cache helpers.

The EV charger runtime treats this cache as optional ground truth: useful when
fresh, ignored when missing or stale. Runtime reads must never block on this
cache being updated.
"""

from __future__ import annotations

import json
import time
from typing import Any

from venus_evcharger.core.shared import compact_json, write_text_atomically


DBUS_INTROSPECTION_SCHEMA_VERSION = 1
UNUSABLE_PATH_STATUSES = frozenset(("known-missing", "unresponsive-backoff"))


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_introspection_snapshot(path: str, *, max_age_seconds: float, now: float | None = None) -> dict[str, Any]:
    """Load a fresh introspection snapshot, returning an empty mapping when unusable."""
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return {}
    try:
        with open(normalized_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:  # pylint: disable=broad-except
        return {}
    if not isinstance(payload, dict):
        return {}
    if int(payload.get("schema_version", 0) or 0) != DBUS_INTROSPECTION_SCHEMA_VERSION:
        return {}
    current = time.time() if now is None else float(now)
    heartbeat = _optional_float(payload.get("heartbeat_at", payload.get("captured_at")))
    if heartbeat is None:
        return {}
    age = current - heartbeat
    return payload if 0.0 <= age <= max(0.0, float(max_age_seconds)) else {}


def service_path_finding(snapshot: dict[str, Any], service_name: str, path: str) -> dict[str, Any]:
    """Return one cached service/path finding from a loaded snapshot."""
    services = snapshot.get("services", {})
    if not isinstance(services, dict):
        return {}
    service_payload = services.get(str(service_name), {})
    if not isinstance(service_payload, dict):
        return {}
    paths = service_payload.get("paths", {})
    if not isinstance(paths, dict):
        return {}
    finding = paths.get(str(path), {})
    return finding if isinstance(finding, dict) else {}


def path_unusable_until(snapshot: dict[str, Any], service_name: str, path: str, now: float | None = None) -> tuple[bool, str]:
    """Return whether a cached finding says the path should currently be skipped."""
    finding = service_path_finding(snapshot, service_name, path)
    status = str(finding.get("status", "") or "")
    if status not in UNUSABLE_PATH_STATUSES:
        return False, ""
    current = time.time() if now is None else float(now)
    retry_at = _optional_float(finding.get("retry_after")) or 0.0
    if retry_at > current:
        return True, status
    return status == "known-missing", status if status == "known-missing" else ""


def path_children(snapshot: dict[str, Any], service_name: str, path: str) -> list[str]:
    """Return cached child nodes for one service/path when the finding is fresh."""
    finding = service_path_finding(snapshot, service_name, path)
    if str(finding.get("status", "") or "") != "fresh":
        return []
    children = finding.get("children", [])
    if not isinstance(children, list):
        return []
    return [str(child) for child in children if str(child or "").strip()]


def load_owner_introspection_snapshot(owner: Any, *, now: float | None = None) -> dict[str, Any]:
    """Load and briefly cache the advisory snapshot for a service/helper object."""
    snapshot_path = str(getattr(owner, "dbus_introspection_snapshot_path", "") or "").strip()
    if not snapshot_path:
        return {}
    current = time.time() if now is None else float(now)
    cache_loaded_at = float(getattr(owner, "_dbus_introspection_snapshot_loaded_at", 0.0) or 0.0)
    if current - cache_loaded_at > 5.0:
        snapshot = load_introspection_snapshot(
            snapshot_path,
            max_age_seconds=float(getattr(owner, "dbus_introspection_max_age_seconds", 900.0) or 900.0),
            now=current,
        )
        owner._dbus_introspection_snapshot_cache = snapshot
        owner._dbus_introspection_snapshot_loaded_at = current
    cached_snapshot = getattr(owner, "_dbus_introspection_snapshot_cache", {})
    return cached_snapshot if isinstance(cached_snapshot, dict) else {}


def owner_path_unusable(owner: Any, service_name: str, path: str, *, now: float | None = None) -> tuple[bool, str]:
    """Return whether the owner's fresh advisory snapshot says to skip this path."""
    current = time.time() if now is None else float(now)
    return path_unusable_until(load_owner_introspection_snapshot(owner, now=current), service_name, path, current)


def owner_path_children(owner: Any, service_name: str, path: str, *, now: float | None = None) -> list[str]:
    """Return cached child nodes for one owner/service/path."""
    return path_children(load_owner_introspection_snapshot(owner, now=now), service_name, path)


def request_owner_introspection(
    owner: Any,
    service_name: str,
    path: str,
    *,
    priority: int = 100,
    reason: str = "",
    source: str = "",
    now: float | None = None,
) -> bool:
    """Queue a best-effort request using request-path configuration from an object."""
    request_path = str(getattr(owner, "dbus_introspection_request_path", "") or "").strip()
    if not request_path:
        return False
    return request_introspection(
        request_path,
        service_name,
        path,
        priority=priority,
        reason=reason,
        source=source,
        now=now,
    )


def request_introspection(
    request_path: str,
    service_name: str,
    path: str,
    *,
    priority: int = 100,
    reason: str = "",
    source: str = "",
    now: float | None = None,
) -> bool:
    """Queue a best-effort priority introspection request for the background worker."""
    normalized_request_path = str(request_path or "").strip()
    normalized_service = str(service_name or "").strip()
    normalized_path = str(path or "").strip()
    if not normalized_request_path or not normalized_service or not normalized_path:
        return False
    payload = _load_request_payload(normalized_request_path)
    requests = payload.setdefault("requests", [])
    if not isinstance(requests, list):
        requests = []
        payload["requests"] = requests
    requests.append(
        {
            "service": normalized_service,
            "path": normalized_path,
            "priority": int(priority),
            "reason": str(reason or ""),
            "source": str(source or ""),
            "requested_at": time.time() if now is None else float(now),
        }
    )
    try:
        write_text_atomically(normalized_request_path, compact_json(payload))
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def _load_request_payload(request_path: str) -> dict[str, Any]:
    try:
        with open(request_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:  # pylint: disable=broad-except
        payload = {}
    return payload if isinstance(payload, dict) else {}
