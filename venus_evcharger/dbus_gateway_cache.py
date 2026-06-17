# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway cache store."""

from __future__ import annotations

import os
from typing import Any, Mapping

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    GatewayPaths,
    _json_ready,
    _now,
    gateway_paths,
    read_json_file,
    write_json_file,
)

def _value_age(updated_at: float, now: float) -> float:
    return max(0.0, now - updated_at) if updated_at else 0.0


def _value_is_stale(status: str, age: float, stale_after_seconds: float) -> bool:
    return status == "fresh" and stale_after_seconds > 0.0 and age > stale_after_seconds


def _valid_snapshot_payload(payload: object) -> bool:
    return isinstance(payload, dict) and float(payload.get("captured_at", 0.0) or 0.0) > 0.0


def _snapshot_too_old(captured_at: float, current: float, max_age_seconds: float) -> bool:
    return max_age_seconds >= 0.0 and current - captured_at > float(max_age_seconds)


class DbusCacheStore:
    """RAM-owned DBus value cache with freshness and health metadata."""

    def __init__(self, paths: GatewayPaths | None = None, *, stale_after_seconds: float = 10.0) -> None:
        self.paths = paths or gateway_paths()
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))
        self.sequence = 0
        self.values: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.health: dict[str, Any] = {
            "state": "init",
            "degraded_until": 0.0,
            "timeouts_60s": 0,
            "avg_latency_ms": 0.0,
            "max_latency_ms": 0.0,
        }

    def update_value(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        status: str = "fresh",
        confidence: float = 1.0,
        last_error: str = "",
        now: float | None = None,
    ) -> None:
        current = _now() if now is None else float(now)
        self.values[str(key)] = {
            "value": _json_ready(value),
            "source": str(source),
            "updated_at": current,
            "age_s": 0.0,
            "status": str(status),
            "last_error": str(last_error),
            "confidence": float(confidence),
        }
        self.sequence += 1

    def mark_error(self, key: str, *, source: str, error: BaseException | str, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        current_value = self.values.get(str(key), {})
        self.values[str(key)] = {
            "value": current_value.get("value"),
            "source": str(source),
            "updated_at": float(current_value.get("updated_at", 0.0) or 0.0),
            "age_s": max(0.0, current - float(current_value.get("updated_at", 0.0) or 0.0)),
            "status": "error",
            "last_error": str(error),
            "confidence": 0.0,
        }
        self.sequence += 1

    def update_services(self, names: list[str], *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self.services = {str(name): {"seen_at": current, "status": "present"} for name in names}
        self.sequence += 1

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = _now() if now is None else float(now)
        values = {key: self._value_snapshot(item, current) for key, item in self.values.items()}
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "captured_at": current,
            "dbus_health": dict(self.health),
            "values": values,
            "services": dict(self.services),
        }

    def _value_snapshot(self, item: Mapping[str, Any], now: float) -> dict[str, Any]:
        updated_at = float(item.get("updated_at", 0.0) or 0.0)
        age = _value_age(updated_at, now)
        status = self._value_status(item, age)
        return {
            **dict(item),
            "age_s": age,
            "status": status,
        }

    def _value_status(self, item: Mapping[str, Any], age: float) -> str:
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
    def load_snapshot(path: str, *, max_age_seconds: float = 30.0, now: float | None = None) -> dict[str, Any]:
        payload = read_json_file(path, {})
        if not _valid_snapshot_payload(payload):
            return {}
        captured_at = float(payload.get("captured_at", 0.0) or 0.0)
        current = _now() if now is None else float(now)
        if _snapshot_too_old(captured_at, current, max_age_seconds):
            return {}
        return payload

    @staticmethod
    def value_entry(snapshot: Mapping[str, Any], key: str) -> dict[str, Any] | None:
        values = snapshot.get("values")
        if not isinstance(values, Mapping):
            return None
        item = values.get(key)
        return dict(item) if isinstance(item, Mapping) else None

