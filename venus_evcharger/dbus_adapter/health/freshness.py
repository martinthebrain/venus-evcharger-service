#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache freshness metrics for the DBus adapter."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.dbus_gateway import FAST_READ_KEYS, DbusCacheStore, dbus_path_key
from venus_evcharger.dbus_gateway_command_types import CommandPayload
from venus_evcharger.dbus_gateway_core import float_or_zero

CacheValues = Mapping[str, Mapping[str, object]]


def cache_freshness(cache: DbusCacheStore, now: float) -> CommandPayload:
    values = {
        key: cache.value_snapshot(value, now)
        for key, value in cache.values.items()
    }
    critical_values: CacheValues = {
        key: value for key, value in values.items() if key in FAST_READ_KEYS
    }
    external_values = values_for_kinds(values, {"external_read"})
    local_values = values_for_kinds(values, {"local_owned", "static"})
    diagnostic_values = values_for_kinds(values, {"diagnostic"})
    return {
        "value_count": len(values),
        "status_counts": status_counts(critical_values),
        "all_status_counts": status_counts(values),
        "external_read_status_counts": status_counts(external_values),
        "local_publish_status_counts": status_counts(local_values),
        "static_status_counts": status_counts(values_for_kinds(values, {"static"})),
        "diagnostic_status_counts": status_counts(diagnostic_values),
        "critical_stale_count": count_status(critical_values, "stale"),
        "optional_source_error_count": optional_source_error_count(external_values),
        "optional_source_unavailable_count": optional_source_unavailable_count(external_values),
        **important_freshness(values),
    }


def values_for_kinds(values: CacheValues, kinds: set[str]) -> dict[str, Mapping[str, object]]:
    return {
        key: value
        for key, value in values.items()
        if str(value.get("freshness_kind", "external_read")) in kinds
    }


def count_status(values: CacheValues, expected: str) -> int:
    return sum(1 for value in values.values() if str(value.get("status", "unknown")) == expected)


def optional_source_error_count(values: CacheValues) -> int:
    optional_values = {key: value for key, value in values.items() if key not in FAST_READ_KEYS}
    return count_status(optional_values, "error")


def optional_source_unavailable_count(values: CacheValues) -> int:
    optional_values = {key: value for key, value in values.items() if key not in FAST_READ_KEYS}
    return count_status(optional_values, "unavailable")


def status_counts(values: CacheValues) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values.values():
        status = str(value.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def important_freshness(values: CacheValues) -> CommandPayload:
    important: CommandPayload = {
        f"{key}_age_s": float_or_zero(values.get(key, {}).get("age_s")) for key in FAST_READ_KEYS
    }
    important.update({f"{key}_status": str(values.get(key, {}).get("status", "missing")) for key in FAST_READ_KEYS})
    return important


def max_cached_path_age(
    values: CacheValues,
    service_name: str,
    paths: set[str],
    now: float,
) -> float:
    ages = [cached_entry_age(values.get(dbus_path_key(service_name, path)), now) for path in paths]
    return max(ages, default=0.0)


def missing_cached_path_count(
    values: CacheValues,
    service_name: str,
    paths: set[str],
) -> float:
    return float(sum(1 for path in paths if dbus_path_key(service_name, path) not in values))


def cached_entry_age(entry: object, now: float) -> float:
    if not isinstance(entry, Mapping):
        return 0.0
    updated_at = float_or_zero(entry.get("updated_at"))
    return max(0.0, now - updated_at) if updated_at > 0.0 else 0.0


def cached_entry_float(entry: object) -> float:
    if not isinstance(entry, Mapping):
        return 0.0
    return float_or_zero(entry.get("value"))
