# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared broad Auto-state, charger transport, and relay freshness helpers."""

from __future__ import annotations

import math
import time
from typing import Any

from venus_evcharger.core.common_types import (
    AUTO_STATE_CODES,
    BLOCKED_AUTO_REASONS,
    CHARGING_AUTO_REASONS,
    EVSE_FAULT_HEALTH_REASONS,
    HEALTH_CODES,
    RECOVERY_AUTO_REASONS,
    WAITING_AUTO_REASONS,
)
from venus_evcharger.core.contracts import finite_float_or_none


def _health_code(reason: str) -> int:
    return HEALTH_CODES.get(reason, 99)


def _normalize_auto_state(state: Any) -> str:
    if state is None:
        return "idle"
    normalized = str(state).strip().lower()
    return normalized if normalized in AUTO_STATE_CODES else "idle"


def _auto_state_code(state: Any) -> int:
    return AUTO_STATE_CODES[_normalize_auto_state(state)]


def _base_auto_reason(reason: Any) -> str:
    return str(reason).replace("-cached", "") if reason is not None else "init"


def _evse_fault_reason(reason: Any) -> str | None:
    normalized = _base_auto_reason(reason)
    return normalized if normalized in EVSE_FAULT_HEALTH_REASONS else None


def _normalized_learning_hint(learned_charge_power_state: Any) -> str:
    return str(learned_charge_power_state).strip().lower() if learned_charge_power_state is not None else "unknown"


def _relay_on_auto_state(learned_state: str) -> str:
    return "learning" if learned_state == "learning" else "charging"


def _reason_resolved_auto_state(state_from_reason: str, relay_on: bool, learned_state: str) -> str:
    if state_from_reason == "charging":
        return "learning" if relay_on and learned_state == "learning" else "charging"
    return state_from_reason


AUTO_REASON_STATE_GROUPS: tuple[tuple[set[str], str], ...] = (
    (RECOVERY_AUTO_REASONS, "recovery"),
    (BLOCKED_AUTO_REASONS, "blocked"),
    (CHARGING_AUTO_REASONS, "charging"),
    (WAITING_AUTO_REASONS, "waiting"),
)


def _reason_auto_state(base_reason: str) -> str | None:
    if base_reason == "init":
        return "idle"
    for reasons, state in AUTO_REASON_STATE_GROUPS:
        if base_reason in reasons:
            return state
    return None


def _derive_auto_state(
    reason: Any,
    *,
    relay_on: bool = False,
    learned_charge_power_state: Any = None,
) -> str:
    base_reason = _base_auto_reason(reason)
    learned_state = _normalized_learning_hint(learned_charge_power_state)
    state_from_reason = _reason_auto_state(base_reason)
    if state_from_reason is not None:
        return _reason_resolved_auto_state(state_from_reason, relay_on, learned_state)
    if relay_on:
        return _relay_on_auto_state(learned_state)
    return "idle"


_CHARGER_TRANSPORT_REASONS = frozenset({"busy", "ownership", "timeout", "offline", "response", "error"})


def _normalized_charger_transport_reason(reason: Any) -> str | None:
    if reason is None:
        return None
    normalized = str(reason).strip().lower()
    return normalized if normalized in _CHARGER_TRANSPORT_REASONS else None


def _charger_transport_health_reason(reason: Any) -> str | None:
    normalized = _normalized_charger_transport_reason(reason)
    return None if normalized is None else f"charger-transport-{normalized}"


def _positive_service_float(svc: Any, attr_name: str) -> float | None:
    raw_value = getattr(svc, attr_name, None)
    if raw_value is None:
        return None
    try:
        numeric_value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return numeric_value if numeric_value > 0.0 else None


def _charger_transport_max_age_seconds(svc: Any) -> float:
    candidates = [2.0]
    worker_poll_seconds = _positive_service_float(svc, "_worker_poll_interval_seconds")
    if worker_poll_seconds is not None:
        candidates.append(worker_poll_seconds * 2.0)
    live_publish_seconds = _positive_service_float(svc, "_dbus_live_publish_interval_seconds")
    if live_publish_seconds is not None:
        candidates.append(live_publish_seconds * 2.0)
    soft_fail_seconds = _positive_service_float(svc, "auto_shelly_soft_fail_seconds")
    if soft_fail_seconds is not None:
        candidates.append(soft_fail_seconds)
    return max(1.0, min(candidates))


def _charger_transport_now(svc: Any, now: float | int | None = None) -> float:
    if now is not None:
        return float(now)
    time_now = getattr(svc, "_time_now", None)
    if callable(time_now):
        raw_value = time_now()
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            return float(raw_value)
    return time.time()


def _fresh_charger_transport_timestamp(svc: Any, now: float | int | None = None) -> float | None:
    transport_at = finite_float_or_none(getattr(svc, "_last_charger_transport_at", None))
    if transport_at is None:
        return None
    current = float(now) if now is not None else _charger_transport_now(svc)
    if abs(current - transport_at) > _charger_transport_max_age_seconds(svc):
        return None
    return float(transport_at)


def _fresh_charger_transport_reason(svc: Any, now: float | int | None = None) -> str | None:
    if _fresh_charger_transport_timestamp(svc, now) is None:
        return None
    return _normalized_charger_transport_reason(getattr(svc, "_last_charger_transport_reason", None))


def _normalized_text_attr(svc: Any, attr_name: str) -> str | None:
    raw_value = getattr(svc, attr_name, None)
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    return normalized or None


