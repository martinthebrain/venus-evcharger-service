# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway cache store."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Unpack

from venus_evcharger import dbus_gateway_cache_io as cache_io
from venus_evcharger import dbus_gateway_cache_metadata as cache_metadata
from venus_evcharger import dbus_gateway_cache_snapshot as cache_snapshot
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    CacheFreshnessKind,
    CacheSourceState,
    GatewayPaths,
    _json_ready,
    _now,
    float_or_zero,
    gateway_paths,
)
from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, EnergyTopologySnapshot

__all__ = ["DbusCacheStore"]


@dataclass(frozen=True)
class _CacheReadFailure:
    source: str
    error: BaseException | str
    status: str
    source_state: CacheSourceState
    now: float | None = None
    freshness_kind: CacheFreshnessKind | None = None
    retry_after_seconds: float | None = None


def _confirmed_at(
    previous: Mapping[str, object],
    current: float,
    *,
    confirmed: bool,
) -> float:
    """Preserve the timestamp when a cache value is only estimated."""
    return current if confirmed else float_or_zero(previous.get("confirmed_at"))


def _confirmed_monotonic(
    previous: Mapping[str, object],
    current: float,
    *,
    confirmed: bool,
) -> float:
    """Preserve the monotonic observation anchor for estimated values."""
    return (
        current
        if confirmed
        else float_or_zero(previous.get("confirmed_monotonic"))
    )


def _reason_metadata(reason_code: str) -> CommandPayload:
    """Return optional reason metadata without changing normal cache entries."""
    return {"reason_code": reason_code} if reason_code else {}


