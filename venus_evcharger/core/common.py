# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared constants and helper functions for the Venus EV charger service."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from venus_evcharger.core import common_schedule as _common_schedule_module
from venus_evcharger.core.common_auto import (
    _age_seconds,
    _auto_state_code,
    _base_auto_reason,
    _charger_retry_remaining_seconds,
    _charger_transport_health_reason,
    _charger_transport_max_age_seconds,
    _charger_transport_now,
    _charger_transport_retry_delay_seconds,
    _confirmed_relay_output_value,
    _confirmed_relay_sample,
    _confirmed_relay_sample_fresh,
    _confirmed_relay_sample_valid,
    _confirmed_relay_state_max_age_seconds,
    _derive_auto_state,
    _evse_fault_reason,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_retry_until,
    _fresh_charger_transport_detail,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_charger_transport_timestamp,
    _fresh_confirmed_relay_output,
    _fresh_confirmed_relay_sample,
    _health_code,
    _normalize_auto_state,
    _normalized_charger_transport_reason,
    _normalized_learning_hint,
    _positive_service_float,
    _reason_auto_state,
)
from venus_evcharger.core.common_schedule import (
    _scheduled_target_day,
    month_in_ranges,
    month_window,
    normalize_hhmm_text,
    normalize_scheduled_enabled_days,
    parse_hhmm,
    scheduled_enabled_days_text,
    scheduled_night_window_active,
)
from venus_evcharger.core.common_types import (
    AUTO_STATE_CODES,
    BLOCKED_AUTO_REASONS,
    CHARGING_AUTO_REASONS,
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    EVSE_FAULT_HEALTH_REASONS,
    HEALTH_CODES,
    MonthRange,
    PhaseMeasurements,
    RECOVERY_AUTO_REASONS,
    SCHEDULED_REASON_CODES,
    SCHEDULED_STATE_CODES,
    ScheduledModeSnapshot,
    TimeWindow,
    WAITING_AUTO_REASONS,
    WEEKDAY_LABELS,
    WEEKDAY_TOKEN_MAP,
    WeekdaySelection,
    _a,
    _kwh,
    _status_label,
    _v,
    _w,
)
from venus_evcharger.core.common_values import (
    mode_uses_auto_logic,
    mode_uses_scheduled_logic,
    normalize_mode,
    normalize_phase,
    phase_values,
    read_version,
)

auto_state_code = _auto_state_code
confirmed_relay_state_max_age_seconds = _confirmed_relay_state_max_age_seconds
derive_auto_state = _derive_auto_state
evse_fault_reason = _evse_fault_reason
fresh_confirmed_relay_output = _fresh_confirmed_relay_output


def local_datetime_from_timestamp(timestamp: float, timezone_name: object = "UTC") -> datetime:
    """Return the configured local wall-clock time for schedule decisions."""
    zone_name = str(timezone_name or "UTC").strip() or "UTC"
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        try:
            zone = ZoneInfo("UTC")
        except ZoneInfoNotFoundError:
            return datetime.fromtimestamp(float(timestamp), timezone.utc).replace(tzinfo=None)
    return datetime.fromtimestamp(float(timestamp), zone).replace(tzinfo=None)


def scheduled_mode_snapshot(
    when: datetime,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
    enabled_days: object,
    delay_seconds: float = 3600.0,
    latest_end_time: object = "06:30",
    target_day_func: Callable[
        [datetime, dict[int, tuple[TimeWindow, TimeWindow]] | None],
        date,
    ] = _scheduled_target_day,
) -> ScheduledModeSnapshot:
    """Delegate through the wrapper so tests may patch local schedule helpers."""
    return _common_schedule_module.scheduled_mode_snapshot(
        when,
        month_windows,
        enabled_days,
        delay_seconds=delay_seconds,
        latest_end_time=latest_end_time,
        target_day_func=target_day_func,
    )


__all__ = [
    "AUTO_STATE_CODES",
    "BLOCKED_AUTO_REASONS",
    "CHARGING_AUTO_REASONS",
    "DEFAULT_SCHEDULED_ENABLED_DAYS",
    "EVSE_FAULT_HEALTH_REASONS",
    "HEALTH_CODES",
    "MonthRange",
    "PhaseMeasurements",
    "RECOVERY_AUTO_REASONS",
    "SCHEDULED_REASON_CODES",
    "SCHEDULED_STATE_CODES",
    "ScheduledModeSnapshot",
    "TimeWindow",
    "WAITING_AUTO_REASONS",
    "WEEKDAY_LABELS",
    "WEEKDAY_TOKEN_MAP",
    "WeekdaySelection",
    "_a",
    "_age_seconds",
    "_auto_state_code",
    "_base_auto_reason",
    "_charger_retry_remaining_seconds",
    "_charger_transport_health_reason",
    "_charger_transport_max_age_seconds",
    "_charger_transport_now",
    "_charger_transport_retry_delay_seconds",
    "_confirmed_relay_output_value",
    "_confirmed_relay_sample",
    "_confirmed_relay_sample_fresh",
    "_confirmed_relay_sample_valid",
    "_confirmed_relay_state_max_age_seconds",
    "_derive_auto_state",
    "_evse_fault_reason",
    "_fresh_charger_retry_reason",
    "_fresh_charger_retry_source",
    "_fresh_charger_retry_until",
    "_fresh_charger_transport_detail",
    "_fresh_charger_transport_reason",
    "_fresh_charger_transport_source",
    "_fresh_charger_transport_timestamp",
    "_fresh_confirmed_relay_output",
    "_fresh_confirmed_relay_sample",
    "_health_code",
    "_kwh",
    "_normalize_auto_state",
    "_normalized_charger_transport_reason",
    "_normalized_learning_hint",
    "_positive_service_float",
    "_reason_auto_state",
    "_scheduled_target_day",
    "_status_label",
    "_v",
    "_w",
    "auto_state_code",
    "confirmed_relay_state_max_age_seconds",
    "derive_auto_state",
    "evse_fault_reason",
    "fresh_confirmed_relay_output",
    "mode_uses_auto_logic",
    "local_datetime_from_timestamp",
    "mode_uses_scheduled_logic",
    "month_in_ranges",
    "month_window",
    "normalize_hhmm_text",
    "normalize_mode",
    "normalize_phase",
    "normalize_scheduled_enabled_days",
    "parse_hhmm",
    "phase_values",
    "read_version",
    "scheduled_enabled_days_text",
    "scheduled_mode_snapshot",
    "scheduled_night_window_active",
]
