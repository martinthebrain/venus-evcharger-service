# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for scheduled, software-update, and outward metric surfaces."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts_basic import (
    finite_float_or_none,
    normalize_binary_flag,
    normalize_learning_state,
    non_negative_float_or_none,
    thresholds_ordered,
    timestamp_not_future,
    valid_battery_soc,
)
from venus_evcharger.core.common_types import SCHEDULED_REASON_CODES, SCHEDULED_STATE_CODES
SOFTWARE_UPDATE_STATE_CODES = {
    "idle": 0,
    "checking": 1,
    "up-to-date": 2,
    "available": 3,
    "available-blocked": 4,
    "running": 5,
    "installed": 6,
    "check-failed": 7,
    "install-failed": 8,
    "update-unavailable": 9,
}
AUTO_METRIC_NUMERIC_FIELDS = (
    "surplus",
    "grid",
    "start_threshold",
    "stop_threshold",
    "threshold_scale",
    "stop_alpha",
    "surplus_volatility",
    "battery_unadjusted_surplus_penalty_w",
    "ev_priority_active",
    "ev_priority_credit_w",
    "ev_priority_available_surplus_w",
    "ev_priority_reclaimable_charge_w",
    "ev_priority_running_load_w",
    "ev_priority_soc",
    "ev_priority_release_soc",
)
AUTO_METRIC_TEXT_FIELDS = ("profile", "threshold_mode", "stop_alpha_stage")

__all__ = [
    "AUTO_METRIC_NUMERIC_FIELDS",
    "AUTO_METRIC_TEXT_FIELDS",
    "SCHEDULED_REASON_CODES",
    "SCHEDULED_STATE_CODES",
    "SOFTWARE_UPDATE_STATE_CODES",
    "normalized_scheduled_state_values",
    "normalized_scheduled_state_fields",
    "normalized_software_update_state_fields",
]


def _normalized_scheduled_label(value: Any, allowed_codes: dict[str, int]) -> str:
    if value is None:
        return "disabled"
    normalized = str(value).strip().lower()
    return normalized if normalized in allowed_codes else "disabled"


def _normalized_scheduled_boost_active(night_boost_active: Any, normalized_state: str) -> int:
    return int(bool(night_boost_active) and normalized_state == "night-boost")


def normalized_scheduled_state_values(
    scheduled_active: Any,
    state: Any,
    reason: Any,
    night_boost_active: Any,
) -> tuple[str, int, str, int, int]:
    """Return normalized scheduled-mode values from the semantic fields only."""
    if not bool(scheduled_active):
        return "disabled", 0, "disabled", 0, 0
    normalized_state = _normalized_scheduled_label(state, SCHEDULED_STATE_CODES)
    normalized_reason = _normalized_scheduled_label(reason, SCHEDULED_REASON_CODES)
    return (
        normalized_state,
        SCHEDULED_STATE_CODES[normalized_state],
        normalized_reason,
        SCHEDULED_REASON_CODES[normalized_reason],
        _normalized_scheduled_boost_active(night_boost_active, normalized_state),
    )


def normalized_scheduled_state_fields(
    scheduled_active: Any,
    state: Any,
    state_code: Any,
    reason: Any,
    reason_code: Any,
    night_boost_active: Any,
) -> tuple[str, int, str, int, int]:
    """Return normalized scheduled-mode values.

    ``state_code`` and ``reason_code`` are accepted for the historical outward
    contract shape. Codes are derived from normalized labels so stale supplied
    codes cannot leak to DBus.
    """
    return normalized_scheduled_state_values(scheduled_active, state, reason, night_boost_active)


def _normalized_software_update_state_label(value: Any) -> str:
    if value is None:
        return "idle"
    normalized = str(value).strip().lower()
    return normalized if normalized in SOFTWARE_UPDATE_STATE_CODES else "idle"


def _software_update_should_report_blocked(
    normalized_state: str,
    normalized_available: int,
    normalized_no_update_active: int,
) -> bool:
    return bool(normalized_available and normalized_no_update_active and normalized_state == "available")


