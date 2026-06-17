# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled-mode weekday and time-window helpers."""

from __future__ import annotations

from configparser import ConfigParser
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from venus_evcharger.core.common_types import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    MonthRange,
    SCHEDULED_REASON_CODES,
    SCHEDULED_STATE_CODES,
    ScheduledModeSnapshot,
    TimeWindow,
    WEEKDAY_LABELS,
    WEEKDAY_TOKEN_MAP,
    WeekdaySelection,
)


def _window_minutes_for_date(
    window_date: date,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
) -> tuple[int, int]:
    windows = month_windows or {}
    start_window, end_window = windows.get(window_date.month, ((8, 0), (18, 0)))
    start_hour, start_minute = start_window
    end_hour, end_minute = end_window
    return (start_hour * 60) + start_minute, (end_hour * 60) + end_minute


def _datetime_for_minutes(window_date: date, minute_of_day: int) -> datetime:
    hour, minute = divmod(int(minute_of_day), 60)
    return datetime(window_date.year, window_date.month, window_date.day, hour, minute)


def _weekday_text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalized_weekday_candidates(value: Any) -> list[str]:
    raw_text = _weekday_text_or_empty(value)
    if not raw_text:
        return []
    separator_map = str.maketrans({separator: "," for separator in ";|/\\ "})
    return [token.strip() for token in raw_text.lower().translate(separator_map).split(",") if token.strip()]


_SPECIAL_SCHEDULED_DAY_SELECTIONS: dict[str, WeekdaySelection] = {
    "all": tuple(range(7)),
    "daily": tuple(range(7)),
    "everyday": tuple(range(7)),
    "*": tuple(range(7)),
    "weekdays": DEFAULT_SCHEDULED_ENABLED_DAYS,
    "weekday": DEFAULT_SCHEDULED_ENABLED_DAYS,
    "workdays": DEFAULT_SCHEDULED_ENABLED_DAYS,
    "workday": DEFAULT_SCHEDULED_ENABLED_DAYS,
    "mon-fri": DEFAULT_SCHEDULED_ENABLED_DAYS,
    "mo-fr": DEFAULT_SCHEDULED_ENABLED_DAYS,
    "weekend": (5, 6),
    "weekends": (5, 6),
    "sat-sun": (5, 6),
    "sa-su": (5, 6),
}


