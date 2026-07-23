# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway cache store."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict, Unpack

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    CacheFreshnessKind,
    CacheSourceState,
    GatewayPaths,
    _json_ready,
    _now,
    float_or_zero,
    gateway_paths,
    is_object_mapping,
    read_json_file,
    write_json_file,
)
from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, EnergyTopologySnapshot
from venus_evcharger.ipc.energy_binary import write_energy_inputs_file

NumericMetadataValue = str | bytes | bytearray | int | float
NUMERIC_METADATA_TYPES = (str, bytes, bytearray, int, float)
CACHE_FRESHNESS_KINDS: dict[str, CacheFreshnessKind] = {
    "external_read": "external_read",
    "local_owned": "local_owned",
    "static": "static",
    "diagnostic": "diagnostic",
}


def _value_age(updated_at: float, now: float) -> float:
    return max(0.0, now - updated_at) if updated_at else 0.0


def _value_is_stale(status: str, age: float, stale_after_seconds: float) -> bool:
    return status == "fresh" and stale_after_seconds > 0.0 and age > stale_after_seconds


def _valid_snapshot_payload(payload: object) -> bool:
    return _snapshot_payload(payload) is not None


def _snapshot_payload(payload: object) -> Mapping[object, object] | None:
    if not is_object_mapping(payload):
        return None
    return payload if _snapshot_captured_at(payload) > 0.0 else None


def _snapshot_captured_at(payload: Mapping[object, object]) -> float:
    return float_or_zero(payload.get("captured_at"))


def _snapshot_too_old(captured_at: float, current: float, max_age_seconds: float) -> bool:
    return max_age_seconds >= 0.0 and current - captured_at > float(max_age_seconds)


@dataclass(frozen=True)
class CacheValueMetadata:
    source: str
    status: str = "fresh"
    confidence: float = 1.0
    last_error: str = ""
    now: float | None = None
    freshness_kind: CacheFreshnessKind = "external_read"
    source_state: CacheSourceState = "active"
    stale_after_seconds: float | None = None


@dataclass(frozen=True)
class _CacheReadFailure:
    source: str
    error: BaseException | str
    status: str
    source_state: CacheSourceState
    now: float | None = None
    freshness_kind: CacheFreshnessKind | None = None
    retry_after_seconds: float | None = None


class ExternalReadMetadata(TypedDict, total=False):
    """Optional metadata accepted by the external-read cache boundary."""

    source: str
    status: str
    confidence: float
    last_error: str
    now: float | None
    stale_after_seconds: float | None
    source_state: CacheSourceState


def _cache_value_metadata(metadata: CacheValueMetadata | None, fields: Mapping[str, object]) -> CacheValueMetadata:
    if metadata is not None:
        if fields:
            return CacheValueMetadata(
                source=str(fields.get("source", metadata.source)),
                status=str(fields.get("status", metadata.status)),
                confidence=_metadata_float(fields.get("confidence"), metadata.confidence),
                last_error=str(fields.get("last_error", metadata.last_error)),
                now=_metadata_now(fields.get("now"), metadata.now),
                freshness_kind=_metadata_freshness_kind(fields.get("freshness_kind"), metadata.freshness_kind),
                source_state=_metadata_source_state(fields.get("source_state"), metadata.source_state),
                stale_after_seconds=_metadata_optional_float(
                    fields.get("stale_after_seconds"), metadata.stale_after_seconds
                ),
            )
        return metadata
    return CacheValueMetadata(
        source=str(fields.get("source", "")),
        status=str(fields.get("status", "fresh")),
        confidence=_metadata_float(fields.get("confidence"), 1.0),
        last_error=str(fields.get("last_error", "")),
        now=_metadata_now(fields.get("now")),
        freshness_kind=_metadata_freshness_kind(fields.get("freshness_kind"), "external_read"),
        source_state=_metadata_source_state(fields.get("source_state"), "active"),
        stale_after_seconds=_metadata_optional_float(fields.get("stale_after_seconds")),
    )


def _metadata_freshness_kind(value: object, fallback: CacheFreshnessKind) -> CacheFreshnessKind:
    normalized = str(value) if value is not None else fallback
    return CACHE_FRESHNESS_KINDS.get(normalized, fallback)


def _metadata_source_state(value: object, fallback: CacheSourceState) -> CacheSourceState:
    normalized = str(value) if value is not None else fallback
    if normalized == "active":
        return "active"
    if normalized == "unavailable":
        return "unavailable"
    if normalized == "error":
        return "error"
    return fallback


def _is_local_cache_value(
    item: Mapping[str, object],
    freshness_kind: CacheFreshnessKind,
    service_name: str,
) -> bool:
    if not service_name:
        return False
    if freshness_kind in {"local_owned", "static"}:
        return True
    source = item.get("source")
    return (
        freshness_kind == "diagnostic"
        and isinstance(source, str)
        and source.startswith(f"{service_name}/")
    )


