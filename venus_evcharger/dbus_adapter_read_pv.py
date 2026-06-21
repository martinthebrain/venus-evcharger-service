#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PV source selection for the dedicated DBus adapter reader."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from venus_evcharger.dbus_gateway import dbus_path_key

PV_MEMBER_ERROR_BACKOFF_SECONDS = 300.0


def pv_total_members(
    spec: Mapping[str, Any],
    ac_services: Sequence[str],
    cached_values: Mapping[str, Mapping[str, Any]],
    *,
    now: float | None = None,
) -> list[tuple[str, str]]:
    current_time = time.time() if now is None else now
    return [
        *ac_pv_members(spec, ac_services, cached_values, now=current_time),
        *dc_pv_members(spec, cached_values, now=current_time),
    ]


def ac_pv_members(
    spec: Mapping[str, Any],
    services: Sequence[str],
    cached_values: Mapping[str, Mapping[str, Any]],
    *,
    now: float,
) -> list[tuple[str, str]]:
    path = str(spec.get("path") or "")
    return [
        (service, path)
        for service in services
        if path and not pv_member_recently_failed(cached_values, service, path, now=now)
    ]


def dc_pv_members(
    spec: Mapping[str, Any],
    cached_values: Mapping[str, Mapping[str, Any]],
    *,
    now: float,
) -> list[tuple[str, str]]:
    target = dc_pv_target(spec) if use_dc_pv(spec) else None
    if target is None or pv_member_recently_failed(cached_values, *target, now=now):
        return []
    return [target]


def dc_pv_target(spec: Mapping[str, Any]) -> tuple[str, str] | None:
    service = str(spec.get("dc_service") or "").strip()
    path = str(spec.get("dc_path") or "").strip()
    if not service or not path.startswith("/"):
        return None
    return service, path


def use_dc_pv(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("use_dc_pv", "")).strip().lower() in {"1", "true", "yes", "on"}


def pv_member_recently_failed(
    cached_values: Mapping[str, Mapping[str, Any]],
    service: str,
    path: str,
    *,
    now: float | None = None,
    backoff_seconds: float = PV_MEMBER_ERROR_BACKOFF_SECONDS,
) -> bool:
    entry = cached_values.get(dbus_path_key(service, path), {})
    if entry.get("status") != "error":
        return False
    error_at = float(entry.get("error_at", 0.0) or 0.0)
    current_time = time.time() if now is None else now
    return error_at > 0.0 and current_time - error_at < backoff_seconds
