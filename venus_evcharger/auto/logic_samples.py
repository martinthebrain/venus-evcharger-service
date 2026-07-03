# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Venus EV charger service.

The Auto controller keeps the policy readable by splitting the decision tree
into many small helper methods. The high-level behavior is:
- gather fresh PV, grid, and battery inputs
- smooth the relevant values
- check hard safety gates first
- evaluate start/stop conditions
- return the desired relay state plus a diagnostic health reason
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Deque

from .logic_learning import _AutoDecisionLearning
from venus_evcharger.core.common import (
    auto_state_code as _auto_state_code,
    derive_auto_state as _derive_auto_state,
    fresh_confirmed_relay_output as _fresh_confirmed_relay_output,
    local_datetime_from_timestamp as _local_datetime_from_timestamp,
    mode_uses_scheduled_logic as _mode_uses_scheduled_logic,
    scheduled_mode_snapshot as _scheduled_mode_snapshot,
)
from venus_evcharger.core.contracts import normalized_auto_decision_trace

AutoSample = tuple[float, float, float]
AutoDecision = bool | object
MonthWindow = tuple[tuple[int, int], tuple[int, int]]
DEFAULT_DAYTIME_WINDOW: MonthWindow = ((8, 0), (18, 0))
DEFAULT_SCHEDULE_TIMEZONE = "UTC"
DEFAULT_SCHEDULE_ENABLED_DAYS = "Mon,Tue,Wed,Thu,Fri"
DEFAULT_SCHEDULE_NIGHT_START_DELAY_SECONDS = 3600.0
DEFAULT_SCHEDULE_LATEST_END_TIME = "04:30"
MINUTES_PER_DAY = 24 * 60



