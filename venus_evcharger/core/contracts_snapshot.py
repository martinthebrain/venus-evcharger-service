# SPDX-License-Identifier: GPL-3.0-or-later
"""Snapshot and cutover normalization contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.contracts_basic import (
    finite_float_or_none,
    non_negative_float_or_none,
    normalize_binary_flag,
    normalize_learning_state,
    normalized_auto_state_pair,
    timestamp_age_within,
    timestamp_not_future,
)
from venus_evcharger.core.contracts_outward import sanitized_auto_metrics


def _mapping_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _snapshot_mapping(snapshot: object) -> dict[str, Any]:
    return _mapping_payload(snapshot) or {}


def _resolved_snapshot_captured_at(
    captured_at: float | None,
    pm_captured_at: float | None,
    current: float | None,
) -> float:
    if captured_at is not None:
        return float(captured_at)
    if current is not None:
        return float(current)
    if pm_captured_at is not None:
        return float(pm_captured_at)
    return 0.0


def _clamped_snapshot_timestamp(
    timestamp: float,
    current: float | None,
    *,
    future_tolerance_seconds: float,
    clamp_future_timestamps: bool,
) -> float:
    if (
        current is not None
        and clamp_future_timestamps
        and not timestamp_not_future(timestamp, current, future_tolerance_seconds)
    ):
        return float(current)
    return float(timestamp)


def _valid_pm_snapshot_payload(pm_status: dict[str, Any] | None) -> bool:
    return isinstance(pm_status, dict) and "output" in pm_status


def _pm_snapshot_future_invalid(
    timestamp: float,
    current: float | None,
    *,
    future_tolerance_seconds: float,
    clamp_future_timestamps: bool,
) -> bool:
    return bool(
        current is not None
        and clamp_future_timestamps
        and not timestamp_not_future(timestamp, current, future_tolerance_seconds)
    )


def _snapshot_pm_payload(normalized: dict[str, Any]) -> tuple[dict[str, Any] | None, float | None, bool]:
    pm_status_raw = normalized.get("pm_status")
    pm_status = _mapping_payload(pm_status_raw)
    pm_captured_at = non_negative_float_or_none(normalized.get("pm_captured_at"))
    pm_confirmed = bool(normalized.get("pm_confirmed", False))
    return pm_status, pm_captured_at, pm_confirmed


def _normalized_pm_snapshot_payload(
    *,
    pm_status: dict[str, Any] | None,
    pm_captured_at: float | None,
    pm_confirmed: bool,
    captured_at: float,
    current: float | None,
    future_tolerance_seconds: float,
    clamp_future_timestamps: bool,
) -> tuple[dict[str, Any] | None, float | None, bool, float]:
    if not _valid_pm_snapshot_payload(pm_status):
        return None, None, False, float(captured_at)
    resolved_pm_captured_at = float(captured_at) if pm_captured_at is None else float(pm_captured_at)
    if _pm_snapshot_future_invalid(
        resolved_pm_captured_at,
        current,
        future_tolerance_seconds=future_tolerance_seconds,
        clamp_future_timestamps=clamp_future_timestamps,
    ):
        return None, None, False, float(captured_at)
    return pm_status, resolved_pm_captured_at, bool(pm_confirmed), max(float(captured_at), resolved_pm_captured_at)


def _apply_normalized_pm_payload(
    normalized: dict[str, Any],
    *,
    resolved_captured_at: float,
    pm_status: dict[str, Any] | None,
    pm_captured_at: float | None,
    pm_confirmed: bool,
) -> None:
    normalized["captured_at"] = float(resolved_captured_at)
    normalized["pm_status"] = pm_status
    normalized["pm_captured_at"] = None if pm_captured_at is None else float(pm_captured_at)
    normalized["pm_confirmed"] = bool(pm_confirmed and pm_status is not None and pm_captured_at is not None)


def normalized_worker_snapshot(
    snapshot: Any,
    *,
    now: float | None = None,
    future_tolerance_seconds: float = 1.0,
    clamp_future_timestamps: bool = True,
) -> dict[str, Any]:
    normalized = _snapshot_mapping(snapshot)
    current = None if now is None else float(now)
    captured_at = non_negative_float_or_none(normalized.get("captured_at"))
    pm_status, pm_captured_at, pm_confirmed = _snapshot_pm_payload(normalized)
    resolved_captured_at = _resolved_snapshot_captured_at(captured_at, pm_captured_at, current)
    resolved_captured_at = _clamped_snapshot_timestamp(
        resolved_captured_at,
        current,
        future_tolerance_seconds=future_tolerance_seconds,
        clamp_future_timestamps=clamp_future_timestamps,
    )
    pm_status, pm_captured_at, pm_confirmed, resolved_captured_at = _normalized_pm_snapshot_payload(
        pm_status=pm_status,
        pm_captured_at=pm_captured_at,
        pm_confirmed=pm_confirmed,
        captured_at=resolved_captured_at,
        current=current,
        future_tolerance_seconds=future_tolerance_seconds,
        clamp_future_timestamps=clamp_future_timestamps,
    )
    _apply_normalized_pm_payload(
        normalized,
        resolved_captured_at=resolved_captured_at,
        pm_status=pm_status,
        pm_captured_at=pm_captured_at,
        pm_confirmed=pm_confirmed,
    )
    return normalized


def normalized_auto_decision_trace(
    *,
    health_reason: Any,
    cached_inputs: bool,
    relay_intent: Any,
    learned_charge_power_state: Any,
    metrics: Any,
    health_code_func: Any,
    derive_auto_state_func: Any,
) -> dict[str, Any]:
    base_reason = str(health_reason).replace("-cached", "") if health_reason is not None else "init"
    cached = bool(cached_inputs)
    normalized_reason = f"{base_reason}-cached" if cached else base_reason
    normalized_health_code = int(health_code_func(base_reason)) + (100 if cached else 0)
    normalized_relay_intent = normalize_binary_flag(relay_intent)
    normalized_metrics = sanitized_auto_metrics(metrics)
    normalized_metrics["relay_intent"] = normalized_relay_intent
    learned_state = normalized_metrics.get("learned_charge_power_state")
    if learned_state is None:
        learned_state = normalize_learning_state(learned_charge_power_state)
    normalized_state = derive_auto_state_func(
        base_reason,
        relay_on=bool(normalized_relay_intent),
        learned_charge_power_state=learned_state,
    )
    normalized_state, normalized_state_code = normalized_auto_state_pair(normalized_state, None)
    normalized_metrics["state"] = normalized_state
    return {
        "health_reason": normalized_reason,
        "health_code": normalized_health_code,
        "state": normalized_state,
        "state_code": normalized_state_code,
        "metrics": normalized_metrics,
    }


def _cutover_blocked_by_pending_or_active(*, pending_state: Any, relay_on: bool, confirmed_output: Any) -> bool:
    return pending_state is not None or bool(relay_on) or bool(confirmed_output)


def _cutover_confirmation_recent(
    *,
    confirmed_at: Any,
    now: float,
    max_age_seconds: float,
    future_tolerance_seconds: float,
) -> bool:
    return timestamp_age_within(
        confirmed_at,
        now,
        max_age_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    )


def _confirmed_after_requested(confirmed_at: Any, requested_at: Any) -> bool:
    confirmed_timestamp = finite_float_or_none(confirmed_at)
    requested_timestamp = finite_float_or_none(requested_at)
    if confirmed_timestamp is None or requested_timestamp is None:
        return False
    return confirmed_timestamp >= requested_timestamp


def _cutover_request_satisfied(*, confirmed_at: Any, requested_at: Any) -> bool:
    return True if requested_at is None else _confirmed_after_requested(confirmed_at, requested_at)


def cutover_confirmed_off(
    *,
    relay_on: bool,
    pending_state: Any,
    confirmed_output: Any,
    confirmed_at: Any,
    requested_at: Any,
    now: float,
    max_age_seconds: float,
    future_tolerance_seconds: float = 1.0,
) -> bool:
    if _cutover_blocked_by_pending_or_active(
        pending_state=pending_state,
        relay_on=relay_on,
        confirmed_output=confirmed_output,
    ):
        return False
    if not _cutover_confirmation_recent(
        confirmed_at=confirmed_at,
        now=now,
        max_age_seconds=max_age_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    ):
        return False
    return _cutover_request_satisfied(confirmed_at=confirmed_at, requested_at=requested_at)


def write_failure_is_reversible(side_effect_started: bool) -> bool:
    return not bool(side_effect_started)
