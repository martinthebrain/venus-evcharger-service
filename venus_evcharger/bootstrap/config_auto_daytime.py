# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto daytime and scheduled-window config loading."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value, _seasonal_month_windows
from venus_evcharger.core.common import (
    normalize_hhmm_text,
    scheduled_enabled_days_text,
)


MonthWindowFunc = Callable[[configparser.ConfigParser, int, str, str], Any]

AUTO_DAYTIME_ONLY_KEY = "AutoDaytimeOnly"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_SCHEDULE_TIMEZONE_KEY = "AutoScheduleTimezone"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_SCHEDULED_NIGHT_START_DELAY_KEY = "AutoScheduledNightStartDelaySeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_SCHEDULED_ENABLED_DAYS_KEY = "AutoScheduledEnabledDays"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_SCHEDULED_LATEST_END_KEY = "AutoScheduledLatestEndTime"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_SCHEDULED_NIGHT_CURRENT_KEY = "AutoScheduledNightCurrentAmps"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_NIGHT_LOCK_STOP_KEY = "AutoNightLockStop"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
DEFAULT_TRUE = "1"  # pragma: no mutate - common true token contract is tested separately.
DEFAULT_FALSE = "0"  # pragma: no mutate - any non-true token normalizes to False.
DEFAULT_SCHEDULE_TIMEZONE = "UTC"  # pragma: no mutate - blank values normalize back to UTC.
DEFAULT_LATEST_END_TIME = "04:30"  # pragma: no mutate - normalized by normalize_hhmm_text.


def load_auto_daytime_policy(
    svc: Any,
    defaults: configparser.SectionProxy,
    month_window: MonthWindowFunc,
) -> None:
    """Load seasonal day-window behavior plus Scheduled-mode night settings."""
    svc.auto_daytime_only = defaults.get(AUTO_DAYTIME_ONLY_KEY, DEFAULT_TRUE).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    svc.auto_month_windows = _seasonal_month_windows(svc.config, month_window)
    svc.auto_schedule_timezone = defaults.get(AUTO_SCHEDULE_TIMEZONE_KEY, DEFAULT_SCHEDULE_TIMEZONE).strip() or "UTC"
    svc.auto_scheduled_night_start_delay_seconds = float(
        _config_value(defaults, AUTO_SCHEDULED_NIGHT_START_DELAY_KEY, 3600)
    )
    svc.auto_scheduled_enabled_days = scheduled_enabled_days_text(defaults.get(AUTO_SCHEDULED_ENABLED_DAYS_KEY))
    svc.auto_scheduled_latest_end_time = normalize_hhmm_text(
        defaults.get(AUTO_SCHEDULED_LATEST_END_KEY),
        DEFAULT_LATEST_END_TIME,
    )
    svc.auto_scheduled_night_current_amps = float(_config_value(defaults, AUTO_SCHEDULED_NIGHT_CURRENT_KEY, 0))
    svc.auto_night_lock_stop = defaults.get(AUTO_NIGHT_LOCK_STOP_KEY, DEFAULT_FALSE).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
