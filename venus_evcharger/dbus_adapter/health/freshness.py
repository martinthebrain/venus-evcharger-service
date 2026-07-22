#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache freshness metrics for the DBus adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from venus_evcharger.dbus_adapter.read.keys import CORE_ENERGY_READ_KEYS
from venus_evcharger.dbus_gateway import DbusCacheStore, dbus_path_key
from venus_evcharger.dbus_gateway_core import float_or_zero
from venus_evcharger.ipc.command_mailbox import normalized_mapping
from venus_evcharger.ipc.command_types import CommandPayload

CacheValues = Mapping[str, Mapping[str, object]]


class PublicationFieldObservation(Protocol):
    """Timestamped semantic publication value exposed by the registry."""

    @property
    def value(self) -> object: ...

    @property
    def observed_at(self) -> float: ...


class EvcsPublicationObservations(Protocol):
    """Minimal semantic observation surface required by health calculations."""

    def evcs_field_observation(self, field: str) -> PublicationFieldObservation | None: ...


def cache_freshness(cache: DbusCacheStore, now: float) -> CommandPayload:
    values = {
        key: cache.value_snapshot(value, now)
        for key, value in cache.values.items()
    }
    critical_values: CacheValues = {
        key: value for key, value in values.items() if key in CORE_ENERGY_READ_KEYS
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
    optional_values = {key: value for key, value in values.items() if key not in CORE_ENERGY_READ_KEYS}
    return count_status(optional_values, "error")


def optional_source_unavailable_count(values: CacheValues) -> int:
    optional_values = {key: value for key, value in values.items() if key not in CORE_ENERGY_READ_KEYS}
    return count_status(optional_values, "unavailable")


def status_counts(values: CacheValues) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values.values():
        status = str(value.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def important_freshness(values: CacheValues) -> CommandPayload:
    important: CommandPayload = {
        f"{key}_age_s": float_or_zero(values.get(key, {}).get("age_s")) for key in CORE_ENERGY_READ_KEYS
    }
    important.update(
        {f"{key}_status": str(values.get(key, {}).get("status", "missing")) for key in CORE_ENERGY_READ_KEYS}
    )
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
    values = normalized_mapping(entry)
    if values is None:
        return 0.0
    updated_at = float(float_or_zero(values.get("updated_at")))
    return max(0.0, now - updated_at) if updated_at > 0.0 else 0.0


def cached_entry_float(entry: object) -> float:
    values = normalized_mapping(entry)
    if values is None:
        return 0.0
    return float(float_or_zero(values.get("value")))


def max_publication_field_age(
    observations: EvcsPublicationObservations,
    fields: set[str] | frozenset[str],
    now: float,
) -> float:
    ages = [publication_field_age(observations.evcs_field_observation(field), now) for field in fields]
    return max(ages, default=0.0)


def missing_publication_field_count(
    observations: EvcsPublicationObservations,
    fields: set[str] | frozenset[str],
) -> float:
    return float(sum(1 for field in fields if observations.evcs_field_observation(field) is None))


def publication_field_age(observation: PublicationFieldObservation | None, now: float) -> float:
    if observation is None or observation.observed_at <= 0.0:
        return 0.0
    return max(0.0, now - observation.observed_at)


def publication_field_float(observation: PublicationFieldObservation | None) -> float:
    return 0.0 if observation is None else float_or_zero(observation.value)
