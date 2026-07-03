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
    return max(0.0, now - updated_at) if updated_at else 0.0


def _value_is_stale(status: str, age: float, stale_after_seconds: float) -> bool:
    return status == "fresh" and stale_after_seconds > 0.0 and age > stale_after_seconds


def _valid_snapshot_payload(payload: object) -> bool:
    return _snapshot_payload(payload) is not None


def _snapshot_payload(payload: object) -> Mapping[object, object] | None:
    if not isinstance(payload, Mapping):
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


def _cache_value_metadata(metadata: CacheValueMetadata | None, fields: Mapping[str, object]) -> CacheValueMetadata:
    if metadata is not None:
        if fields:
            return CacheValueMetadata(
                source=str(fields.get("source", metadata.source)),
                status=str(fields.get("status", metadata.status)),
                confidence=_metadata_float(fields.get("confidence"), metadata.confidence),
                last_error=str(fields.get("last_error", metadata.last_error)),
                now=_metadata_now(fields.get("now"), metadata.now),
            )
        return metadata
    return CacheValueMetadata(
        source=str(fields.get("source", "")),
        status=str(fields.get("status", "fresh")),
        confidence=_metadata_float(fields.get("confidence"), 1.0),
        last_error=str(fields.get("last_error", "")),
        now=_metadata_now(fields.get("now")),
    )


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
        self.values: dict[str, CommandPayload] = {}
        self.services: dict[str, CommandPayload] = {}
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
        self.values[str(key)] = {
            "value": _json_ready(value),
            "source": str(details.source),
            "updated_at": current,
            "age_s": 0.0,
            "status": str(details.status),
            "last_error": str(details.last_error),
            "confidence": float(details.confidence),
        }
        self.sequence += 1

    def mark_error(self, key: str, *, source: str, error: BaseException | str, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        current_value = self.values.get(str(key), {})
        self.values[str(key)] = {
            "value": current_value.get("value"),
            "source": str(source),
            "updated_at": float_or_zero(current_value.get("updated_at")),
            "error_at": current,
            "age_s": max(0.0, current - float_or_zero(current_value.get("updated_at"))),
            "status": "error",
            "last_error": str(error),
            "confidence": 0.0,
        }
        self.sequence += 1

    def update_services(self, names: list[str], *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self.services = {str(name): {"seen_at": current, "status": "present"} for name in names}
        self.sequence += 1

    def snapshot(self, *, now: float | None = None) -> CommandPayload:
        current = _now() if now is None else float(now)
        values = {key: self.value_snapshot(item, current) for key, item in self.values.items()}
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "captured_at": current,
            "dbus_health": dict(self.health),
            "values": values,
            "services": dict(self.services),
        }

    def value_snapshot(self, item: Mapping[str, object], now: float) -> CommandPayload:
        updated_at = float_or_zero(item.get("updated_at"))
        age = _value_age(updated_at, now)
        status = self._value_status(item, age)
        return {
            **dict(item),
            "age_s": age,
            "status": status,
        }

    def _value_status(self, item: Mapping[str, object], age: float) -> str:
        status = str(item.get("status", "unknown"))
        if _value_is_stale(status, age, self.stale_after_seconds):
            return "stale"
        return status

    def write_snapshot_files(self) -> None:
        os.makedirs(self.paths.run_dir, exist_ok=True)
        snapshot = self.snapshot()
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
        if not isinstance(values, Mapping):
            return None
        item = values.get(key)
        return dict(item) if isinstance(item, Mapping) else None
