# SPDX-License-Identifier: GPL-3.0-or-later
"""Host and procfs data capture for DBus adapter resource monitoring."""

from __future__ import annotations

import os
from collections.abc import Mapping
from math import isfinite
from typing import Protocol

from venus_evcharger.dbus_adapter.resource_metrics import (
    parse_numeric_mapping,
    parse_process_cpu_seconds,
    parse_system_cpu,
)


class ResourceReader(Protocol):
    """Raw resource source consumed by ``ResourceMonitor``."""

    def system_cpu(self) -> tuple[int, int] | None: ...
    def process_cpu_seconds(self) -> float | None: ...
    def meminfo(self) -> Mapping[str, float] | None: ...
    def process_status(self) -> Mapping[str, float] | None: ...
    def load_average(self) -> tuple[float, float, float] | None: ...
    def cpu_count(self) -> int: ...
    def open_fd_count(self) -> int | None: ...


class ProcfsResourceReader:
    """Capture one process and its host through Linux procfs and ``os``."""

    def __init__(self, pid: int) -> None:
        self.pid = int(pid)

    def system_cpu(self) -> tuple[int, int] | None:
        try:
            with open("/proc/stat", encoding="utf-8") as handle:
                total, idle = parse_system_cpu(handle.readline())
            return (total, idle) if total > 0 and 0 <= idle <= total else None
        except (OSError, ValueError):
            return None

    def process_cpu_seconds(self) -> float | None:
        try:
            with open(f"/proc/{self.pid}/stat", encoding="utf-8") as handle:
                raw = handle.read()
            ticks_per_second = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
            seconds = parse_process_cpu_seconds(raw, clock_ticks_per_second=ticks_per_second)
            return seconds if isfinite(seconds) and seconds >= 0.0 else None
        except (OSError, IndexError, KeyError, ValueError):
            return None

    def meminfo(self) -> Mapping[str, float] | None:
        return self._numeric_mapping("/proc/meminfo")

    def process_status(self) -> Mapping[str, float] | None:
        return self._numeric_mapping(f"/proc/{self.pid}/status", digits_only=True)

    @staticmethod
    def load_average() -> tuple[float, float, float] | None:
        try:
            load1, load5, load15 = os.getloadavg()
            values = float(load1), float(load5), float(load15)
            return values if all(isfinite(value) and value >= 0.0 for value in values) else None
        except OSError:
            return None

    @staticmethod
    def cpu_count() -> int:
        return max(1, os.cpu_count() or 1)

    def open_fd_count(self) -> int | None:
        try:
            return len(os.listdir(f"/proc/{self.pid}/fd"))
        except OSError:
            return None

    @staticmethod
    def _numeric_mapping(
        path: str,
        *,
        digits_only: bool = False,
    ) -> Mapping[str, float] | None:
        try:
            with open(path, encoding="utf-8") as handle:
                values = parse_numeric_mapping(handle, digits_only=digits_only)
            return values or None
        except (OSError, ValueError, IndexError):
            return None
