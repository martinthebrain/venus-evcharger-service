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
    payload = _read_snapshot_payload(normalized_path)
    if not _snapshot_schema_valid(payload):
        return {}
    return payload if _snapshot_fresh(payload, max_age_seconds=max_age_seconds, now=now) else {}


def _read_snapshot_payload(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _snapshot_schema_valid(payload: dict[str, Any]) -> bool:
    return int(payload.get("schema_version", 0) or 0) == DBUS_INTROSPECTION_SCHEMA_VERSION


def _snapshot_fresh(payload: dict[str, Any], *, max_age_seconds: float, now: float | None) -> bool:
    heartbeat = _optional_float(payload.get("heartbeat_at", payload.get("captured_at")))
    if heartbeat is None:
        return False
    age = (time.time() if now is None else float(now)) - heartbeat
    return 0.0 <= age <= max(0.0, float(max_age_seconds))


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
    if _retry_after_pending(finding, now):
        return True, status
    return status == "known-missing", status if status == "known-missing" else ""


def _retry_after_pending(finding: dict[str, Any], now: float | None) -> bool:
    current = time.time() if now is None else float(now)
    return (_optional_float(finding.get("retry_after")) or 0.0) > current


def path_children(snapshot: dict[str, Any], service_name: str, path: str) -> list[str]:
    """Return cached child nodes for one service/path when the finding is fresh."""
    finding = service_path_finding(snapshot, service_name, path)
    if str(finding.get("status", "") or "") != "fresh":
        return []
    return _normalized_children(finding.get("children", []))


def _normalized_children(children: Any) -> list[str]:
    if not isinstance(children, list):
        return []
    return [str(child) for child in children if str(child or "").strip()]


def load_owner_introspection_snapshot(owner: Any, *, now: float | None = None) -> dict[str, Any]:
    """Load and briefly cache the advisory snapshot for a service/helper object."""
    snapshot_path = _owner_snapshot_path(owner)
    if not snapshot_path:
        return {}
    current = time.time() if now is None else float(now)
    _refresh_owner_snapshot_if_due(owner, snapshot_path, current)
    return _owner_cached_snapshot(owner)


def _owner_snapshot_path(owner: Any) -> str:
    return str(getattr(owner, "dbus_introspection_snapshot_path", "") or "").strip()


def _refresh_owner_snapshot_if_due(owner: Any, snapshot_path: str, current: float) -> None:
    if _owner_snapshot_reload_due(owner, current):
        _reload_owner_snapshot(owner, snapshot_path, current)


def _owner_cached_snapshot(owner: Any) -> dict[str, Any]:
    cached_snapshot = getattr(owner, "_dbus_introspection_snapshot_cache", {})
    return cached_snapshot if isinstance(cached_snapshot, dict) else {}


def _owner_snapshot_reload_due(owner: Any, current: float) -> bool:
    cache_loaded_at = float(getattr(owner, "_dbus_introspection_snapshot_loaded_at", 0.0) or 0.0)
    return current - cache_loaded_at > 5.0


def _reload_owner_snapshot(owner: Any, snapshot_path: str, current: float) -> None:
    snapshot = load_introspection_snapshot(
        snapshot_path,
        max_age_seconds=float(getattr(owner, "dbus_introspection_max_age_seconds", 900.0) or 900.0),
        now=current,
    )
    owner._dbus_introspection_snapshot_cache = snapshot
    owner._dbus_introspection_snapshot_loaded_at = current


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
    target = _request_target(request_path, service_name, path)
    if not target:
        return False
    request_file, service, dbus_path = target
    payload = _load_request_payload(request_file)
    _append_request(payload, service, dbus_path, priority=priority, reason=reason, source=source, now=now)
    return _write_request_payload(request_file, payload)


def _request_target(request_path: str, service_name: str, path: str) -> tuple[str, str, str] | None:
    request_file, service, dbus_path = (_normalized_text(request_path), _normalized_text(service_name), _normalized_text(path))
    return (request_file, service, dbus_path) if _valid_request_target(request_file, service, dbus_path) else None


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _valid_request_target(request_path: str, service_name: str, path: str) -> bool:
    return bool(request_path and service_name and path)


def _append_request(
    payload: dict[str, Any],
    service: str,
    path: str,
    *,
    priority: int,
    reason: str,
    source: str,
    now: float | None,
) -> None:
    _request_list(payload).append(
        {
            "service": service,
            "path": path,
            "priority": int(priority),
            "reason": str(reason or ""),
            "source": str(source or ""),
            "requested_at": time.time() if now is None else float(now),
        }
    )


def _request_list(payload: dict[str, Any]) -> list[Any]:
    requests = payload.setdefault("requests", [])
    if isinstance(requests, list):
        return requests
    normalized_requests: list[Any] = []
    payload["requests"] = normalized_requests
    return normalized_requests


def _write_request_payload(request_path: str, payload: dict[str, Any]) -> bool:
    try:
        write_text_atomically(request_path, compact_json(payload))
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _load_request_payload(request_path: str) -> dict[str, Any]:
    try:
        with open(request_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}