def _normalized_weekday_tokens(value: Any) -> list[str]:
    if isinstance(value, (tuple, list, set, frozenset)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return _normalized_weekday_candidates(value)


def _special_scheduled_day_selection(tokens: list[str]) -> WeekdaySelection | None:
    if len(tokens) != 1:
        return None
    return _SPECIAL_SCHEDULED_DAY_SELECTIONS.get(tokens[0])


def _append_unique_weekday(target: list[int], weekday: int) -> None:
    if weekday not in target:
        target.append(weekday)


def _weekday_range_bounds(token: str) -> tuple[int, int] | None:
    if "-" not in token:
        return None
    start_token, end_token = [part.strip() for part in token.split("-", 1)]
    start_day = WEEKDAY_TOKEN_MAP.get(start_token)
    end_day = WEEKDAY_TOKEN_MAP.get(end_token)
    if start_day is None or end_day is None:
        return None
    return start_day, end_day


def _weekday_range_values(start_day: int, end_day: int) -> list[int]:
    current = start_day
    weekdays: list[int] = []
    while current != end_day:
        weekdays.append(current)
        current = (current + 1) % 7
    weekdays.append(end_day)
    return weekdays


def _extend_weekday_range(target: list[int], token: str) -> None:
    range_bounds = _weekday_range_bounds(token)
    if range_bounds is None:
        return
    for weekday in _weekday_range_values(*range_bounds):
        _append_unique_weekday(target, weekday)


def _weekday_indices_for_token(token: str) -> list[int]:
    weekday = WEEKDAY_TOKEN_MAP.get(token)
    if weekday is not None:
        return [weekday]
    normalized: list[int] = []
    _extend_weekday_range(normalized, token)
    return normalized


def _extend_normalized_weekday_selection(normalized: list[int], token: str) -> None:
    for weekday in _weekday_indices_for_token(token):
        _append_unique_weekday(normalized, weekday)


def _normalized_weekday_selection(tokens: list[str], fallback: WeekdaySelection) -> WeekdaySelection:
    normalized: list[int] = []
    for token in tokens:
        _extend_normalized_weekday_selection(normalized, token)
    return tuple(normalized) if normalized else tuple(fallback)


def normalize_scheduled_enabled_days(
    value: Any,
    fallback: WeekdaySelection = DEFAULT_SCHEDULED_ENABLED_DAYS,
) -> WeekdaySelection:
    tokens = _normalized_weekday_tokens(value)
    if not tokens:
        return tuple(fallback)
    special_selection = _special_scheduled_day_selection(tokens)
    if special_selection is not None:
        return special_selection
    return _normalized_weekday_selection(tokens, fallback)


def scheduled_enabled_days_text(
    value: Any,
    fallback: WeekdaySelection = DEFAULT_SCHEDULED_ENABLED_DAYS,
) -> str:
    return ",".join(WEEKDAY_LABELS[index] for index in normalize_scheduled_enabled_days(value, fallback))


def parse_hhmm(value: Any, fallback: TimeWindow) -> TimeWindow:
    try:
        hour_text, minute_text = str(value).strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (AttributeError, TypeError, ValueError):
        pass
    return fallback


def normalize_hhmm_text(value: Any, fallback: str = "06:30") -> str:
    fallback_window = parse_hhmm(fallback, (6, 30))
    hour, minute = parse_hhmm(value, fallback_window)
    return f"{hour:02d}:{minute:02d}"


def _scheduled_target_day(when: datetime, month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None) -> date:
    current_minutes = when.hour * 60 + when.minute
    today = when.date()
    start_minutes, end_minutes = _window_minutes_for_date(today, month_windows)
    if start_minutes < end_minutes and current_minutes >= end_minutes:
        return today + timedelta(days=1)
    return today


@dataclass(frozen=True)
class _ScheduledSnapshotContext:
    target_date: date
    target_day_index: int
    target_enabled: bool
    start_minutes: int
    end_minutes: int
    fallback_start: datetime
    night_boost_end: datetime


def _scheduled_snapshot_context(
    when: datetime,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
    enabled_days: Any,
    delay_seconds: float,
    latest_end_time: Any,
    target_day_func: Callable[[datetime, dict[int, tuple[TimeWindow, TimeWindow]] | None], date] = _scheduled_target_day,
) -> _ScheduledSnapshotContext:
    target_date = target_day_func(when, month_windows)
    target_day_index = int(target_date.weekday())
    enabled_tuple = normalize_scheduled_enabled_days(enabled_days)
    start_minutes, end_minutes = _window_minutes_for_date(target_date, month_windows)
    previous_day = target_date - timedelta(days=1)
    _previous_start_minutes, previous_end_minutes = _window_minutes_for_date(previous_day, month_windows)
    fallback_start = _datetime_for_minutes(previous_day, previous_end_minutes) + timedelta(
        seconds=max(0.0, float(delay_seconds))
    )
    latest_end_hour, latest_end_minute = parse_hhmm(latest_end_time, (6, 30))
    latest_end_dt = datetime(target_date.year, target_date.month, target_date.day, latest_end_hour, latest_end_minute)
    daytime_start = _datetime_for_minutes(target_date, start_minutes)
    return _ScheduledSnapshotContext(
        target_date=target_date,
        target_day_index=target_day_index,
        target_enabled=target_day_index in enabled_tuple,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        fallback_start=fallback_start,
        night_boost_end=min(latest_end_dt, daytime_start),
    )


def _scheduled_snapshot_text_fields(context: _ScheduledSnapshotContext) -> tuple[str, str, str]:
    return (
        WEEKDAY_LABELS[context.target_day_index],
        context.target_date.isoformat(),
        context.fallback_start.strftime("%Y-%m-%d %H:%M"),
    )


def _scheduled_snapshot(
    context: _ScheduledSnapshotContext,
    state: str,
    reason: str,
    *,
    target_day_enabled: bool,
) -> ScheduledModeSnapshot:
    day_label, target_date_text, fallback_start_text = _scheduled_snapshot_text_fields(context)
    return ScheduledModeSnapshot(
        state=state,
        state_code=SCHEDULED_STATE_CODES[state],
        reason=reason,
        reason_code=SCHEDULED_REASON_CODES[reason],
        night_boost_active=(state == "night-boost"),
        target_day_index=context.target_day_index,
        target_day_label=day_label,
        target_date_text=target_date_text,
        target_day_enabled=target_day_enabled,
        fallback_start_text=fallback_start_text,
        boost_until_text=context.night_boost_end.strftime("%Y-%m-%d %H:%M"),
    )


def _scheduled_daytime_window_active(when: datetime, context: _ScheduledSnapshotContext) -> bool:
    current_minutes = when.hour * 60 + when.minute
    return (
        context.start_minutes < context.end_minutes
        and context.start_minutes <= current_minutes < context.end_minutes
        and when.date() == context.target_date
    )


def _scheduled_post_night_state(when: datetime, context: _ScheduledSnapshotContext) -> tuple[str, str]:
    current_minutes = when.hour * 60 + when.minute
    checks: tuple[tuple[bool, tuple[str, str]], ...] = (
        (when < context.fallback_start, ("waiting-fallback", "waiting-fallback-delay")),
        (when < context.night_boost_end, ("night-boost", "night-boost-window")),
        (
            when.date() == context.target_date
            and context.start_minutes < context.end_minutes
            and current_minutes >= context.start_minutes,
            ("auto-window", "daytime-auto"),
        ),
    )
    for matched, result in checks:
        if matched:
            return result
    return "after-latest-end", "latest-end-reached"


def scheduled_mode_snapshot(
    when: datetime,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
    enabled_days: Any,
    delay_seconds: float = 3600.0,
    latest_end_time: Any = "06:30",
    target_day_func: Callable[[datetime, dict[int, tuple[TimeWindow, TimeWindow]] | None], date] = _scheduled_target_day,
) -> ScheduledModeSnapshot:
    context = _scheduled_snapshot_context(
        when,
        month_windows,
        enabled_days,
        delay_seconds,
        latest_end_time,
        target_day_func,
    )
    if _scheduled_daytime_window_active(when, context):
        return _scheduled_snapshot(context, "auto-window", "daytime-auto", target_day_enabled=context.target_enabled)
    if not context.target_enabled:
        return _scheduled_snapshot(context, "inactive-day", "target-day-disabled", target_day_enabled=False)
    state, reason = _scheduled_post_night_state(when, context)
    return _scheduled_snapshot(context, state, reason, target_day_enabled=True)


def scheduled_night_window_active(
    when: datetime,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
    delay_seconds: float = 3600.0,
) -> bool:
    return scheduled_mode_snapshot(
        when,
        month_windows,
        DEFAULT_SCHEDULED_ENABLED_DAYS,
        delay_seconds=delay_seconds,
        latest_end_time="23:59",
    ).night_boost_active


def _month_in_range(month: int, start_month: int, end_month: int) -> bool:
    if start_month <= end_month:
        return start_month <= month <= end_month
    return month >= start_month or month <= end_month


def month_in_ranges(month: int, ranges: Iterable[MonthRange]) -> bool:
    return any(_month_in_range(month, start_month, end_month) for start_month, end_month in ranges)


def month_window(
    config: ConfigParser,
    month: int,
    default_start: str,
    default_end: str,
) -> tuple[TimeWindow, TimeWindow]:
    month_name = datetime(2000, month, 1).strftime("%b")
    start = parse_hhmm(config["DEFAULT"].get(f"Auto{month_name}Start", default_start), parse_hhmm(default_start, (8, 0)))
    end = parse_hhmm(config["DEFAULT"].get(f"Auto{month_name}End", default_end), parse_hhmm(default_end, (18, 0)))
    return start, end
