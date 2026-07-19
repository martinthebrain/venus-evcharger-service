#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic AC and DC PV source selection for adapter reads."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from venus_evcharger.dbus_adapter.read.spec import ReadSpec
from venus_evcharger.dbus_gateway import dbus_path_key

PV_MEMBER_ERROR_BACKOFF_SECONDS = 300.0


def pv_total_members(
    spec: ReadSpec,
    ac_services: Sequence[str],
    cached_values: Mapping[str, Mapping[str, object]],
    *,
    now: float | None = None,
) -> list[tuple[str, str]]:
    current_time = time.time() if now is None else now
    return [
        *ac_pv_members(spec, ac_services, cached_values, now=current_time),
        *dc_pv_members(spec, cached_values, now=current_time),
    ]


def ac_pv_members(
    spec: ReadSpec,
    services: Sequence[str],
    cached_values: Mapping[str, Mapping[str, object]],
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
    spec: ReadSpec,
    cached_values: Mapping[str, Mapping[str, object]],
    *,
    now: float,
) -> list[tuple[str, str]]:
    target = dc_pv_target(spec) if use_dc_pv(spec) else None
    if target is None or pv_member_recently_failed(cached_values, *target, now=now):
        return []
    return [target]


def dc_pv_target(spec: ReadSpec) -> tuple[str, str] | None:
    if "dc_service" not in spec or "dc_path" not in spec:
        return None
    service = _stripped_text(spec["dc_service"])
    path = _stripped_text(spec["dc_path"])
    if not service or not path.startswith("/"):
        return None
    return service, path


def use_dc_pv(spec: ReadSpec) -> bool:
    if "use_dc_pv" not in spec:
        return False
    return str(spec["use_dc_pv"]).strip().lower() in {"1", "true", "yes", "on"}


def pv_member_recently_failed(
    cached_values: Mapping[str, Mapping[str, object]],
    service: str,
    path: str,
    *,
    now: float | None = None,
    backoff_seconds: float = PV_MEMBER_ERROR_BACKOFF_SECONDS,
) -> bool:
    entry = cached_values.get(dbus_path_key(service, path), {})
    if entry.get("status") != "error":
        return False
    if "error_at" not in entry:
        return False
    error_at = _float_or_zero(entry["error_at"])
    current_time = time.time() if now is None else now
    return error_at > 0.0 and current_time - error_at < backoff_seconds


def _float_or_zero(raw_value: object) -> float:
    if isinstance(raw_value, bool):
        return 0.0
    if isinstance(raw_value, (float, int, str)):
        try:
            return float(raw_value)
        except ValueError:
            return 0.0
    return 0.0


def _stripped_text(raw_value: object) -> str:
    return raw_value.strip() if isinstance(raw_value, str) else ""
