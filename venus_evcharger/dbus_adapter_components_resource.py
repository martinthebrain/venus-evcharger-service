# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus adapter component helpers."""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Mapping
from typing import Any


class TickHealth:
    """Rolling event-loop tick diagnostics without touching DBus."""

    def __init__(self, *, window_seconds: float = 60.0) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self._ticks: deque[tuple[float, float, bool, float, bool]] = deque()
        self._last_tick_start = 0.0

    def record(self, *, duration_ms: float, expected_interval_s: float, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        late = float(duration_ms) > max(0.0, float(expected_interval_s)) * 1000.0 * 2.0
        gap_ms = max(0.0, (current - self._last_tick_start) * 1000.0) if self._last_tick_start > 0.0 else 0.0
        late_gap = gap_ms > max(0.0, float(expected_interval_s)) * 1000.0 * 2.0 if gap_ms > 0.0 else False
        self._last_tick_start = current
        self._ticks.append((current, max(0.0, float(duration_ms)), late, gap_ms, late_gap))
        self._prune(current)

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.monotonic() if now is None else float(now)
        self._prune(current)
        durations = self._durations()
        gaps = self._gaps()
        return {
            "tick_count_60s": len(self._ticks),
            "avg_tick_duration_ms_60s": _average(durations),
            "max_tick_duration_ms_60s": max(durations) if durations else 0.0,
            "late_ticks_60s": self._late_tick_count(),
            "avg_tick_gap_ms_60s": _average(gaps),
            "max_tick_gap_ms_60s": max(gaps) if gaps else 0.0,
            "late_tick_gap_count_60s": self._late_gap_count(),
        }

    def _durations(self) -> list[float]:
        return [duration for _timestamp, duration, _late, _gap, _late_gap in self._ticks]

    def _gaps(self) -> list[float]:
        return [gap for _timestamp, _duration, _late, gap, _late_gap in self._ticks if gap > 0.0]

    def _late_tick_count(self) -> int:
        return sum(1 for _timestamp, _duration, late, _gap, _late_gap in self._ticks if late)

    def _late_gap_count(self) -> int:
        return sum(1 for _timestamp, _duration, _late, _gap, late_gap in self._ticks if late_gap)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._ticks and self._ticks[0][0] < cutoff:
            self._ticks.popleft()


class ResourceMonitor:
    """Read lightweight CPU/RAM/process diagnostics from procfs only."""

    def __init__(self, *, pid: int | None = None) -> None:
        self.pid = os.getpid() if pid is None else int(pid)
        self._last_sample: tuple[float, int, int, float] | None = None

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        total, idle = self._read_system_cpu()
        proc_cpu = self._read_process_cpu_seconds()
        system_cpu_pct, process_cpu_pct = self._cpu_percentages(now, total, idle, proc_cpu)
        meminfo = self._read_meminfo()
        status = self._read_process_status()
        load1, load5, load15 = self._loadavg()
        return self._snapshot_payload(
            load=(load1, load5, load15),
            meminfo=meminfo,
            process_cpu_pct=process_cpu_pct,
            status=status,
            system_cpu_pct=system_cpu_pct,
        )

    def _snapshot_payload(
        self,
        *,
        load: tuple[float, float, float],
        meminfo: Mapping[str, float],
        process_cpu_pct: float,
        status: Mapping[str, float],
        system_cpu_pct: float,
    ) -> dict[str, Any]:
        load1, load5, load15 = load
        cpu_count = max(1, os.cpu_count() or 1)
        mem_total = float(meminfo.get("MemTotal", 0.0) or 0.0)
        mem_available = float(meminfo.get("MemAvailable", 0.0) or 0.0)
        return {
            "state": _resource_state(load1 / cpu_count, system_cpu_pct, mem_available),
            "loadavg_1m": load1,
            "loadavg_5m": load5,
            "loadavg_15m": load15,
            "load_per_cpu_1m": load1 / cpu_count,
            "system_cpu_pct": system_cpu_pct,
            "mem_total_kb": mem_total,
            "mem_available_kb": mem_available,
            "mem_available_pct": _percentage(mem_available, mem_total),
            "process": self._process_snapshot(status, process_cpu_pct),
        }

    def _process_snapshot(self, status: Mapping[str, float], process_cpu_pct: float) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "rss_kb": float(status.get("VmRSS", 0.0) or 0.0),
            "rss_hwm_kb": float(status.get("VmHWM", 0.0) or 0.0),
            "threads": int(status.get("Threads", 0) or 0),
            "fd_size": int(status.get("FDSize", 0) or 0),
            "open_fds": self._open_fd_count(),
            "cpu_pct_one_core": process_cpu_pct,
        }

    def _cpu_percentages(self, now: float, total: int, idle: int, proc_cpu: float) -> tuple[float, float]:
        if self._last_sample is None:
            self._last_sample = (now, total, idle, proc_cpu)
            return 0.0, 0.0
        last_now, last_total, last_idle, last_proc_cpu = self._last_sample
        self._last_sample = (now, total, idle, proc_cpu)
        total_delta = max(0, total - last_total)
        idle_delta = max(0, idle - last_idle)
        elapsed = max(0.001, now - last_now)
        system_pct = ((total_delta - idle_delta) / total_delta * 100.0) if total_delta > 0 else 0.0
        process_pct = max(0.0, (proc_cpu - last_proc_cpu) / elapsed * 100.0)
        return system_pct, process_pct

    @staticmethod
    def _loadavg() -> tuple[float, float, float]:
        try:
            load1, load5, load15 = os.getloadavg()
            return float(load1), float(load5), float(load15)
        except OSError:
            return 0.0, 0.0, 0.0

    @staticmethod
    def _read_system_cpu() -> tuple[int, int]:
        try:
            with open("/proc/stat", encoding="utf-8") as handle:
                parts = handle.readline().split()[1:]
        except OSError:
            return 0, 0
        values = [int(float(part)) for part in parts]
        idle = (values[3] if len(values) > 3 else 0) + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def _read_process_cpu_seconds(self) -> float:
        try:
            with open(f"/proc/{self.pid}/stat", encoding="utf-8") as handle:
                parts = handle.read().split()
            ticks = float(parts[13]) + float(parts[14])
            return ticks / float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        except (OSError, IndexError, KeyError, ValueError):
            return 0.0

    @staticmethod
    def _read_meminfo() -> dict[str, float]:
        values: dict[str, float] = {}
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, raw_value = line.split(":", 1)
                    values[key] = float(raw_value.strip().split()[0])
        except (OSError, ValueError, IndexError):
            return {}
        return values

    def _read_process_status(self) -> dict[str, float]:
        values: dict[str, float] = {}
        try:
            with open(f"/proc/{self.pid}/status", encoding="utf-8") as handle:
                for line in handle:
                    key, raw_value = line.split(":", 1)
                    token = raw_value.strip().split()[0]
                    if token.isdigit():
                        values[key] = float(token)
        except (OSError, ValueError, IndexError):
            return {}
        return values

    def _open_fd_count(self) -> int:
        try:
            return len(os.listdir(f"/proc/{self.pid}/fd"))
        except OSError:
            return 0

    @staticmethod
    def _resource_state(load_per_cpu: float, cpu_pct: float, mem_available_kb: float) -> str:
        return _resource_state(load_per_cpu, cpu_pct, mem_available_kb)


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentage(value: float, total: float) -> float:
    return (value / total * 100.0) if total > 0.0 else 0.0


def _resource_state(load_per_cpu: float, cpu_pct: float, mem_available_kb: float) -> str:
    if _resource_constrained(load_per_cpu, cpu_pct, mem_available_kb):
        return "constrained"
    if _resource_busy(load_per_cpu, cpu_pct, mem_available_kb):
        return "busy"
    return "ok"


def _resource_constrained(load_per_cpu: float, cpu_pct: float, mem_available_kb: float) -> bool:
    return load_per_cpu >= 1.5 or cpu_pct >= 90.0 or mem_available_kb < 32768.0


def _resource_busy(load_per_cpu: float, cpu_pct: float, mem_available_kb: float) -> bool:
    return load_per_cpu >= 1.0 or cpu_pct >= 80.0 or mem_available_kb < 65536.0