def _local_value_status(status: str, service_registered: bool) -> str:
    if status != "fresh":
        return status
    return "fresh" if service_registered else "unavailable"


def _metadata_optional_float(value: object, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    numeric_fallback = 0.0 if fallback is None else fallback
    return max(0.0, _metadata_float(value, numeric_fallback))


def _metadata_now(value: object, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    if isinstance(value, NUMERIC_METADATA_TYPES):
        return float(value)
    return fallback


def _metadata_float(value: object, fallback: float) -> float:
    if value is None:
        return fallback
    if isinstance(value, NUMERIC_METADATA_TYPES):
        return float(value)
    return fallback


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
        metadata: CacheValueMetadata | None = None,
        **metadata_fields: object,
    ) -> None:
        details = _cache_value_metadata(metadata, metadata_fields)
        current = _now() if details.now is None else float(details.now)
        previous = self.values.get(str(key), {})
        normalized_value = _json_ready(value)
        changed_at = float_or_zero(previous.get("changed_at"))
        if previous.get("value") != normalized_value or changed_at <= 0.0:
            changed_at = current
        self.values[str(key)] = {
            "value": normalized_value,
            "source": str(details.source),
            "changed_at": changed_at,
            "confirmed_at": current,
            "updated_at": current,
            "age_s": 0.0,
            "status": str(details.status),
            "last_error": str(details.last_error),
            "confidence": float(details.confidence),
            "freshness_kind": details.freshness_kind,
            "source_state": details.source_state,
            "stale_after_s": details.stale_after_seconds,
        }
        self.sequence += 1

    def update_external_read(
        self,
        key: str,
        value: object,
        **metadata_fields: Unpack[ExternalReadMetadata],
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
            _metadata_freshness_kind(current_value.get("freshness_kind"), "external_read")
            if failure.freshness_kind is None
            else failure.freshness_kind
        )
        failure_value: CommandPayload = {
            "value": current_value.get("value"),
            "source": str(failure.source),
            "changed_at": float_or_zero(current_value.get("changed_at")),
            "confirmed_at": float_or_zero(current_value.get("confirmed_at")),
            "updated_at": float_or_zero(current_value.get("updated_at")),
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
        self._energy_topology_snapshot = topology
        self.energy_inputs = inputs.to_payload()
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
        confirmed_at = float_or_zero(item.get("confirmed_at"))
        changed_at = float_or_zero(item.get("changed_at"))
        age = _value_age(confirmed_at, now)
        status = self._value_status(item, age)
        return {
            **dict(item),
            "age_s": age,
            "change_age_s": _value_age(changed_at, now),
            "status": status,
        }

    def _value_status(self, item: Mapping[str, object], age: float) -> str:
        status = str(item.get("status", "unknown"))
        freshness_kind = _metadata_freshness_kind(item.get("freshness_kind"), "external_read")
        if _is_local_cache_value(item, freshness_kind, self.local_service_name):
            return _local_value_status(status, self.local_service_registered)
        stale_after = _metadata_float(item.get("stale_after_s"), self.stale_after_seconds)
        if freshness_kind == "external_read" and _value_is_stale(status, age, stale_after):
            return "stale"
        return status

    def write_snapshot_files(self) -> None:
        os.makedirs(self.paths.run_dir, exist_ok=True)
        snapshot = self.snapshot()
        self._write_semantic_snapshot_files()
        write_json_file(self.paths.cache_path, snapshot)
        write_text_atomically(self.paths.cache_sequence_path, f"{self.sequence}\n")
        write_json_file(
            self.paths.health_path,
            {
                "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
                "sequence": self.sequence,
                "captured_at": snapshot["captured_at"],
                "dbus_health": snapshot["dbus_health"],
            },
        )

    def _write_semantic_snapshot_files(self) -> None:
        energy_inputs = self._energy_inputs_snapshot
        energy_topology = self._energy_topology_snapshot
        if energy_inputs is not None:
            write_energy_inputs_file(self.paths.energy_inputs_path, energy_inputs)
        if energy_topology is not None:
            write_json_file(self.paths.energy_topology_path, energy_topology.to_payload())

    @staticmethod
    def load_snapshot(path: str, *, max_age_seconds: float = 30.0, now: float | None = None) -> CommandPayload:
        payload = _snapshot_payload(read_json_file(path))
        if payload is None:
            return {}
        captured_at = _snapshot_captured_at(payload)
        current = _now() if now is None else float(now)
        if _snapshot_too_old(captured_at, current, max_age_seconds):
            return {}
        return {str(key): value for key, value in payload.items()}

    @staticmethod
    def value_entry(snapshot: Mapping[str, object], key: str) -> CommandPayload | None:
        values = snapshot.get("values")
        if not is_object_mapping(values):
            return None
        item = values.get(key)
        return {str(item_key): item_value for item_key, item_value in item.items()} if is_object_mapping(item) else None
