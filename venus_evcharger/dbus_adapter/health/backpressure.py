#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Backpressure policy for the DBus adapter."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.dbus_gateway_core import float_or_zero
from venus_evcharger.ipc.command_types import CommandPayload

BACKPRESSURE_SLO_REASONS = {"core_reads_fresh", "queue_age_ok"}
SLO_VIOLATION_SEQUENCE_TYPES = (list, tuple, set)


def backpressure_snapshot(
    *,
    circuit_state: str,
    queue_health: Mapping[str, object],
    slo: Mapping[str, object],
    queue_max_age_seconds: float,
) -> CommandPayload:
    queue_age = float_or_zero(queue_health.get("oldest_command_age_s"))
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
    slo: Mapping[str, object],
    *,
    queue_max_age_seconds: float,
) -> list[str]:
    reasons = [f"dbus-{circuit_state}"] if circuit_state != "ok" else []
    if queue_age > queue_max_age_seconds:
        reasons.append("queue-age")
    reasons.extend(backpressure_slo_reasons(slo))
    return reasons


def backpressure_slo_reasons(slo: Mapping[str, object]) -> list[str]:
    return [str(item) for item in slo_violations(slo) if item in BACKPRESSURE_SLO_REASONS]


def slo_violations(slo: Mapping[str, object]) -> list[object]:
    if "violated" not in slo:
        return []
    raw_violations = slo["violated"]
    return list(raw_violations) if isinstance(raw_violations, SLO_VIOLATION_SEQUENCE_TYPES) else []


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
