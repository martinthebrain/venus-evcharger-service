#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""SLO calculations for the dedicated DBus adapter process."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from venus_evcharger.dbus_gateway_command_types import CommandPayload
from venus_evcharger.dbus_gateway_core import float_or_zero


@dataclass(frozen=True)
class SloThresholds:
    gui_max_age_seconds: float
    core_read_max_age_seconds: float
    queue_max_age_seconds: float
    mainloop_gap_max_ms: float
    tick_seconds: float
    max_tick_seconds: float


def slo_payload(
    checks: Mapping[str, bool],
    targets: Mapping[str, float],
    observed: Mapping[str, float],
) -> CommandPayload:
    violated = [name for name, ok in checks.items() if not ok]
    return {
        "state": "violated" if violated else "ok",
        "violated": violated,
        "checks": dict(checks),
        "targets": dict(targets),
        "observed": dict(observed),
    }


def slo_checks_from_observed(observed: Mapping[str, float], thresholds: SloThresholds) -> dict[str, bool]:
    gui_target = effective_gui_max_age_seconds(thresholds)
    return {
        "gui_fresh": float(observed.get("gui_max_age_s", 0.0)) <= gui_target,
        "gui_measurements_fresh": float(observed.get("gui_measurement_max_age_s", 0.0)) <= gui_target,
        "gui_controls_fresh": float(observed.get("gui_control_max_age_s", 0.0)) <= gui_target,
        "gui_session_fresh": float(observed.get("gui_session_max_age_s", 0.0)) <= gui_target,
        "core_reads_fresh": float(observed.get("core_read_max_age_s", 0.0)) <= thresholds.core_read_max_age_seconds,
        "queue_age_ok": float(observed.get("queue_oldest_age_s", 0.0)) <= thresholds.queue_max_age_seconds,
        "mainloop_gap_ok": float(observed.get("mainloop_max_gap_ms_60s", 0.0))
        <= effective_mainloop_gap_max_ms(thresholds),
    }


def slo_targets(thresholds: SloThresholds) -> dict[str, float]:
    effective_gui_age = effective_gui_max_age_seconds(thresholds)
    return {
        "gui_max_age_s": effective_gui_age,
        "gui_measurement_max_age_s": effective_gui_age,
        "gui_control_max_age_s": effective_gui_age,
        "gui_session_max_age_s": effective_gui_age,
        "configured_gui_max_age_s": thresholds.gui_max_age_seconds,
        "core_read_max_age_s": thresholds.core_read_max_age_seconds,
        "queue_max_age_s": thresholds.queue_max_age_seconds,
        "mainloop_gap_max_ms": effective_mainloop_gap_max_ms(thresholds),
    }


def effective_gui_max_age_seconds(thresholds: SloThresholds) -> float:
    return max(thresholds.gui_max_age_seconds, thresholds.core_read_max_age_seconds * 2.0)


def effective_mainloop_gap_max_ms(thresholds: SloThresholds) -> float:
    adaptive_tick_ms = max(thresholds.tick_seconds, thresholds.max_tick_seconds) * 1000.0
    return max(thresholds.mainloop_gap_max_ms, adaptive_tick_ms * 2.5)


def max_core_read_age(cache_freshness: Mapping[str, object]) -> float:
    ages = [
        float_or_zero(cache_freshness.get(f"{key}_age_s"))
        for key in ("grid_power_w", "pv_power_w", "battery_soc")
        if f"{key}_age_s" in cache_freshness
    ]
    return max(ages) if ages else 0.0


def stale_core_read_keys(
    cache_freshness: Mapping[str, object],
    keys: Iterable[str],
    *,
    max_age_seconds: float,
) -> set[str]:
    return {key for key in keys if core_read_stale(key, cache_freshness, max_age_seconds=max_age_seconds)}


def core_read_stale(key: str, cache_freshness: Mapping[str, object], *, max_age_seconds: float) -> bool:
    status_key = f"{key}_status"
    age_key = f"{key}_age_s"
    if status_key not in cache_freshness or age_key not in cache_freshness:
        return True
    if str(cache_freshness[status_key]) != "fresh":
        return True
    return float_or_zero(cache_freshness[age_key]) > max_age_seconds


def regulated_publish_burst(
    *,
    queue_age: float,
    eventloop_gap_ms: float,
    base_burst: int,
    thresholds: SloThresholds,
) -> int:
    burst = base_burst
    if queue_age > thresholds.queue_max_age_seconds:
        burst = min(max(burst * 3, burst + 4), 50)
    if eventloop_gap_ms > effective_mainloop_gap_max_ms(thresholds):
        burst = max(1, min(burst, max(1, base_burst // 2)))
    return int(burst)
