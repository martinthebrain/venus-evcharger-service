# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway cache store."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.dbus_gateway_command_types import CommandPayload
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    GatewayPaths,
    _json_ready,
    _now,
    float_or_zero,
    gateway_paths,
    read_json_file,
    write_json_file,
)

NumericMetadataValue = str | bytes | bytearray | int | float
NUMERIC_METADATA_TYPES = (str, bytes, bytearray, int, float)


def _value_age(updated_at: float, now: float) -> float:
    return max(0.0, now - updated_at) if updated_at else 0.0  # pragma: no mutate


def _value_is_stale(status: str, age: float, stale_after_seconds: float) -> bool:
    return status == "fresh" and stale_after_seconds > 0.0 and age > stale_after_seconds  # pragma: no mutate


def _valid_snapshot_payload(payload: object) -> bool:
    return _snapshot_payload(payload) is not None  # pragma: no mutate


def _snapshot_payload(payload: object) -> Mapping[object, object] | None:
    if not isinstance(payload, Mapping):
        return None
    return payload if _snapshot_captured_at(payload) > 0.0 else None  # pragma: no mutate


def _snapshot_captured_at(payload: Mapping[object, object]) -> float:
    return float_or_zero(payload.get("captured_at"))


def _snapshot_too_old(captured_at: float, current: float, max_age_seconds: float) -> bool:
    return max_age_seconds >= 0.0 and current - captured_at > float(max_age_seconds)  # pragma: no mutate


@dataclass(frozen=True)
class CacheValueMetadata:
    source: str
    status: str = "fresh"
    confidence: float = 1.0
    last_error: str = ""
    now: float | None = None


def _cache_value_metadata(metadata: CacheValueMetadata | None, fields: Mapping[str, object]) -> CacheValueMetadata:  # pragma: no mutate block
    if metadata is not None:
        if fields:
            return CacheValueMetadata(  # pragma: no mutate
                source=str(fields.get("source", metadata.source)),  # pragma: no mutate
                status=str(fields.get("status", metadata.status)),  # pragma: no mutate
                confidence=_metadata_float(fields.get("confidence"), metadata.confidence),  # pragma: no mutate
                last_error=str(fields.get("last_error", metadata.last_error)),  # pragma: no mutate
                now=_metadata_now(fields.get("now"), metadata.now),  # pragma: no mutate
            )
        return metadata  # pragma: no mutate
    return CacheValueMetadata(  # pragma: no mutate
        source=str(fields.get("source", "")),  # pragma: no mutate
        status=str(fields.get("status", "fresh")),  # pragma: no mutate
        confidence=_metadata_float(fields.get("confidence"), 1.0),  # pragma: no mutate
        last_error=str(fields.get("last_error", "")),  # pragma: no mutate
        now=_metadata_now(fields.get("now")),  # pragma: no mutate
    )


