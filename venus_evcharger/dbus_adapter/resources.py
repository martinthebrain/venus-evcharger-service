# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource monitor orchestration for the DBus adapter."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import TypeGuard, TypeVar, cast

from venus_evcharger.dbus_adapter import resource_metrics, resource_pressure, resource_procfs
from venus_evcharger.ipc.command_types import CommandPayload

DEFAULT_MEMORY_STALE_SECONDS = 10.0
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ResourceMonitorSettings:
    """Sampling and recovery policy for host-resource observations."""

    sample_interval_seconds: float = 2.0
    recovery_hold_seconds: float = 10.0
    memory_stale_seconds: float = DEFAULT_MEMORY_STALE_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sample_interval_seconds",
            max(0.0, float(self.sample_interval_seconds)),
        )
        object.__setattr__(
            self,
            "recovery_hold_seconds",
            max(0.0, float(self.recovery_hold_seconds)),
        )
        object.__setattr__(
            self,
            "memory_stale_seconds",
            max(0.0, float(self.memory_stale_seconds)),
        )


@dataclass(frozen=True, slots=True)
class _MemoryObservation:
    captured_at: float
    values: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class _SnapshotCache:
    payload: CommandPayload
    valid_until: float


class ResourceMonitor:
    """Sample host resources at a bounded cadence and classify pressure."""

    def __init__(
        self,
        *,
        pid: int | None = None,
        settings: ResourceMonitorSettings | None = None,
        reader: resource_procfs.ResourceReader | None = None,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        policy = _or_default(settings, ResourceMonitorSettings)
        self.pid = _resolved_pid(pid)
        self.sample_interval_seconds = policy.sample_interval_seconds
        self.memory_stale_seconds = policy.memory_stale_seconds
        self._reader = _or_default(
            reader,
            lambda: resource_procfs.ProcfsResourceReader(self.pid),
        )
        self._monotonic = _or_default(monotonic, lambda: time.monotonic)
        self._wall_clock = _or_default(wall_clock, lambda: time.time)
        self._cpu_usage = resource_metrics.CpuUsageTracker()
        self._state = resource_pressure.ResourceStateLatch(
            recovery_hold_seconds=policy.recovery_hold_seconds,
        )
        self._last_memory: _MemoryObservation | None = None
        self._snapshot_cache: _SnapshotCache | None = None

    def snapshot(self) -> CommandPayload:
        now = self._monotonic()
        cache = self._snapshot_cache
        if cache is not None and now < cache.valid_until:
            return _snapshot_copy(cache.payload)
        metrics = self._measure(now)
        state = self._state.observe(
            load_per_cpu=metrics.load_per_cpu,
            cpu_pct=metrics.system_cpu_pct,
            mem_available_kb=metrics.mem_available_kb,
            now=now,
            observed_at=self._wall_clock(),
        )
        payload = metrics.payload(pid=self.pid, state=state)
        payload["pressure_evidence"] = self._state.pressure_evidence_payload()
        self._snapshot_cache = _SnapshotCache(payload, now + self.sample_interval_seconds)
        return _snapshot_copy(payload)

    def _measure(self, now: float) -> resource_metrics.ResourceMetrics:
        cpu_count = self._reader.cpu_count()
        system_cpu_pct, process_cpu_pct = self._cpu_usage.percentages(
            now=now,
            system_cpu=self._reader.system_cpu(),
            process_cpu=self._reader.process_cpu_seconds(),
            cpu_count=cpu_count,
        )
        meminfo, memory_status, memory_age = self._memory_sample(now)
        process_status = self._reader.process_status()
        load = self._reader.load_average()
        open_fds = self._reader.open_fd_count()
        return resource_metrics.ResourceMetrics(
            load=load,
            cpu_count=cpu_count,
            system_cpu_pct=system_cpu_pct,
            process_cpu_pct=process_cpu_pct,
            meminfo=meminfo,
            memory_sample_status=memory_status,
            memory_sample_age_s=memory_age,
            process_status=process_status,
            open_fds=open_fds,
        )

    def _memory_sample(
        self,
        now: float,
    ) -> tuple[
        Mapping[str, float] | None,
        resource_metrics.MemorySampleStatus,
        float | None,
    ]:
        current = self._reader.meminfo()
        if _valid_memory_sample(current):
            self._last_memory = _MemoryObservation(now, current)
            return current, "fresh", 0.0
        previous = self._last_memory
        if previous is None:
            return None, "unavailable", None
        age = max(0.0, now - previous.captured_at)
        if age <= self.memory_stale_seconds:
            return previous.values, "cached", age
        self._last_memory = None
        return None, "unavailable", None


def _valid_memory_sample(
    values: Mapping[str, float] | None,
) -> TypeGuard[Mapping[str, float]]:
    sample = _memory_values(values)
    return sample is not None and _physical_memory_values(*sample)


def _memory_values(
    values: Mapping[str, float] | None,
) -> tuple[float, float] | None:
    if values is None:
        return None
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return None
    return total, available


def _physical_memory_values(total: float, available: float) -> bool:
    return isfinite(total) and isfinite(available) and total > 0.0 and 0.0 <= available <= total


def _snapshot_copy(payload: CommandPayload) -> CommandPayload:
    copied = dict(payload)
    process = payload.get("process")
    if _is_object_mapping(process):
        copied["process"] = dict(process)
    pressure_evidence = payload.get("pressure_evidence")
    if _is_object_mapping(pressure_evidence):
        evidence_copy = dict(pressure_evidence)
        causes = pressure_evidence.get("causes")
        if _is_text_list(causes):
            evidence_copy["causes"] = list(causes)
        copied["pressure_evidence"] = evidence_copy
    return copied


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_text_list(value: object) -> TypeGuard[list[str]]:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, str) for item in cast(list[object], value))


def _resolved_pid(pid: int | None) -> int:
    return os.getpid() if pid is None else int(pid)


def _or_default(value: _T | None, factory: Callable[[], _T]) -> _T:
    return factory() if value is None else value
