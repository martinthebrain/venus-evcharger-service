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

import logging
import math
import time
from datetime import datetime
from typing import Any, Deque

from .policy import AutoPolicy, validate_auto_policy
from venus_evcharger.core.common import (
    auto_state_code as _auto_state_code,
    derive_auto_state as _derive_auto_state,
    fresh_confirmed_relay_output as _fresh_confirmed_relay_output,
    local_datetime_from_timestamp as _local_datetime_from_timestamp,
    mode_uses_scheduled_logic as _mode_uses_scheduled_logic,
    scheduled_mode_snapshot as _scheduled_mode_snapshot,
)
from venus_evcharger.core.contracts import normalized_auto_decision_trace, thresholds_ordered
from venus_evcharger.core.controller_contracts import ComposableControllerRole as _ComposableControllerRole

AutoSample = tuple[float, float, float]
AutoDecision = bool | object
MonthWindow = tuple[tuple[int, int], tuple[int, int]]



class _AutoDecisionSamples(_ComposableControllerRole):
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

    def _learning_policy_now(self) -> float:
        """Return the current timestamp for learned-power freshness checks."""
        time_now = getattr(self.service, "_time_now", None)
        if callable(time_now):
            current_time = time_now()
            if isinstance(current_time, (int, float)):
                return float(current_time)
        return time.time()

    @staticmethod
    def _normalize_learned_charge_power_state(value: Any) -> str:
        """Return one supported learned-power state string."""
        state = str(value).strip().lower() if value is not None else "unknown"
        if state in {"unknown", "learning", "stable", "stale"}:
            return state
        return "unknown"

    def _current_learned_charge_power_state(self, now: float | None = None) -> str:
        """Return the effective learned-power state, including age-based staleness."""
        state = self._stored_learned_charge_power_state()
        if not self._has_positive_learned_charge_power():
            return "unknown"
        stale_state = self._stale_learned_charge_power_state(state, now)
        return state if stale_state is None else stale_state

    def _active_learned_charge_power(self, now: float | None = None) -> float | None:
        """Return the learned charging power when it is present and still fresh."""
        learned_value = self._positive_learned_charge_power()
        if self._learned_charge_power_inactive_for_auto(learned_value, now):
            return None
        return learned_value

    def _stored_learned_charge_power_state(self) -> str:
        """Return the normalized learned-power state stored on the service."""
        return self._normalize_learned_charge_power_state(
            getattr(self.service, "learned_charge_power_state", "unknown")
        )

    def _positive_learned_charge_power(self) -> float | None:
        """Return the learned charging power when it is positive."""
        learned_power = getattr(self.service, "learned_charge_power_watts", None)
        if learned_power is None:
            return None
        learned_value = float(learned_power)
        if learned_value <= 0:
            return None
        return learned_value

    def _has_positive_learned_charge_power(self) -> bool:
        """Return True when a usable learned charging power is present."""
        return self._positive_learned_charge_power() is not None

    def _learned_charge_power_can_expire(self) -> bool:
        """Return True when learned charging power has an age limit."""
        return float(self._auto_policy().learn_charge_power.max_age_seconds) > 0

    def _learned_charge_power_missing_update_time(self) -> bool:
        """Return True when learned charging power has no update timestamp."""
        return getattr(self.service, "learned_charge_power_updated_at", None) is None

    @staticmethod
    def _unknown_or_stale_learning_state(state: str) -> str:
        """Return the fallback state used when learning data has no timestamp."""
        return "unknown" if state == "unknown" else "stale"

    def _learned_charge_power_age_seconds(self, now: float | None = None) -> float | None:
        """Return the age of the learned charging power, if it is timestamped."""
        updated_at = getattr(self.service, "learned_charge_power_updated_at", None)
        if updated_at is None:
            return None
        current_time = self._learning_policy_now() if now is None else float(now)
        return current_time - float(updated_at)

    def _learned_charge_power_expired(self, now: float | None = None) -> bool:
        """Return True when the learned charging power is older than its max age."""
        age_seconds = self._learned_charge_power_age_seconds(now)
        if age_seconds is None:
            return True
        max_age_seconds = float(self._auto_policy().learn_charge_power.max_age_seconds)
        return age_seconds > max_age_seconds

    def _stale_learned_charge_power_state(self, state: str, now: float | None = None) -> str | None:
        """Return an overridden state when learned power is missing freshness."""
        if not self._learned_charge_power_can_expire():
            return None
        if self._learned_charge_power_missing_update_time():
            return self._unknown_or_stale_learning_state(state)
        if self._learned_charge_power_expired(now):
            return "stale"
        return None

    def _learned_charge_power_policy_enabled(self) -> bool:
        """Return True when adaptive learned-power scaling is enabled."""
        return bool(self._auto_policy().learn_charge_power.enabled)

    def _learned_charge_power_age_invalid_for_auto(self, now: float | None = None) -> bool:
        """Return True when learned power cannot be trusted because of age metadata."""
        return self._learned_charge_power_can_expire() and (
            self._learned_charge_power_missing_update_time() or self._learned_charge_power_expired(now)
        )

    def _learned_charge_power_inactive_for_auto(
        self,
        learned_value: float | None,
        now: float | None = None,
    ) -> bool:
        """Return True when learned power must not influence Auto thresholds."""
        return any(
            (
                not self._learned_charge_power_policy_enabled(),
                self._current_learned_charge_power_state(now) != "stable",
                learned_value is None,
                self._learned_charge_power_age_invalid_for_auto(now),
            )
        )

    def _learned_charge_power_scale(self, now: float | None = None) -> float:
        """Return a linear threshold scale derived from the learned charging power."""
        policy = self._auto_policy().learn_charge_power
        learned_value = self._active_learned_charge_power(now)
        if not policy.enabled or learned_value is None:
            return 1.0
        return learned_value / float(policy.reference_power_watts)

    def _scale_surplus_thresholds(self, start_watts: float, stop_watts: float) -> tuple[float, float]:
        """Scale the configured surplus thresholds around the reference charging load."""
        scale = self._learned_charge_power_scale()
        return round(float(start_watts) * scale, 1), round(float(stop_watts) * scale, 1)

    def _surplus_thresholds_for_soc(self, battery_soc: float) -> tuple[float, float, str]:
        """Return the active start/stop surplus thresholds for the current battery SOC."""
        svc = self.service
        policy = self._auto_policy()
        profile, active, profile_name = policy.resolve_threshold_profile(
            battery_soc,
            getattr(svc, "_auto_high_soc_profile_active", None),
        )
        svc._auto_high_soc_profile_active = bool(active)
        start_watts, stop_watts = self._scale_surplus_thresholds(
            float(profile.start_surplus_watts),
            float(profile.stop_surplus_watts),
        )
        if not thresholds_ordered(start_watts, stop_watts):
            logging.warning(
                "Adaptive surplus thresholds became invalid for profile %s: start=%s stop=%s; falling back to static profile values",
                profile_name,
                start_watts,
                stop_watts,
            )
            return float(profile.start_surplus_watts), float(profile.stop_surplus_watts), profile_name
        return start_watts, stop_watts, profile_name

    def _auto_policy(self) -> AutoPolicy:
        """Return the structured AutoPolicy, synthesizing it from legacy attrs when needed."""
        svc = self.service
        policy = getattr(svc, "auto_policy", None)
        if policy is None:
            policy = validate_auto_policy(AutoPolicy.from_service(svc))
            try:
                svc.auto_policy = policy
            except AttributeError:
                pass
        return policy

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
        if not getattr(self.service, "auto_daytime_only", False):
            return True

        current_dt = datetime.now() if current_dt is None else current_dt
        current_minutes = current_dt.hour * 60 + current_dt.minute
        start_minutes, end_minutes = self._daytime_window_minutes_for_month(current_dt.month)
        return self._minutes_within_daytime_window(current_minutes, start_minutes, end_minutes)

    def _scheduled_night_charge_active(self, now: float | None = None) -> bool:
        """Return whether scheduled/plan mode should force nighttime charging."""
        svc = self.service
        if not _mode_uses_scheduled_logic(getattr(svc, "virtual_mode", 0)):
            return False
        current_time = self._learning_policy_now() if now is None else float(now)
        return _scheduled_mode_snapshot(
            _local_datetime_from_timestamp(current_time, getattr(svc, "auto_schedule_timezone", "UTC")),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri"),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "04:30"),
        ).night_boost_active

    def _daytime_window_minutes_for_month(self, month: int) -> tuple[int, int]:
        """Return configured daytime start/end minutes for one month."""
        month_windows: dict[int, MonthWindow] = getattr(self.service, "auto_month_windows", {})
        start_window, end_window = month_windows.get(month, ((8, 0), (18, 0)))
        start_hour, start_minute = start_window
        end_hour, end_minute = end_window
        return start_hour * 60 + start_minute, end_hour * 60 + end_minute

    @staticmethod
    def _minutes_within_daytime_window(current_minutes: int, start_minutes: int, end_minutes: int) -> bool:
        """Return True when current minutes fall inside one daytime window."""
        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

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
            metrics=getattr(svc, "_last_auto_metrics", {}) or {},
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
        if getattr(self.service, "auto_audit_log", False):
            self.write_auto_audit_event(base_reason, cached)

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
