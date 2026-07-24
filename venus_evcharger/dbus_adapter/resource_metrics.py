# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure parsing and metric calculations for host resource samples."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal, TypeAlias, TypeGuard

from venus_evcharger.ipc.command_types import CommandPayload

CPU_IDLE_INDEX = 3
CPU_IOWAIT_INDEX = 4
MemorySampleStatus: TypeAlias = Literal["fresh", "cached", "unavailable"]


def parse_system_cpu(raw: str) -> tuple[int, int]:
    """Return total and idle CPU ticks from the first proc-stat line."""
    lines = raw.splitlines()
    if not lines:
        return 0, 0
    parts = lines[0].split()[1:]
    values = [int(float(part)) for part in parts]
    idle = _value_at(values, CPU_IDLE_INDEX) + _value_at(values, CPU_IOWAIT_INDEX)
    return sum(values), idle


def parse_process_cpu_seconds(raw: str, *, clock_ticks_per_second: float) -> float:
    """Return user and system CPU time from one process stat record."""
    _command, separator, fields_text = raw.rpartition(")")
    if not separator:
        raise ValueError("process stat command field is missing")
    fields_from_state = fields_text.split()
    ticks = float(fields_from_state[11]) + float(fields_from_state[12])
    return ticks / clock_ticks_per_second


def parse_numeric_mapping(lines: Iterable[str], *, digits_only: bool = False) -> dict[str, float]:
    """Parse numeric ``key: value`` records and fail closed on malformed lines."""
    values: dict[str, float] = {}
    for line in lines:
        key, separator, raw_value = line.partition(":")
        if not separator:
            return {}
        token = numeric_token(raw_value, digits_only=digits_only)
        if token is not None:
            values[key] = float(token)
    return values


def numeric_token(raw_value: str, *, digits_only: bool) -> str | None:
    """Return the first accepted numeric token from a procfs value."""
    token = next(iter(raw_value.strip().split()), None)
    if token is None or (digits_only and not token.isdigit()):
        return None
    return token


class CpuUsageTracker:
    """Calculate CPU percentages from successive cumulative samples."""

    def __init__(self) -> None:
        self.last_system_sample: tuple[float, int, int] | None = None
        self.last_process_sample: tuple[float, float] | None = None

    def percentages(
        self,
        *,
        now: float,
        system_cpu: tuple[int, int] | None,
        process_cpu: float | None,
        cpu_count: int,
    ) -> tuple[float | None, float | None]:
        return (
            self._system_percentage(now, system_cpu),
            self._process_percentage(now, process_cpu, cpu_count),
        )

    def _system_percentage(
        self,
        now: float,
        sample: tuple[int, int] | None,
    ) -> float | None:
        if sample is None:
            self.last_system_sample = None
            return None
        total, idle = sample
        previous = self.last_system_sample
        self.last_system_sample = (now, total, idle)
        if previous is None:
            return 0.0
        delta = _system_cpu_delta(previous, self.last_system_sample)
        if delta is None:
            return 0.0
        return _system_cpu_percentage(*delta)

    def _process_percentage(
        self,
        now: float,
        process_cpu: float | None,
        cpu_count: int,
    ) -> float | None:
        if not _valid_process_cpu(process_cpu):
            self.last_process_sample = None
            return None
        previous = self.last_process_sample
        self.last_process_sample = (now, process_cpu)
        if previous is None:
            return 0.0
        last_now, last_process_cpu = previous
        elapsed = now - last_now
        if elapsed <= 0.0:
            return 0.0
        process_delta = max(0.0, process_cpu - last_process_cpu)
        maximum = max(1, int(cpu_count)) * 100.0
        return _bounded(process_delta / elapsed * 100.0, maximum=maximum)