def _software_update_should_clear_blocked(normalized_state: str, normalized_no_update_active: int) -> bool:
    return normalized_state == "available-blocked" and not normalized_no_update_active


def _software_update_unblocked_state(normalized_available: int) -> str:
    return "available" if normalized_available else "up-to-date"


def _resolved_software_update_available_state(
    normalized_state: str,
    normalized_available: int,
    normalized_no_update_active: int,
) -> str:
    if _software_update_should_report_blocked(normalized_state, normalized_available, normalized_no_update_active):
        return "available-blocked"
    if _software_update_should_clear_blocked(normalized_state, normalized_no_update_active):
        return _software_update_unblocked_state(normalized_available)
    return normalized_state


def normalized_software_update_state_fields(
    state: Any,
    available: Any,
    no_update_active: Any,
) -> tuple[str, int, int, int]:
    normalized_available = normalize_binary_flag(available)
    normalized_no_update_active = normalize_binary_flag(no_update_active)
    normalized_state = _resolved_software_update_available_state(
        _normalized_software_update_state_label(state),
        normalized_available,
        normalized_no_update_active,
    )
    return (
        normalized_state,
        SOFTWARE_UPDATE_STATE_CODES[normalized_state],
        normalized_available,
        normalized_no_update_active,
    )


def _displayable_timestamp_candidates(
    last_confirmed_at: Any,
    last_pm_at: Any,
    last_pm_confirmed: Any,
) -> tuple[float | None, float | None]:
    confirmed_timestamp = finite_float_or_none(last_confirmed_at)
    fallback_timestamp = finite_float_or_none(last_pm_at) if bool(last_pm_confirmed) else None
    return confirmed_timestamp, fallback_timestamp


def _timestamp_displayable(candidate: float | None, current: float | None, future_tolerance_seconds: float) -> bool:
    return candidate is not None and (
        current is None or timestamp_not_future(candidate, current, future_tolerance_seconds)
    )


def displayable_confirmed_read_timestamp(
    *,
    last_confirmed_at: Any,
    last_pm_at: Any,
    last_pm_confirmed: Any,
    now: float | int | None = None,
    future_tolerance_seconds: float = 1.0,
) -> float | None:
    current = None if now is None else float(now)
    for candidate in _displayable_timestamp_candidates(last_confirmed_at, last_pm_at, last_pm_confirmed):
        if _timestamp_displayable(candidate, current, future_tolerance_seconds):
            return candidate
    return None


def _normalized_metric_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _sanitize_numeric_auto_metrics(sanitized: dict[str, Any]) -> None:
    for field in AUTO_METRIC_NUMERIC_FIELDS:
        sanitized[field] = finite_float_or_none(sanitized.get(field))


def _sanitize_text_auto_metrics(sanitized: dict[str, Any]) -> None:
    for field in AUTO_METRIC_TEXT_FIELDS:
        sanitized[field] = _normalized_metric_text(sanitized.get(field))


def _sanitize_soc_metric(sanitized: dict[str, Any]) -> None:
    soc_value = finite_float_or_none(sanitized.get("soc"))
    sanitized["soc"] = soc_value if valid_battery_soc(soc_value) else None


def _sanitize_learning_metrics(sanitized: dict[str, Any]) -> None:
    sanitized["learned_charge_power"] = non_negative_float_or_none(sanitized.get("learned_charge_power"))
    learned_state = sanitized.get("learned_charge_power_state")
    sanitized["learned_charge_power_state"] = (
        normalize_learning_state(learned_state) if learned_state is not None else None
    )


def _sanitize_threshold_metrics(sanitized: dict[str, Any]) -> None:
    if thresholds_ordered(sanitized.get("start_threshold"), sanitized.get("stop_threshold")):
        return
    sanitized["start_threshold"] = None
    sanitized["stop_threshold"] = None


def sanitized_auto_metrics(metrics: Any) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    sanitized: dict[str, Any] = dict(metrics)
    _sanitize_numeric_auto_metrics(sanitized)
    _sanitize_learning_metrics(sanitized)
    _sanitize_soc_metric(sanitized)
    _sanitize_text_auto_metrics(sanitized)
    _sanitize_threshold_metrics(sanitized)
    return sanitized