def _fresh_charger_transport_source(svc: Any, now: float | int | None = None) -> str | None:
    if _fresh_charger_transport_timestamp(svc, now) is None:
        return None
    return _normalized_text_attr(svc, "_last_charger_transport_source")


def _fresh_charger_transport_detail(svc: Any, now: float | int | None = None) -> str | None:
    if _fresh_charger_transport_timestamp(svc, now) is None:
        return None
    return _normalized_text_attr(svc, "_last_charger_transport_detail")


def _charger_transport_retry_delay_seconds(svc: Any, reason: Any) -> float:
    normalized = _normalized_charger_transport_reason(reason)
    base_delay_seconds = _positive_service_float(svc, "auto_dbus_backoff_base_seconds") or 5.0
    soft_fail_seconds = _positive_service_float(svc, "auto_shelly_soft_fail_seconds") or 10.0
    retry_delay_by_reason: dict[str, float] = {
        "busy": max(1.0, min(base_delay_seconds, 5.0)),
        "ownership": max(3.0, base_delay_seconds * 2.0),
        "timeout": max(2.0, min(soft_fail_seconds, max(base_delay_seconds * 1.5, 2.0))),
        "offline": max(10.0, soft_fail_seconds, base_delay_seconds * 4.0),
        "response": max(3.0, base_delay_seconds * 2.0),
    }
    if normalized is None:
        return max(2.0, base_delay_seconds * 2.0)
    return retry_delay_by_reason.get(normalized, max(2.0, base_delay_seconds * 2.0))


def _fresh_charger_retry_until(svc: Any, now: float | int | None = None) -> float | None:
    retry_until = finite_float_or_none(getattr(svc, "_charger_retry_until", None))
    if retry_until is None:
        return None
    current = float(now) if now is not None else _charger_transport_now(svc)
    return retry_until if retry_until > current else None


def _fresh_charger_retry_reason(svc: Any, now: float | int | None = None) -> str | None:
    if _fresh_charger_retry_until(svc, now) is None:
        return None
    return _normalized_charger_transport_reason(getattr(svc, "_charger_retry_reason", None))


def _fresh_charger_retry_source(svc: Any, now: float | int | None = None) -> str | None:
    if _fresh_charger_retry_until(svc, now) is None:
        return None
    return _normalized_text_attr(svc, "_charger_retry_source")


def _charger_retry_remaining_seconds(svc: Any, now: float | int | None = None) -> int:
    retry_until = _fresh_charger_retry_until(svc, now)
    if retry_until is None:
        return -1
    current = float(now) if now is not None else _charger_transport_now(svc)
    return int(math.ceil(retry_until - current))


def _age_seconds(timestamp: float | int | None, now: float | int | None = None) -> int:
    if timestamp is None:
        return -1
    current = time.time() if now is None else float(now)
    return max(0, int(current - float(timestamp)))


def _confirmed_relay_state_max_age_seconds(svc: Any) -> float:
    fallback_candidates = [5.0]
    worker_poll_seconds = _positive_service_float(svc, "_worker_poll_interval_seconds")
    if worker_poll_seconds is not None:
        fallback_candidates.append(worker_poll_seconds * 2.0)
    relay_sync_timeout_seconds = _positive_service_float(svc, "relay_sync_timeout_seconds")
    if relay_sync_timeout_seconds is not None:
        fallback_candidates.append(relay_sync_timeout_seconds)
    return max(1.0, min(fallback_candidates))


def _confirmed_relay_sample(svc: Any) -> tuple[dict[str, Any] | None, Any]:
    pm_status = getattr(svc, "_last_confirmed_pm_status", None)
    captured_at = getattr(svc, "_last_confirmed_pm_status_at", None)
    if pm_status is None:
        pm_status, captured_at = _legacy_confirmed_relay_sample(svc)
    if not isinstance(pm_status, dict):
        return None, None
    return {str(key): value for key, value in pm_status.items()}, captured_at


def _legacy_confirmed_relay_sample(svc: Any) -> tuple[Any, Any]:
    if not bool(getattr(svc, "_last_pm_status_confirmed", False)):
        return None, None
    return getattr(svc, "_last_pm_status", None), getattr(svc, "_last_pm_status_at", None)


def _confirmed_relay_sample_valid(pm_status: dict[str, Any] | None, captured_at: Any) -> bool:
    return isinstance(pm_status, dict) and "output" in pm_status and captured_at is not None


def _confirmed_relay_sample_fresh(captured_at: float, current: float, max_age_seconds: float) -> bool:
    age_seconds = current - float(captured_at)
    return -1.0 <= age_seconds <= max_age_seconds


def _confirmed_relay_output_value(pm_status: dict[str, Any]) -> bool:
    return bool(pm_status.get("output"))


def _fresh_confirmed_relay_sample(svc: Any, current: float) -> tuple[dict[str, Any], float] | None:
    max_age_seconds = _confirmed_relay_state_max_age_seconds(svc)
    pm_status, captured_at = _confirmed_relay_sample(svc)
    if not _confirmed_relay_sample_valid(pm_status, captured_at):
        return None
    assert pm_status is not None
    assert captured_at is not None
    resolved_captured_at = float(captured_at)
    if not _confirmed_relay_sample_fresh(resolved_captured_at, current, max_age_seconds):
        return None
    return pm_status, resolved_captured_at


def _fresh_confirmed_relay_output(svc: Any, now: float | int | None = None) -> bool | None:
    current = time.time() if now is None else float(now)
    sample = _fresh_confirmed_relay_sample(svc, current)
    if sample is None:
        return None
    pm_status, _captured_at = sample
    return _confirmed_relay_output_value(pm_status)
