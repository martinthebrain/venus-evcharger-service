#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-level objective calculations for the DBus adapter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.dbus_gateway_core import float_or_zero
from venus_evcharger.ipc.command_types import CommandPayload

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


@dataclass(frozen=True, slots=True)
class SloThresholds:
    gui_max_age_seconds: float
    core_read_max_age_seconds: float
    queue_max_age_seconds: float
    mainloop_gap_max_ms: float


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
        "gui_fresh": freshness_check(
            observed,
            age_field="gui_max_age_s",
            missing_field="gui_missing_field_count",
            max_age=gui_target,
        ),
        "gui_measurements_fresh": freshness_check(
            observed,
            age_field="gui_measurement_max_age_s",
            missing_field="gui_measurement_missing_field_count",
            max_age=gui_target,
        ),
        "gui_controls_fresh": freshness_check(
            observed,
            age_field="gui_control_max_age_s",
            missing_field="gui_control_missing_field_count",
            max_age=gui_target,
        ),
        "gui_session_fresh": freshness_check(
            observed,
            age_field="gui_session_max_age_s",
            missing_field="gui_session_missing_field_count",
            max_age=gui_target,
        ),
        "core_reads_fresh": freshness_check(
            observed,
            age_field="core_read_max_age_s",
            missing_field="core_read_missing_count",
            max_age=thresholds.core_read_max_age_seconds,
        )
        and observed_zero(observed, "core_read_nonfresh_count"),
        "queue_age_ok": observed_at_most(
            observed,
            "queue_oldest_age_s",
            thresholds.queue_max_age_seconds,
        ),
        "mainloop_gap_ok": observed_at_most(
            observed,
            "mainloop_max_gap_ms_60s",
            thresholds.mainloop_gap_max_ms,
        ),
    }


def freshness_check(
    observed: Mapping[str, float],
    *,
    age_field: str,
    missing_field: str,
    max_age: float,
) -> bool:
    return observed_at_most(observed, age_field, max_age) and observed_zero(
        observed,
        missing_field,
    )


def observed_at_most(observed: Mapping[str, float], field: str, maximum: float) -> bool:
    if field not in observed:
        return False
    value = float(observed[field])
    return 0.0 <= value <= maximum


def observed_zero(observed: Mapping[str, float], field: str) -> bool:
    return field in observed and float(observed[field]) == 0.0


def slo_targets(thresholds: SloThresholds) -> dict[str, float]:
    effective_gui_age = effective_gui_max_age_seconds(thresholds)
    return {
        "gui_max_age_s": effective_gui_age,
        "gui_measurement_max_age_s": effective_gui_age,
        "gui_control_max_age_s": effective_gui_age,
        "gui_session_max_age_s": effective_gui_age,
        "gui_missing_field_count": 0.0,
        "gui_measurement_missing_field_count": 0.0,
        "gui_control_missing_field_count": 0.0,
        "gui_session_missing_field_count": 0.0,
        "configured_gui_max_age_s": thresholds.gui_max_age_seconds,
        "core_read_max_age_s": thresholds.core_read_max_age_seconds,
        "core_read_missing_count": 0.0,
        "core_read_nonfresh_count": 0.0,
        "queue_max_age_s": thresholds.queue_max_age_seconds,
        "mainloop_gap_max_ms": thresholds.mainloop_gap_max_ms,
    }


def effective_gui_max_age_seconds(thresholds: SloThresholds) -> float:
    return max(thresholds.gui_max_age_seconds, thresholds.core_read_max_age_seconds * 2.0)


def max_core_read_age(cache_freshness: Mapping[str, object]) -> float:
    ages = [
        float_or_zero(cache_freshness.get(f"{key}_age_s"))
        for key in ("grid_power_w", "pv_power_w", "battery_soc")
        if f"{key}_age_s" in cache_freshness
    ]
    return max(ages) if ages else 0.0


def core_read_missing_count(cache_freshness: Mapping[str, object]) -> float:
    return float(
        sum(
            1
            for key in ("grid_power_w", "pv_power_w", "battery_soc")
            if str(cache_freshness.get(f"{key}_status", "missing")) == "missing"
        )
    )


def core_read_nonfresh_count(cache_freshness: Mapping[str, object]) -> float:
    return float(
        sum(
            1
            for key in ("grid_power_w", "pv_power_w", "battery_soc")
            if (
                f"{key}_status" in cache_freshness
                and str(cache_freshness[f"{key}_status"]) not in {"fresh", "missing"}
            )
        )
    )


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
    return float(float_or_zero(cache_freshness[age_key])) > max_age_seconds


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
    if eventloop_gap_ms > thresholds.mainloop_gap_max_ms:
        burst = max(1, min(burst, max(1, base_burst // 2)))
    return pressure_limited_publish_burst(burst, base_burst=base_burst, pressure_state=pressure_state)


def runtime_pressure_state(resource_state: str, backpressure_state: str) -> GatewayPressureState:
    return higher_pressure_state(
        _RESOURCE_PRESSURE_STATES.get(resource_state, "ok"),
        _BACKPRESSURE_STATES.get(backpressure_state, "ok"),
    )


def higher_pressure_state(left: GatewayPressureState, right: GatewayPressureState) -> GatewayPressureState:
    return max((left, right), key=_PRESSURE_RANK.__getitem__)


def pressure_limited_publish_burst(
    burst: int,
    *,
    base_burst: int,
    pressure_state: GatewayPressureState,
) -> int:
    validate_pressure_inputs(base_burst, pressure_state)
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
    validate_pressure_inputs(base_local_publish_burst, pressure_state)
    adjusted = dict(budgets)
    if pressure_state == "ok":
        return adjusted
    if pressure_state == "protective":
        return _with_publish_caps(adjusted, gui_cap=1, local_cap=1)
    if pressure_state == "slow":
        return _with_publish_caps(
            adjusted,
            gui_cap=max(1, base_local_publish_burst // 4),
            local_cap=1,
        )
    return _with_publish_caps(
        adjusted,
        gui_cap=max(1, base_local_publish_burst // 2),
        local_cap=max(1, base_local_publish_burst // 4),
    )


def validate_pressure_inputs(base_burst: int, pressure_state: GatewayPressureState) -> None:
    if base_burst < 1:
        raise ValueError("base publish burst must be positive")
    if pressure_state not in _PRESSURE_RANK:
        raise ValueError(f"unknown gateway pressure state: {pressure_state}")


def _with_publish_caps(
    budgets: dict[str, int],
    *,
    gui_cap: int,
    local_cap: int,
) -> dict[str, int]:
    budgets["gui-critical-publish"] = min(int(budgets.get("gui-critical-publish", gui_cap)), gui_cap)
    budgets["local-publish"] = min(int(budgets.get("local-publish", local_cap)), local_cap)
    budgets["diagnostic"] = 0
    budgets["discovery"] = 0
    budgets["introspection"] = 0
    return budgets
