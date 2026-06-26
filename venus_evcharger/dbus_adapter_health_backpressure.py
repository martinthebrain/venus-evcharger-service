#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Backpressure policy for the dedicated DBus adapter process."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BACKPRESSURE_SLO_REASONS = {"core_reads_fresh", "queue_age_ok"}


def backpressure_snapshot(
    *,
    circuit_state: str,
    queue_health: Mapping[str, Any],
    slo: Mapping[str, Any],
    queue_max_age_seconds: float,
) -> dict[str, Any]:
    queue_age = float(queue_health.get("oldest_command_age_s", 0.0) or 0.0)
    reasons = backpressure_reasons(circuit_state, queue_age, slo, queue_max_age_seconds=queue_max_age_seconds)
    state = backpressure_state(circuit_state, queue_age, reasons, queue_max_age_seconds=queue_max_age_seconds)
    return {
        "state": state,
        "core_should_throttle": state != "ok",
        "suppress_optional_commands": state in {"slow", "protective"},
        "prefer_coalescing": state != "ok",
        "reason": ",".join(dict.fromkeys(reasons)) if reasons else "ok",
    }


def backpressure_reasons(
    circuit_state: str,
    queue_age: float,
    slo: Mapping[str, Any],
    *,
    queue_max_age_seconds: float,
) -> list[str]:
    reasons = [f"dbus-{circuit_state}"] if circuit_state != "ok" else []
    if queue_age > queue_max_age_seconds:
        reasons.append("queue-age")
    reasons.extend(backpressure_slo_reasons(slo))
    return reasons


def backpressure_slo_reasons(slo: Mapping[str, Any]) -> list[str]:
    return [str(item) for item in list(slo.get("violated", []) or []) if item in BACKPRESSURE_SLO_REASONS]


def backpressure_state(
    circuit_state: str,
    queue_age: float,
    reasons: list[str],
    *,
    queue_max_age_seconds: float,
) -> str:
    if circuit_state == "protective":
        return "protective"
    if circuit_state == "degraded" or queue_age > queue_max_age_seconds * 2.0:
        return "slow"
    return "congested" if reasons else "ok"
