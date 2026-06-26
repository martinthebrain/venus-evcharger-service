# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter component helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from venus_evcharger.dbus_gateway import write_json_file


class DbusReadScheduler:
    """Track due times for fixed DBus read groups."""

    def __init__(self, specs: Mapping[str, Mapping[str, Any]]) -> None:
        self.specs: dict[str, dict[str, Any]] = {str(key): dict(value) for key, value in specs.items()}
        self.next_read_at: dict[str, float] = dict.fromkeys(self.specs, 0.0)
        self.failure_counts: dict[str, int] = dict.fromkeys(self.specs, 0)
        self._order: dict[str, int] = {key: index for index, key in enumerate(self.specs)}

    def next_due(
        self,
        *,
        now: float,
        circuit_state: str,
        priority_allowed: Callable[[str], bool],
    ) -> tuple[str, Mapping[str, Any], float] | None:
        due_keys = [
            key
            for key in self.specs
            if now >= self.next_read_at.get(key, 0.0)
        ]
        due_keys.sort(key=lambda key: (self.next_read_at.get(key, 0.0), self._order.get(key, 0)))
        for key in due_keys:
            spec = self.specs[key]
            interval = self.effective_interval(spec, circuit_state)
            if priority_allowed(str(spec.get("priority", "read"))):
                return key, spec, interval
            return None
        return None

    def record_success(self, key: str, *, now: float, interval: float) -> None:
        normalized = str(key)
        self.failure_counts[normalized] = 0
        self.next_read_at[normalized] = float(now) + max(0.0, float(interval))

    def record_error(self, key: str, *, now: float, interval: float) -> None:
        normalized = str(key)
        failures = min(6, self.failure_counts.get(normalized, 0) + 1)
        self.failure_counts[normalized] = failures
        base = max(float(interval) * 10.0, 30.0)
        self.next_read_at[normalized] = float(now) + min(300.0, base * (2 ** (failures - 1)))

    def force_due(self, keys: set[str] | tuple[str, ...] | list[str]) -> None:
        """Make selected read specs due as soon as the DBus rate limiter allows."""
        for key in keys:
            normalized = str(key)
            if normalized in self.specs:
                self.next_read_at[normalized] = 0.0

    @staticmethod
    def effective_interval(spec: Mapping[str, Any], circuit_state: str) -> float:
        interval = float(spec.get("interval", 2.0))
        if circuit_state == "protective":
            return interval * 5.0
        if circuit_state == "degraded":
            return interval * 3.0
        return interval


class DbusDiscoveryManager:
    """Track service discovery cadence and diagnostic state."""

    def __init__(self, *, interval_seconds: float) -> None:
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.next_scan_at = 0.0
        self.last_success_at = 0.0
        self.last_error = ""

    def due(self, *, now: float, priority_allowed: Callable[[str], bool]) -> bool:
        return now >= self.next_scan_at and priority_allowed("discovery")

    def record_success(self, *, now: float) -> None:
        self.last_success_at = float(now)
        self.last_error = ""
        self.next_scan_at = float(now) + self.interval_seconds

    def record_error(self, error: BaseException, *, now: float) -> None:
        self.last_error = str(error)
        self.next_scan_at = float(now) + min(60.0, self.interval_seconds)


class AtomicJsonWriter:
    """Small explicit adapter-side wrapper for atomic JSON writes."""

    def write(self, path: str, payload: Mapping[str, Any]) -> None:
        write_json_file(path, payload)
