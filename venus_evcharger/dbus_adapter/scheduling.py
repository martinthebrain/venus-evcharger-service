# SPDX-License-Identifier: GPL-3.0-or-later
"""Read and discovery scheduling for the DBus adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from venus_evcharger.dbus_adapter.read.spec import ReadSpec, read_spec_from_mapping
from venus_evcharger.dbus_gateway import write_json_file
from venus_evcharger.ipc.command_types import CommandMapping


class DbusReadScheduler:
    """Track due times for fixed DBus read groups."""

    def __init__(self, specs: Mapping[str, Mapping[str, object]]) -> None:
        self.specs: dict[str, ReadSpec] = {str(key): read_spec_from_mapping(value) for key, value in specs.items()}
        self.next_read_at: dict[str, float] = dict.fromkeys(self.specs, 0.0)
        self.failure_counts: dict[str, int] = dict.fromkeys(self.specs, 0)
        self._order: dict[str, int] = {key: index for index, key in enumerate(self.specs)}

    def next_due(
        self,
        *,
        monotonic_at: float,
        circuit_state: str,
        priority_allowed: Callable[[str], bool],
    ) -> tuple[str, ReadSpec, float] | None:
        due_keys = [
            key
            for key in self.specs
            if monotonic_at >= self.next_read_at[key]
        ]
        due_keys.sort(key=lambda key: (self.next_read_at[key], self._order[key]))
        for key in due_keys:
            spec = self.specs[key]
            interval = self.effective_interval(spec, circuit_state)
            if priority_allowed(str(spec.get("priority", "read"))):
                return key, spec, interval
            return None
        return None

    def record_success(
        self,
        key: str,
        *,
        monotonic_at: float,
        interval: float,
        interval_factor: float = 1.0,
    ) -> None:
        normalized = str(key)
        self.failure_counts[normalized] = 0
        self.next_read_at[normalized] = float(monotonic_at) + max(
            0.0,
            float(interval),
        ) * max(1.0, float(interval_factor))

    def record_error(
        self,
        key: str,
        *,
        monotonic_at: float,
        interval: float,
    ) -> None:
        normalized = str(key)
        failures = min(6, self.failure_counts.get(normalized, 0) + 1)
        self.failure_counts[normalized] = failures
        base = max(float(interval) * 10.0, 30.0)
        self.next_read_at[normalized] = float(monotonic_at) + min(
            300.0,
            base * (2 ** (failures - 1)),
        )

    def force_due(self, keys: set[str] | tuple[str, ...] | list[str]) -> None:
        """Make selected read specs due as soon as the DBus rate limiter allows."""
        for key in keys:
            normalized = str(key)
            if normalized in self.specs:
                self.next_read_at[normalized] = 0.0

    def expedite_healthy(self, keys: set[str] | tuple[str, ...] | list[str]) -> None:
        """Expedite stale reads without defeating an active failure backoff."""
        for key in keys:
            normalized = str(key)
            if (
                normalized in self.specs
                and self.failure_counts[normalized] == 0
            ):
                self.next_read_at[normalized] = 0.0

    @staticmethod
    def effective_interval(spec: Mapping[str, object], circuit_state: str) -> float:
        interval = _interval_seconds(spec.get("interval"))
        if circuit_state == "protective":
            return interval * 5.0
        if circuit_state == "degraded":
            return interval * 3.0
        return interval


class DbusDiscoveryManager:
    """Track monotonic discovery cadence plus wallclock diagnostics."""

    def __init__(
        self,
        *,
        interval_seconds: float,
        missing_pv_interval_seconds: float = 60.0,
    ) -> None:
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.missing_pv_interval_seconds = min(
            self.interval_seconds,
            max(15.0, float(missing_pv_interval_seconds)),
        )
        self.next_scan_monotonic = 0.0
        self.last_success_at = 0.0
        self.next_scan_at = 0.0
        self.last_error = ""
        self.active_interval_seconds = self.interval_seconds

    def due(
        self,
        *,
        monotonic_at: float,
        priority_allowed: Callable[[str], bool],
    ) -> bool:
        return (
            monotonic_at >= self.next_scan_monotonic
            and priority_allowed("discovery")
        )

    def record_success(
        self,
        *,
        monotonic_at: float,
        captured_at: float,
        needs_early_rescan: bool,
    ) -> None:
        interval = (
            self.missing_pv_interval_seconds
            if needs_early_rescan
            else self.interval_seconds
        )
        self.last_success_at = float(captured_at)
        self.last_error = ""
        self.active_interval_seconds = interval
        self.next_scan_monotonic = float(monotonic_at) + interval
        self.next_scan_at = float(captured_at) + interval

    def record_error(
        self,
        error: BaseException,
        *,
        monotonic_at: float,
        captured_at: float,
    ) -> None:
        retry_seconds = min(
            60.0,
            self.interval_seconds,
            self.missing_pv_interval_seconds,
        )
        self.last_error = str(error)
        self.active_interval_seconds = retry_seconds
        self.next_scan_monotonic = float(monotonic_at) + retry_seconds
        self.next_scan_at = float(captured_at) + retry_seconds

    def defer_for(
        self,
        *,
        monotonic_at: float,
        captured_at: float,
        seconds: float,
    ) -> None:
        """Defer due discovery once instead of sliding its deadline forever."""
        delay = max(0.0, float(seconds))
        current_monotonic = float(monotonic_at)
        if self.next_scan_monotonic > current_monotonic:
            return
        self.next_scan_monotonic = current_monotonic + delay
        self.next_scan_at = float(captured_at) + delay

    def force_due(self) -> None:
        """Schedule one topology refresh without exposing DBus discovery details."""
        self.next_scan_monotonic = 0.0
        self.next_scan_at = 0.0


class AtomicJsonWriter:
    """Small explicit adapter-side wrapper for atomic JSON writes."""

    def write(self, path: str, payload: CommandMapping) -> None:
        write_json_file(path, payload)


def _interval_seconds(raw_value: object) -> float:
    if isinstance(raw_value, bool):
        return 2.0
    if isinstance(raw_value, (float, int, str)):
        try:
            return float(raw_value)
        except ValueError:
            return 2.0
    return 2.0
