#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""SLO calculations for the dedicated DBus adapter process."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.dbus_gateway_command_types import CommandPayload
from venus_evcharger.dbus_gateway_core import float_or_zero

GatewayPressureState = Literal["ok", "congested", "slow", "protective"]
_PRESSURE_RANK: dict[GatewayPressureState, int] = {
    "ok": 0,
    "congested": 1,
    "slow": 2,
    "protective": 3,
}
_RESOURCE_PRESSURE_STATES: dict[str, GatewayPressureState] = {
    "busy": "congested",
    "constrained": "slow",
}
_BACKPRESSURE_STATES: dict[str, GatewayPressureState] = {
    "congested": "congested",
    "slow": "slow",
    "protective": "protective",
}


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
    pressure_state: GatewayPressureState = "ok",
) -> int:
    burst = base_burst
    if queue_age > thresholds.queue_max_age_seconds:
        burst = min(max(burst * 3, burst + 4), 50)
    if eventloop_gap_ms > effective_mainloop_gap_max_ms(thresholds):
        burst = max(1, min(burst, max(1, base_burst // 2)))
    return pressure_limited_publish_burst(burst, base_burst=base_burst, pressure_state=pressure_state)


def runtime_pressure_state(resource_state: str, backpressure_state: str) -> GatewayPressureState:
    return higher_pressure_state(
        _RESOURCE_PRESSURE_STATES.get(resource_state, "ok"),
        _BACKPRESSURE_STATES.get(backpressure_state, "ok"),
    )


def higher_pressure_state(left: GatewayPressureState, right: GatewayPressureState) -> GatewayPressureState:
    return left if _PRESSURE_RANK[left] >= _PRESSURE_RANK[right] else right


def pressure_limited_publish_burst(
    burst: int,
    *,
    base_burst: int,
    pressure_state: GatewayPressureState,
) -> int:
    if pressure_state == "protective":
        return 1
    if pressure_state == "slow":
        return max(1, min(burst, max(1, base_burst // 4)))
    if pressure_state == "congested":
        return max(1, min(burst, max(1, base_burst // 2)))
    return max(1, burst)


def pressure_limited_queue_budgets(
    budgets: Mapping[str, int],
    *,
    base_local_publish_burst: int,
    pressure_state: GatewayPressureState,
) -> dict[str, int]:
    adjusted = dict(budgets)
    if pressure_state == "ok":
        return adjusted
    if pressure_state == "protective":
        return _with_publish_caps(adjusted, gui_cap=1, local_cap=1, diagnostic_cap=0)
    if pressure_state == "slow":
        return _with_publish_caps(
            adjusted,
            gui_cap=max(1, base_local_publish_burst // 4),
            local_cap=1,
            diagnostic_cap=0,
        )
    return _with_publish_caps(
        adjusted,
        gui_cap=max(1, base_local_publish_burst // 2),
        local_cap=max(1, base_local_publish_burst // 4),
        diagnostic_cap=0,
    )


def _with_publish_caps(
    budgets: dict[str, int],
    *,
    gui_cap: int,
    local_cap: int,
    diagnostic_cap: int,
) -> dict[str, int]:
    budgets["gui-critical-publish"] = min(int(budgets.get("gui-critical-publish", gui_cap)), gui_cap)
    budgets["local-publish"] = min(int(budgets.get("local-publish", local_cap)), local_cap)
    budgets["diagnostic"] = min(int(budgets.get("diagnostic", diagnostic_cap)), diagnostic_cap)
    return budgets