class _AutoDecisionSamples(_AutoDecisionLearning):
    @staticmethod
    def get_available_surplus_watts(pv_power: float | int, grid_power: float | int) -> float:
        """Compute PV-backed export as available charging surplus."""
        pv_power = max(0.0, float(pv_power))
        export_power = max(0.0, -float(grid_power))
        return min(pv_power, export_power)

    def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> None:
        """Append a sample for averaging and prune old data."""
        samples: Deque[AutoSample] = self.service.auto_samples
        samples.append((now, float(surplus_power), float(grid_power)))
        cutoff = now - self.service.auto_average_window_seconds
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def clear_auto_samples(self) -> None:
        """Clear all auto averaging samples."""
        self.service.auto_samples.clear()
        self.service._stop_smoothed_surplus_power = None
        self.service._stop_smoothed_grid_power = None

    def average_auto_metric(self, index: int) -> float | None:
        """Compute the mean of one field from the sample buffer."""
        samples: Deque[AutoSample] = self.service.auto_samples
        if not samples:
            return None
        return sum(sample[index] for sample in samples) / len(samples)

    @staticmethod
    def _smooth_metric(previous: float | None, current: float, alpha: float) -> float:
        """Apply EWMA smoothing, falling back to the current value on first sample."""
        if previous is None:
            return float(current)
        return float(previous) + (float(alpha) * (float(current) - float(previous)))

    def _stop_surplus_volatility(self) -> float | None:
        """Return the population standard deviation of recent raw surplus samples."""
        samples: Deque[AutoSample] = self.service.auto_samples
        if len(samples) < 2:
            return None
        surplus_values = [float(sample[1]) for sample in samples]
        mean_value = sum(surplus_values) / len(surplus_values)
        variance = sum((value - mean_value) ** 2 for value in surplus_values) / len(surplus_values)
        return math.sqrt(variance)

    def _adaptive_stop_alpha(self) -> tuple[float, str, float | None]:
        """Return an adaptive EWMA alpha based on recent surplus volatility."""
        volatility = self._stop_surplus_volatility()
        return self._auto_policy().ewma.adaptive_alpha(volatility)

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        """Record the last relay state change for minimum on/off logic."""
        changed_at = time.time() if now is None else float(now)
        self.service.relay_last_changed_at = changed_at
        if not relay_on:
            self.service.relay_last_off_at = changed_at

    def is_within_auto_daytime_window(self, current_dt: datetime | None = None) -> bool:
        """Return True if current time is inside the seasonal day window."""
        if not self._auto_daytime_only_enabled():
            return True

        current_dt = datetime.now() if current_dt is None else current_dt
        current_minutes = current_dt.hour * 60 + current_dt.minute
        start_minutes, end_minutes = self._daytime_window_minutes_for_month(current_dt.month)
        return self._minutes_within_daytime_window(current_minutes, start_minutes, end_minutes)

    def _scheduled_night_charge_active(self, now: float | None = None) -> bool:
        """Return whether scheduled/plan mode should force nighttime charging."""
        if not _mode_uses_scheduled_logic(self._service_virtual_mode()):
            return False
        current_time = self._learning_policy_now() if now is None else float(now)
        return _scheduled_mode_snapshot(
            _local_datetime_from_timestamp(current_time, self._schedule_timezone()),
            self._auto_month_windows(),
            self._scheduled_enabled_days(),
            delay_seconds=self._scheduled_night_start_delay_seconds(),
            latest_end_time=self._scheduled_latest_end_time(),
        ).night_boost_active

    def _daytime_window_minutes_for_month(self, month: int) -> tuple[int, int]:
        """Return configured daytime start/end minutes for one month."""
        start_window, end_window = self._auto_month_windows().get(month, DEFAULT_DAYTIME_WINDOW)
        start_hour, start_minute = start_window
        end_hour, end_minute = end_window
        return start_hour * 60 + start_minute, end_hour * 60 + end_minute

    def _auto_daytime_only_enabled(self) -> bool:
        """Return True when Auto decisions must respect the daylight window."""
        if not hasattr(self.service, "auto_daytime_only"):
            return False
        return bool(self.service.auto_daytime_only)

    def _service_virtual_mode(self) -> int:
        """Return the service mode used for scheduled-mode decisions."""
        if not hasattr(self.service, "virtual_mode"):
            return 0
        mode = self.service.virtual_mode
        return 0 if mode is None else int(mode)

    def _schedule_timezone(self) -> str:
        """Return the configured schedule timezone or its documented default."""
        if not hasattr(self.service, "auto_schedule_timezone"):
            return DEFAULT_SCHEDULE_TIMEZONE
        timezone_name = self.service.auto_schedule_timezone
        return DEFAULT_SCHEDULE_TIMEZONE if timezone_name is None else str(timezone_name)

    def _auto_month_windows(self) -> dict[int, MonthWindow]:
        """Return the configured monthly daylight windows."""
        if not hasattr(self.service, "auto_month_windows"):
            return {}
        month_windows = self.service.auto_month_windows
        return {} if month_windows is None else dict(month_windows)

    def _scheduled_enabled_days(self) -> str:
        """Return the scheduled-mode enabled-day expression."""
        if not hasattr(self.service, "auto_scheduled_enabled_days"):
            return DEFAULT_SCHEDULE_ENABLED_DAYS
        days = self.service.auto_scheduled_enabled_days
        return DEFAULT_SCHEDULE_ENABLED_DAYS if days is None else str(days)

    def _scheduled_night_start_delay_seconds(self) -> float:
        """Return the scheduled nighttime start delay."""
        if not hasattr(self.service, "auto_scheduled_night_start_delay_seconds"):
            return DEFAULT_SCHEDULE_NIGHT_START_DELAY_SECONDS
        delay_seconds = self.service.auto_scheduled_night_start_delay_seconds
        return DEFAULT_SCHEDULE_NIGHT_START_DELAY_SECONDS if delay_seconds is None else float(delay_seconds)

    def _scheduled_latest_end_time(self) -> str:
        """Return the latest scheduled nighttime end time."""
        if not hasattr(self.service, "auto_scheduled_latest_end_time"):
            return DEFAULT_SCHEDULE_LATEST_END_TIME
        latest_end_time = self.service.auto_scheduled_latest_end_time
        return DEFAULT_SCHEDULE_LATEST_END_TIME if latest_end_time is None else str(latest_end_time)

    @staticmethod
    def _minutes_within_daytime_window(current_minutes: int, start_minutes: int, end_minutes: int) -> bool:
        """Return True when current minutes fall inside one daytime window."""
        window_length = (end_minutes - start_minutes) % MINUTES_PER_DAY
        if window_length == 0:
            return True
        elapsed_since_start = (current_minutes - start_minutes) % MINUTES_PER_DAY
        return elapsed_since_start < window_length

    def _apply_decision_trace_postconditions(
        self,
        reason: str,
        cached: bool,
        relay_intent: bool,
    ) -> None:
        """Normalize outward decision-trace state after one Auto decision settled."""
        svc = self.service
        trace = normalized_auto_decision_trace(
            health_reason=reason,
            cached_inputs=cached,
            relay_intent=relay_intent,
            learned_charge_power_state=getattr(svc, "learned_charge_power_state", "unknown"),
            metrics=self._last_auto_metrics_source(),
            health_code_func=self._health_code,
            derive_auto_state_func=_derive_auto_state,
        )
        svc._last_health_reason = trace["health_reason"]
        svc._last_health_code = trace["health_code"]
        svc._last_auto_state = trace["state"]
        svc._last_auto_state_code = trace["state_code"]
        if isinstance(getattr(svc, "_last_auto_metrics", None), dict):
            svc._last_auto_metrics.clear()
            svc._last_auto_metrics.update(trace["metrics"])
            return
        svc._last_auto_metrics = trace["metrics"]

    def set_health(self, reason: str, cached: bool = False, relay_intent: bool | None = None) -> None:
        """Store health reason and numeric code, optionally marking cached inputs."""
        base_reason = reason
        effective_relay_intent = self._observed_relay_state() if relay_intent is None else bool(relay_intent)
        self._apply_decision_trace_postconditions(base_reason, cached, effective_relay_intent)
        if self._auto_audit_log_enabled():
            self.write_auto_audit_event(base_reason, cached)

    def _last_auto_metrics_source(self) -> dict[str, Any]:
        """Return the current metrics dict passed into decision-trace normalization."""
        if not hasattr(self.service, "_last_auto_metrics"):
            return {}
        metrics = self.service._last_auto_metrics
        return metrics if isinstance(metrics, dict) else {}

    def _auto_audit_log_enabled(self) -> bool:
        """Return True when Auto decision audit events should be written."""
        if not hasattr(self.service, "auto_audit_log"):
            return False
        return bool(self.service.auto_audit_log)

    def _observed_relay_state(self) -> bool:
        """Return the best current relay state hint for broad Auto-state classification."""
        return bool(_fresh_confirmed_relay_output(self.service, self._learning_policy_now()))

    def _derive_auto_state(self, reason: str) -> str:
        """Return the broad Auto state for one detailed health reason."""
        return _derive_auto_state(
            reason,
            relay_on=self._observed_relay_state(),
            learned_charge_power_state=getattr(self.service, "learned_charge_power_state", "unknown"),
        )

    def _set_auto_state(self, state: str) -> None:
        """Persist one explicit broad Auto state for diagnostics and auditing."""
        self.service._last_auto_state = state
        self.service._last_auto_state_code = _auto_state_code(state)
        metrics = getattr(self.service, "_last_auto_metrics", None)
        if isinstance(metrics, dict):
            metrics["state"] = state

    def _reset_auto_state(self) -> None:
        """Reset Auto start/stop timers and rolling samples."""
        svc = self.service
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc.auto_stop_condition_reason = None
        self.clear_auto_samples()

    def _clear_auto_start_tracking(self, clear_samples: bool = False) -> None:
        """Clear pending Auto-start tracking, optionally including average samples."""
        self.service.auto_start_condition_since = None
        if clear_samples:
            self.clear_auto_samples()

    def _clear_auto_stop_tracking(self) -> None:
        """Clear pending Auto-stop tracking."""
        self.service.auto_stop_condition_since = None
        self.service.auto_stop_condition_reason = None

    def _set_health_result(self, reason: str, cached_inputs: bool, result: bool) -> bool:
        """Set a health reason and return the corresponding decision result."""
        self.set_health(reason, cached_inputs, relay_intent=result)
        return result

    def _idle_result_with_health(self, reason: str, cached_inputs: bool) -> bool:
        """Return the standard idle/relay-off result with a health reason."""
        self._clear_auto_start_tracking()
        return self._set_health_result(reason, cached_inputs, False)

    def _running_result_with_health(self, reason: str, cached_inputs: bool) -> bool:
        """Return the standard running/relay-on result with a health reason."""
        self._clear_auto_stop_tracking()
        return self._set_health_result(reason, cached_inputs, True)