class DbusCacheStore:
    """RAM-owned DBus value cache with freshness and health metadata."""

    def __init__(self, paths: GatewayPaths | None = None, *, stale_after_seconds: float = 10.0) -> None:
        self.paths = paths or gateway_paths()
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))
        self.sequence = 0
        self.local_service_registered = False
        self.local_service_name = ""
        self.values: dict[str, CommandPayload] = {}
        self.services: dict[str, CommandPayload] = {}
        self.energy_inputs: CommandPayload = {}
        self.energy_topology: CommandPayload = {}
        self._energy_inputs_snapshot: EnergyInputsSnapshot | None = None
        self._energy_topology_snapshot: EnergyTopologySnapshot | None = None
        self.health: CommandPayload = {
            "state": "init",
            "degraded_until": 0.0,
            "timeouts_60s": 0,
            "avg_latency_ms": 0.0,
            "max_latency_ms": 0.0,
        }

    def update_value(
        self,
        key: str,
        value: object,
        *,
        metadata: cache_metadata.CacheValueMetadata | None = None,
        **metadata_fields: object,
    ) -> None:
        details = cache_metadata.merge_cache_value_metadata(metadata, metadata_fields)
        current = _now() if details.now is None else float(details.now)
        current_monotonic = (
            time.monotonic()
            if details.now_monotonic is None
            else float(details.now_monotonic)
        )
        previous = self.values.get(str(key), {})
        normalized_value = _json_ready(value)
        changed_at = float_or_zero(previous.get("changed_at"))
        if previous.get("value") != normalized_value or changed_at <= 0.0:
            changed_at = current
        confirmed_at = _confirmed_at(previous, current, confirmed=details.confirmed)
        confirmed_monotonic = _confirmed_monotonic(
            previous,
            current_monotonic,
            confirmed=details.confirmed,
        )
        cache_value: CommandPayload = {
            "value": normalized_value,
            "source": str(details.source),
            "changed_at": changed_at,
            "confirmed_at": confirmed_at,
            "confirmed_monotonic": confirmed_monotonic,
            "updated_at": current,
            "updated_monotonic": current_monotonic,
            "age_s": 0.0,
            "status": str(details.status),
            "last_error": str(details.last_error),
            "confidence": float(details.confidence),
            "freshness_kind": details.freshness_kind,
            "source_state": details.source_state,
            "stale_after_s": details.stale_after_seconds,
            **_reason_metadata(details.reason_code),
        }
        self.values[str(key)] = cache_value
        self.sequence += 1

    def update_external_read(
        self,
        key: str,
        value: object,
        **metadata_fields: Unpack[cache_metadata.ExternalReadMetadata],
    ) -> None:
        """Record a value obtained from the external DBus boundary."""
        self.update_value(
            key,
            value,
            freshness_kind="external_read",
            **metadata_fields,
        )

    def mark_error(
        self,
        key: str,
        *,
        source: str,
        error: BaseException | str,
        now: float | None = None,
        freshness_kind: CacheFreshnessKind | None = None,
    ) -> None:
        self._mark_unreadable(
            key,
            _CacheReadFailure(
                source=source,
                error=error,
                now=now,
                freshness_kind=freshness_kind,
                status="error",
                source_state="error",
            ),
        )

    def mark_unavailable(
        self,
        key: str,
        *,
        source: str,
        error: BaseException | str,
        retry_after_seconds: float,
        now: float | None = None,
    ) -> None:
        """Record a non-fatal source outage while retaining forensic details."""
        self._mark_unreadable(
            key,
            _CacheReadFailure(
                source=source,
                error=error,
                now=now,
                status="unavailable",
                source_state="unavailable",
                retry_after_seconds=retry_after_seconds,
            ),
        )

    def _mark_unreadable(self, key: str, failure: _CacheReadFailure) -> None:
        current = _now() if failure.now is None else float(failure.now)
        current_value = self.values.get(str(key), {})
        resolved_kind = (
            cache_metadata.normalize_freshness_kind(current_value.get("freshness_kind"), "external_read")
            if failure.freshness_kind is None
            else failure.freshness_kind
        )
        failure_value: CommandPayload = {
            "value": current_value.get("value"),
            "source": str(failure.source),
            "changed_at": float_or_zero(current_value.get("changed_at")),
            "confirmed_at": float_or_zero(current_value.get("confirmed_at")),
            "confirmed_monotonic": float_or_zero(
                current_value.get("confirmed_monotonic")
            ),
            "updated_at": float_or_zero(current_value.get("updated_at")),
            "updated_monotonic": float_or_zero(
                current_value.get("updated_monotonic")
            ),
            "error_at": current,
            "age_s": max(0.0, current - float_or_zero(current_value.get("updated_at"))),
            "status": failure.status,
            "last_error": str(failure.error),
            "confidence": 0.0,
            "freshness_kind": resolved_kind,
            "source_state": failure.source_state,
            "stale_after_s": current_value.get("stale_after_s"),
        }
        if failure.retry_after_seconds is not None:
            failure_value["next_probe_at"] = current + max(0.0, float(failure.retry_after_seconds))
        self.values[str(key)] = failure_value
        self.sequence += 1

    def set_local_service_registered(self, registered: bool, *, service_name: str) -> None:
        normalized = bool(registered)
        normalized_name = str(service_name)
        if normalized == self.local_service_registered and normalized_name == self.local_service_name:
            return
        self.local_service_registered = normalized
        self.local_service_name = normalized_name
        self.sequence += 1

    def update_services(self, names: list[str], *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self.services = {str(name): {"seen_at": current, "status": "present"} for name in names}
        self.sequence += 1

    def set_semantic_energy_snapshots(
        self,
        inputs: EnergyInputsSnapshot,
        topology: EnergyTopologySnapshot,
    ) -> None:
        """Attach adapter-derived public snapshots without changing raw cache state."""
        self._energy_inputs_snapshot = inputs
        self.energy_inputs = inputs.to_payload()
        self.set_energy_topology_snapshot(topology)

    def set_energy_topology_snapshot(self, topology: EnergyTopologySnapshot) -> None:
        self._energy_topology_snapshot = topology
        self.energy_topology = topology.to_payload()

    def snapshot(self, *, now: float | None = None) -> CommandPayload:
        current = _now() if now is None else float(now)
        values = {key: self.value_snapshot(item, current) for key, item in self.values.items()}
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "captured_at": current,
            "dbus_health": dict(self.health),
            "energy_inputs": dict(self.energy_inputs),
            "energy_topology": dict(self.energy_topology),
            "values": values,
            "services": dict(self.services),
        }

    def value_snapshot(self, item: Mapping[str, object], now: float) -> CommandPayload:
        return cache_snapshot.project_cache_value(
            item,
            now,
            cache_snapshot.CacheLivenessPolicy(
                stale_after_seconds=self.stale_after_seconds,
                local_service_registered=self.local_service_registered,
                local_service_name=self.local_service_name,
            ),
        )

    def write_cache_snapshot(self, *, now: float | None = None) -> None:
        snapshot = self.snapshot(now=now)
        cache_io.write_cache_snapshot(self.paths, snapshot, self.sequence)

    def write_health_snapshot(self, *, now: float | None = None) -> None:
        captured_at = _now() if now is None else float(now)
        cache_io.write_health_snapshot(
            self.paths,
            sequence=self.sequence,
            captured_at=captured_at,
            health=self.health,
        )

    def write_energy_inputs_snapshot(self) -> None:
        cache_io.write_energy_inputs_snapshot(self.paths, self._energy_inputs_snapshot)

    def write_energy_topology_snapshot(self) -> None:
        cache_io.write_energy_topology_snapshot(self.paths, self._energy_topology_snapshot)

    @staticmethod
    def load_snapshot(path: str, *, max_age_seconds: float = 30.0, now: float | None = None) -> CommandPayload:
        return cache_snapshot.load_cache_snapshot(path, max_age_seconds=max_age_seconds, now=now)

    @staticmethod
    def value_entry(snapshot: Mapping[str, object], key: str) -> CommandPayload | None:
        return cache_snapshot.cache_value_entry(snapshot, key)