def _metadata_now(value: object, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    if isinstance(value, NUMERIC_METADATA_TYPES):
        return float(value)  # pragma: no mutate
    return fallback


def _metadata_float(value: object, fallback: float) -> float:
    if value is None:
        return fallback
    if isinstance(value, NUMERIC_METADATA_TYPES):
        return float(value)  # pragma: no mutate
    return fallback


class DbusCacheStore:
    """RAM-owned DBus value cache with freshness and health metadata."""

    def __init__(self, paths: GatewayPaths | None = None, *, stale_after_seconds: float = 10.0) -> None:  # pragma: no mutate block
        self.paths = paths or gateway_paths()  # pragma: no mutate
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))  # pragma: no mutate
        self.sequence = 0
        self.values: dict[str, CommandPayload] = {}
        self.services: dict[str, CommandPayload] = {}
        self.health: CommandPayload = {
            "state": "init",  # pragma: no mutate
            "degraded_until": 0.0,  # pragma: no mutate
            "timeouts_60s": 0,  # pragma: no mutate
            "avg_latency_ms": 0.0,  # pragma: no mutate
            "max_latency_ms": 0.0,  # pragma: no mutate
        }

    def update_value(
        self,
        key: str,
        value: object,
        *,
        metadata: CacheValueMetadata | None = None,
        **metadata_fields: object,
    ) -> None:  # pragma: no mutate block
        details = _cache_value_metadata(metadata, metadata_fields)  # pragma: no mutate
        current = _now() if details.now is None else float(details.now)  # pragma: no mutate
        self.values[str(key)] = {
            "value": _json_ready(value),  # pragma: no mutate
            "source": str(details.source),  # pragma: no mutate
            "updated_at": current,  # pragma: no mutate
            "age_s": 0.0,  # pragma: no mutate
            "status": str(details.status),  # pragma: no mutate
            "last_error": str(details.last_error),  # pragma: no mutate
            "confidence": float(details.confidence),  # pragma: no mutate
        }
        self.sequence += 1

    def mark_error(self, key: str, *, source: str, error: BaseException | str, now: float | None = None) -> None:  # pragma: no mutate block
        current = _now() if now is None else float(now)  # pragma: no mutate
        current_value = self.values.get(str(key), {})  # pragma: no mutate
        self.values[str(key)] = {
            "value": current_value.get("value"),  # pragma: no mutate
            "source": str(source),  # pragma: no mutate
            "updated_at": float_or_zero(current_value.get("updated_at")),  # pragma: no mutate
            "error_at": current,  # pragma: no mutate
            "age_s": max(0.0, current - float_or_zero(current_value.get("updated_at"))),  # pragma: no mutate
            "status": "error",  # pragma: no mutate
            "last_error": str(error),  # pragma: no mutate
            "confidence": 0.0,  # pragma: no mutate
        }
        self.sequence += 1

    def update_services(self, names: list[str], *, now: float | None = None) -> None:  # pragma: no mutate block
        current = _now() if now is None else float(now)  # pragma: no mutate
        self.services = {str(name): {"seen_at": current, "status": "present"} for name in names}  # pragma: no mutate
        self.sequence += 1

    def snapshot(self, *, now: float | None = None) -> CommandPayload:  # pragma: no mutate block
        current = _now() if now is None else float(now)  # pragma: no mutate
        values = {key: self.value_snapshot(item, current) for key, item in self.values.items()}  # pragma: no mutate
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,  # pragma: no mutate
            "sequence": self.sequence,  # pragma: no mutate
            "captured_at": current,  # pragma: no mutate
            "dbus_health": dict(self.health),  # pragma: no mutate
            "values": values,  # pragma: no mutate
            "services": dict(self.services),  # pragma: no mutate
        }

    def value_snapshot(self, item: Mapping[str, object], now: float) -> CommandPayload:
        updated_at = float_or_zero(item.get("updated_at"))  # pragma: no mutate
        age = _value_age(updated_at, now)  # pragma: no mutate
        status = self._value_status(item, age)  # pragma: no mutate
        return {
            **dict(item),
            "age_s": age,  # pragma: no mutate
            "status": status,  # pragma: no mutate
        }

    def _value_status(self, item: Mapping[str, object], age: float) -> str:
        status = str(item.get("status", "unknown"))  # pragma: no mutate
        if _value_is_stale(status, age, self.stale_after_seconds):
            return "stale"  # pragma: no mutate
        return status  # pragma: no mutate

    def write_snapshot_files(self) -> None:  # pragma: no mutate block
        os.makedirs(self.paths.run_dir, exist_ok=True)
        snapshot = self.snapshot()  # pragma: no mutate
        write_json_file(self.paths.cache_path, snapshot)
        write_text_atomically(self.paths.cache_sequence_path, f"{self.sequence}\n")
        write_json_file(
            self.paths.health_path,
            {
                "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,  # pragma: no mutate
                "sequence": self.sequence,  # pragma: no mutate
                "captured_at": snapshot["captured_at"],  # pragma: no mutate
                "dbus_health": snapshot["dbus_health"],  # pragma: no mutate
            },
        )

    @staticmethod
    def load_snapshot(path: str, *, max_age_seconds: float = 30.0, now: float | None = None) -> CommandPayload:  # pragma: no mutate block
        payload = _snapshot_payload(read_json_file(path, {}))  # pragma: no mutate
        if payload is None:
            return {}
        captured_at = _snapshot_captured_at(payload)  # pragma: no mutate
        current = _now() if now is None else float(now)  # pragma: no mutate
        if _snapshot_too_old(captured_at, current, max_age_seconds):
            return {}
        return {str(key): value for key, value in payload.items()}  # pragma: no mutate

    @staticmethod
    def value_entry(snapshot: Mapping[str, object], key: str) -> CommandPayload | None:
        values = snapshot.get("values")  # pragma: no mutate
        if not isinstance(values, Mapping):
            return None
        item = values.get(key)  # pragma: no mutate
        return dict(item) if isinstance(item, Mapping) else None  # pragma: no mutate