@dataclass(frozen=True, slots=True)
class ResourceMetrics:
    """Normalized host and process measurements for one monitor sample."""

    load: tuple[float, float, float] | None
    cpu_count: int
    system_cpu_pct: float | None
    process_cpu_pct: float | None
    meminfo: Mapping[str, float] | None
    memory_sample_status: MemorySampleStatus
    memory_sample_age_s: float | None
    process_status: Mapping[str, float] | None
    open_fds: int | None

    @property
    def load_per_cpu(self) -> float | None:
        if self.load is None:
            return None
        return max(0.0, self.load[0]) / max(1, int(self.cpu_count))

    @property
    def mem_available_kb(self) -> float | None:
        return _mapping_number(self.meminfo, "MemAvailable")

    def payload(self, *, pid: int, state: str) -> CommandPayload:
        mem_total = _mapping_number(self.meminfo, "MemTotal")
        mem_available = self.mem_available_kb
        load1, load5, load15 = self.load if self.load is not None else (None, None, None)
        return {
            "state": state,
            "loadavg_1m": load1,
            "loadavg_5m": load5,
            "loadavg_15m": load15,
            "load_per_cpu_1m": self.load_per_cpu,
            "system_cpu_pct": _bounded_optional(self.system_cpu_pct, maximum=100.0),
            "mem_total_kb": mem_total,
            "mem_available_kb": mem_available,
            "mem_available_pct": _optional_percentage(mem_available, mem_total),
            "memory_sample_status": self.memory_sample_status,
            "memory_sample_age_s": self.memory_sample_age_s,
            "process": self._process_payload(pid),
        }

    def _process_payload(self, pid: int) -> CommandPayload:
        maximum = max(1, int(self.cpu_count)) * 100.0
        return {
            "pid": pid,
            "rss_kb": _mapping_number(self.process_status, "VmRSS"),
            "rss_hwm_kb": _mapping_number(self.process_status, "VmHWM"),
            "threads": _mapping_integer(self.process_status, "Threads"),
            "fd_size": _mapping_integer(self.process_status, "FDSize"),
            "open_fds": self.open_fds,
            "cpu_pct_one_core": _bounded_optional(self.process_cpu_pct, maximum=maximum),
        }


def average(values: list[float]) -> float:
    """Return the arithmetic mean, or zero for an empty sample."""
    return sum(values) / len(values) if values else 0.0


def percentage(value: float, total: float) -> float:
    """Return a physically bounded percentage, or zero without valid inputs."""
    if not isfinite(total) or total <= 0.0:
        return 0.0
    return _bounded(value / total * 100.0, maximum=100.0)


def _value_at(values: list[int], index: int) -> int:
    return values[index] if len(values) > index else 0


def _system_cpu_percentage(total_delta: int, idle_delta: int) -> float:
    bounded_idle = min(total_delta, max(0, idle_delta))
    return (total_delta - bounded_idle) / total_delta * 100.0


def _system_cpu_delta(
    previous: tuple[float, int, int],
    current: tuple[float, int, int],
) -> tuple[int, int] | None:
    last_now, last_total, last_idle = previous
    now, total, idle = current
    total_delta = total - last_total
    idle_delta = idle - last_idle
    if now <= last_now or total_delta <= 0 or idle_delta < 0:
        return None
    return total_delta, idle_delta


def _valid_process_cpu(value: float | None) -> TypeGuard[float]:
    return value is not None and isfinite(value) and value >= 0.0


def _bounded(value: float, *, maximum: float) -> float:
    if not isfinite(value):
        return 0.0
    return min(maximum, max(0.0, value))


def _bounded_optional(value: float | None, *, maximum: float) -> float | None:
    return None if value is None else _bounded(value, maximum=maximum)


def _optional_percentage(value: float | None, total: float | None) -> float | None:
    if value is None or total is None:
        return None
    return percentage(value, total)


def _mapping_number(values: Mapping[str, float] | None, key: str) -> float | None:
    if values is None:
        return None
    value = values.get(key)
    if value is None or not isfinite(value):
        return None
    return float(value)


def _mapping_integer(values: Mapping[str, float] | None, key: str) -> int | None:
    value = _mapping_number(values, key)
    return None if value is None else int(value)
