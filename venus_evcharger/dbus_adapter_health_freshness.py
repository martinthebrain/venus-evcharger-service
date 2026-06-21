#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache freshness helpers for the dedicated DBus adapter process."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.dbus_gateway import FAST_READ_KEYS, DbusCacheStore, dbus_path_key


def cache_freshness(cache: DbusCacheStore, now: float) -> dict[str, Any]:
    values = {
        key: cache._value_snapshot(value, now)  # pylint: disable=protected-access
        for key, value in cache.values.items()
    }
    return {"value_count": len(values), "status_counts": status_counts(values), **important_freshness(values)}


def status_counts(values: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values.values():
        status = str(value.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def important_freshness(values: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    important: dict[str, Any] = {
        f"{key}_age_s": float(values.get(key, {}).get("age_s", 0.0) or 0.0) for key in FAST_READ_KEYS
    }
    important.update({f"{key}_status": str(values.get(key, {}).get("status", "missing")) for key in FAST_READ_KEYS})
    return important


def max_cached_path_age(
    values: Mapping[str, Mapping[str, Any]],
    service_name: str,
    paths: set[str],
    now: float,
) -> float:
    ages = [cached_entry_age(values.get(dbus_path_key(service_name, path)), now) for path in paths]
    ages = [age for age in ages if age > 0.0]
    return max(ages) if ages else 0.0


def missing_cached_path_count(
    values: Mapping[str, Mapping[str, Any]],
    service_name: str,
    paths: set[str],
) -> float:
    return float(sum(1 for path in paths if dbus_path_key(service_name, path) not in values))


def cached_entry_age(entry: object, now: float) -> float:
    if not isinstance(entry, Mapping):
        return 0.0
    updated_at = float(entry.get("updated_at", 0.0) or 0.0)
    return max(0.0, now - updated_at) if updated_at > 0.0 else 0.0


def cached_entry_float(entry: object) -> float:
    if not isinstance(entry, Mapping):
        return 0.0
    try:
        return float(entry.get("value", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
