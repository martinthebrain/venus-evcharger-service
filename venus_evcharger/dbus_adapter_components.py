# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal building blocks for the dedicated DBus adapter process."""

from __future__ import annotations

import time
import os
from collections import deque
from typing import Any, Callable, Literal, Mapping

import dbus

from venus_evcharger.dbus_gateway import LatencyWindow, write_json_file


class DbusOperationDeferred(RuntimeError):
    """Raised when rate limiting defers one DBus operation without blocking."""


CommandOutcome = Literal["applied", "dropped", "deferred"]


class DbusRateLimiter:
    """Global DBus rate limiter for reads, writes, and introspection."""

    def __init__(
        self,
        *,
        read_interval_seconds: float = 0.25,
        write_interval_seconds: float = 0.35,
        introspection_interval_seconds: float = 2.0,
    ) -> None:
        self.intervals = {
            "read": max(0.0, float(read_interval_seconds)),
            "write": max(0.0, float(write_interval_seconds)),
            "introspection": max(0.0, float(introspection_interval_seconds)),
        }
        self.next_at = {"read": 0.0, "write": 0.0, "introspection": 0.0}

    def due(self, kind: str, *, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else float(now)) >= self.next_at[kind]

    def mark(self, kind: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        self.next_at[kind] = current + self.intervals[kind]

    def require_due(self, kind: str) -> None:
        if not self.due(kind):
            raise DbusOperationDeferred(kind)
        self.mark(kind)


class DbusCircuitBreaker:
    """Classify DBus health and suppress optional work when unstable."""

    def __init__(self, *, degraded_seconds: float = 60.0, protective_seconds: float = 180.0) -> None:
        self.latencies = LatencyWindow()
        self.latencies_by_kind: dict[str, LatencyWindow] = {}
        self.degraded_until = 0.0
        self.protective_until = 0.0
        self.degraded_seconds = max(1.0, float(degraded_seconds))
        self.protective_seconds = max(1.0, float(protective_seconds))
        self.last_success_at = 0.0
        self.last_error = ""
        self._errors: deque[tuple[float, str]] = deque()
        self._successes: deque[tuple[float, str]] = deque()
        self.consecutive_failures = 0

    def record_success(self, latency_ms: float, *, kind: str = "dbus") -> None:
        now = time.time()
        self.latencies.record_latency(latency_ms, now=now)
        self._kind_window(kind).record_latency(latency_ms, now=now)
        self._successes.append((now, str(kind or "dbus")))
        self._prune_events(now)
        self.last_success_at = now
        self.last_error = ""
        self.consecutive_failures = 0

    def record_error(self, error: BaseException, *, kind: str = "dbus") -> None:
        now = time.time()
        self.last_error = str(error)
        self._errors.append((now, str(kind or "dbus")))
        self._prune_events(now)
        self.consecutive_failures += 1
        if self._looks_like_timeout(error):
            self.latencies.record_timeout(now=now)
            self._kind_window(kind).record_timeout(now=now)
            count = int(self.latencies.summary(now=now)["timeouts_60s"])
            if count > 5:
                self.protective_until = max(self.protective_until, now + self.protective_seconds)
            elif count >= 3:
                self.degraded_until = max(self.degraded_until, now + self.degraded_seconds)

    def state(self, *, now: float | None = None) -> str:
        current = time.time() if now is None else float(now)
        if current < self.protective_until:
            return "protective"
        if current < self.degraded_until:
            return "degraded"
        return "ok"

    def allows_priority(self, priority: str) -> bool:
        rank = {"safety": 0, "user": 1, "publish": 2, "read": 3, "optional": 4, "discovery": 5, "diagnostic": 6}.get(
            str(priority or "diagnostic"),
            6,
        )
        state = self.state()
        if state == "protective":
            return rank <= 3
        if state == "degraded":
            return rank <= 4
        return True

    def health(self) -> dict[str, Any]:
        now = time.time()
        self._prune_events(now)
        summary = self.latencies.summary(now=now)
        operations = {kind: window.summary(now=now) for kind, window in sorted(self.latencies_by_kind.items())}
        return {
            "state": self.state(),
            "degraded_until": max(self.degraded_until, self.protective_until),
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "errors_60s": len(self._errors),
            "successes_60s": len(self._successes),
            "consecutive_failures": self.consecutive_failures,
            "operations": operations,
            **summary,
        }

    def _kind_window(self, kind: str) -> LatencyWindow:
        normalized = str(kind or "dbus")
        if normalized not in self.latencies_by_kind:
            self.latencies_by_kind[normalized] = LatencyWindow()
        return self.latencies_by_kind[normalized]

    def _prune_events(self, now: float) -> None:
        cutoff = now - 60.0
        while self._errors and self._errors[0][0] < cutoff:
            self._errors.popleft()
        while self._successes and self._successes[0][0] < cutoff:
            self._successes.popleft()

    @staticmethod
    def _looks_like_timeout(error: BaseException) -> bool:
        detail = str(error).lower()
        getter = getattr(error, "get_dbus_name", None)
        name = ""
        if callable(getter):
            try:
                name = str(getter()).lower()
            except Exception:  # pylint: disable=broad-except
                name = ""
        return "timeout" in detail or "noreply" in detail or "no_reply" in detail or "noreply" in name


class DbusConnectionManager:
    """Own the private system bus connection."""

    def __init__(self) -> None:
        self._bus: Any = None

    def bus(self) -> Any:
        if self._bus is None:
            self._bus = dbus.SystemBus(private=True)
        return self._bus

    def reset(self) -> None:
        close = getattr(self._bus, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pylint: disable=broad-except
                pass
        self._bus = None


class DbusReadScheduler:
    """Track due times for fixed DBus read groups."""

    def __init__(self, specs: Mapping[str, Mapping[str, Any]]) -> None:
        self.specs: dict[str, dict[str, Any]] = {str(key): dict(value) for key, value in specs.items()}
        self.next_read_at: dict[str, float] = {key: 0.0 for key in self.specs}

    def next_due(
        self,
        *,
        now: float,
        circuit_state: str,
        priority_allowed: Callable[[str], bool],
    ) -> tuple[str, Mapping[str, Any], float] | None:
        for key, spec in self.specs.items():
            interval = self._effective_interval(spec, circuit_state)
            if now < self.next_read_at.get(key, 0.0):
                continue
            if priority_allowed(str(spec.get("priority", "read"))):
                return key, spec, interval
            return None
        return None

    def record_success(self, key: str, *, now: float, interval: float) -> None:
        self.next_read_at[str(key)] = float(now) + max(0.0, float(interval))

    def record_error(self, key: str, *, now: float, interval: float) -> None:
        self.record_success(key, now=now, interval=interval)

    @staticmethod
    def _effective_interval(spec: Mapping[str, Any], circuit_state: str) -> float:
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
        durations = [duration for _timestamp, duration, _late, _gap, _late_gap in self._ticks]
        gaps = [gap for _timestamp, _duration, _late, gap, _late_gap in self._ticks if gap > 0.0]
        return {
            "tick_count_60s": len(self._ticks),
            "avg_tick_duration_ms_60s": sum(durations) / len(durations) if durations else 0.0,
            "max_tick_duration_ms_60s": max(durations) if durations else 0.0,
            "late_ticks_60s": sum(1 for _timestamp, _duration, late, _gap, _late_gap in self._ticks if late),
            "avg_tick_gap_ms_60s": sum(gaps) / len(gaps) if gaps else 0.0,
            "max_tick_gap_ms_60s": max(gaps) if gaps else 0.0,
            "late_tick_gap_count_60s": sum(1 for _timestamp, _duration, _late, _gap, late_gap in self._ticks if late_gap),
        }

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
        cpu_count = max(1, os.cpu_count() or 1)
        mem_total = float(meminfo.get("MemTotal", 0.0) or 0.0)
        mem_available = float(meminfo.get("MemAvailable", 0.0) or 0.0)
        mem_available_pct = (mem_available / mem_total * 100.0) if mem_total > 0.0 else 0.0
        return {
            "state": self._resource_state(load1 / cpu_count, system_cpu_pct, mem_available),
            "loadavg_1m": load1,
            "loadavg_5m": load5,
            "loadavg_15m": load15,
            "load_per_cpu_1m": load1 / cpu_count,
            "system_cpu_pct": system_cpu_pct,
            "mem_total_kb": mem_total,
            "mem_available_kb": mem_available,
            "mem_available_pct": mem_available_pct,
            "process": {
                "pid": self.pid,
                "rss_kb": float(status.get("VmRSS", 0.0) or 0.0),
                "rss_hwm_kb": float(status.get("VmHWM", 0.0) or 0.0),
                "threads": int(status.get("Threads", 0) or 0),
                "fd_size": int(status.get("FDSize", 0) or 0),
                "open_fds": self._open_fd_count(),
                "cpu_pct_one_core": process_cpu_pct,
            },
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
    def _resource_state(load_per_cpu: float, cpu_pct: float, mem_available_kb: float) -> str:
        if load_per_cpu >= 1.5 or cpu_pct >= 90.0 or mem_available_kb < 32768.0:
            return "constrained"
        if load_per_cpu >= 1.0 or cpu_pct >= 80.0 or mem_available_kb < 65536.0:
            return "busy"
        return "ok"

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
            with open("/proc/stat", "r", encoding="utf-8") as handle:
                parts = handle.readline().split()[1:]
        except OSError:
            return 0, 0
        values = [int(float(part)) for part in parts]
        idle = (values[3] if len(values) > 3 else 0) + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def _read_process_cpu_seconds(self) -> float:
        try:
            with open(f"/proc/{self.pid}/stat", "r", encoding="utf-8") as handle:
                parts = handle.read().split()
            ticks = float(parts[13]) + float(parts[14])
            return ticks / float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        except (OSError, IndexError, KeyError, ValueError):
            return 0.0

    @staticmethod
    def _read_meminfo() -> dict[str, float]:
        values: dict[str, float] = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, raw_value = line.split(":", 1)
                    values[key] = float(raw_value.strip().split()[0])
        except (OSError, ValueError, IndexError):
            return {}
        return values

    def _read_process_status(self) -> dict[str, float]:
        values: dict[str, float] = {}
        try:
            with open(f"/proc/{self.pid}/status", "r", encoding="utf-8") as handle:
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
