# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any


from venus_evcharger.core.contracts import non_negative_int, normalize_binary_flag, normalized_auto_state_pair

from .base import _ControllerBoundPort


PendingRelayCommand = tuple[bool | None, float | None]


def _require_pending_relay_command(value: object) -> PendingRelayCommand:
    if not isinstance(value, tuple):
        raise TypeError(f"peek_pending_relay_command must return tuple, got {type(value).__name__}")
    if len(value) != 2:
        raise TypeError(f"peek_pending_relay_command must return tuple length 2, got {len(value)}")
    relay_on, requested_at = value
    return _pending_relay_state(relay_on), _pending_relay_requested_at(requested_at)


def _pending_relay_state(value: object) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise TypeError(f"peek_pending_relay_command state must be bool|None, got {type(value).__name__}")


def _pending_relay_requested_at(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"peek_pending_relay_command timestamp must be int|float|None, got {type(value).__name__}")
    return float(value)


class AutoDecisionPort(_ControllerBoundPort):
    """Expose the Auto-decision surface needed by ``AutoDecisionController``."""

    _ALLOWED_ATTRS = {
        "auto_samples",
        "auto_average_window_seconds",
        "relay_last_changed_at",
        "relay_last_off_at",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "auto_stop_condition_reason",
        "_last_health_reason",
        "_last_health_code",
        "_last_auto_state",
        "_last_auto_state_code",
        "_last_pm_status_confirmed",
        "_last_confirmed_pm_status",
        "_last_confirmed_pm_status_at",
        "auto_shelly_soft_fail_seconds",
        "auto_min_runtime_seconds",
        "auto_min_offtime_seconds",
        "_last_grid_at",
        "_grid_recovery_required",
        "_grid_recovery_since",
        "auto_grid_missing_stop_seconds",
        "auto_grid_recovery_start_seconds",
        "virtual_mode",
        "_auto_mode_cutover_pending",
        "_ignore_min_offtime_once",
        "_last_battery_allow_warning",
        "auto_allow_without_battery_soc",
        "auto_battery_scan_interval_seconds",
        "auto_resume_soc",
        "auto_min_soc",
        "auto_high_soc_threshold",
        "auto_high_soc_release_threshold",
        "auto_high_soc_start_surplus_watts",
        "auto_high_soc_stop_surplus_watts",
        "auto_stop_delay_seconds",
        "auto_stop_grid_import_watts",
        "auto_stop_surplus_delay_seconds",
        "auto_stop_ewma_alpha",
        "auto_stop_ewma_alpha_stable",
        "auto_stop_ewma_alpha_volatile",
        "auto_stop_surplus_volatility_low_watts",
        "auto_stop_surplus_volatility_high_watts",
        "auto_policy",
        "auto_night_lock_stop",
        "_last_auto_metrics",
        "_auto_high_soc_profile_active",
        "_stop_smoothed_surplus_power",
        "_stop_smoothed_grid_power",
        "started_at",
        "auto_startup_warmup_seconds",
        "manual_override_until",
        "virtual_autostart",
        "auto_start_delay_seconds",
        "auto_start_max_grid_import_watts",
        "auto_start_surplus_watts",
        "auto_stop_surplus_watts",
        "_auto_cached_inputs_used",
        "virtual_enable",
        "virtual_startstop",
        "auto_daytime_only",
        "auto_month_windows",
        "auto_scheduled_enabled_days",
        "auto_scheduled_night_start_delay_seconds",
        "auto_scheduled_latest_end_time",
        "auto_audit_log",
        "auto_scheduled_night_current_amps",
    }

    _ALLOWED_METHODS = {
        "_time_now",
    }

    _MUTABLE_ATTRS = _ALLOWED_ATTRS

    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: object) -> None:
        normalize_mode = getattr(self._service, "_normalize_mode", None)
        self._service.virtual_mode = (
            normalize_mode(value) if callable(normalize_mode) else non_negative_int(value)
        )

    @property
    def virtual_autostart(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_autostart", 1), default=1)

    @virtual_autostart.setter
    def virtual_autostart(self, value: object) -> None:
        self._service.virtual_autostart = normalize_binary_flag(value)

    @property
    def virtual_enable(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_enable", 1), default=1)

    @virtual_enable.setter
    def virtual_enable(self, value: object) -> None:
        self._service.virtual_enable = normalize_binary_flag(value)

    @property
    def virtual_startstop(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_startstop", 1), default=1)

    @virtual_startstop.setter
    def virtual_startstop(self, value: object) -> None:
        self._service.virtual_startstop = normalize_binary_flag(value)

    @property
    def _auto_mode_cutover_pending(self) -> bool:
        return bool(getattr(self._service, "_auto_mode_cutover_pending", False))

    @_auto_mode_cutover_pending.setter
    def _auto_mode_cutover_pending(self, value: object) -> None:
        self._service._auto_mode_cutover_pending = bool(value)

    @property
    def _ignore_min_offtime_once(self) -> bool:
        return bool(getattr(self._service, "_ignore_min_offtime_once", False))

    @_ignore_min_offtime_once.setter
    def _ignore_min_offtime_once(self, value: object) -> None:
        self._service._ignore_min_offtime_once = bool(value)

    @property
    def _last_auto_state(self) -> str:
        state, _code = normalized_auto_state_pair(
            getattr(self._service, "_last_auto_state", "idle"),
            getattr(self._service, "_last_auto_state_code", 0),
        )
        return state

    @_last_auto_state.setter
    def _last_auto_state(self, value: object) -> None:
        state, code = normalized_auto_state_pair(value, getattr(self._service, "_last_auto_state_code", 0))
        self._service._last_auto_state = state
        self._service._last_auto_state_code = code

    @property
    def _last_auto_state_code(self) -> int:
        _state, code = normalized_auto_state_pair(
            getattr(self._service, "_last_auto_state", "idle"),
            getattr(self._service, "_last_auto_state_code", 0),
        )
        return code

    @_last_auto_state_code.setter
    def _last_auto_state_code(self, value: object) -> None:
        state, code = normalized_auto_state_pair(getattr(self._service, "_last_auto_state", "idle"), value)
        self._service._last_auto_state = state
        self._service._last_auto_state_code = code

    def clear_auto_samples(self) -> object:
        return self._controller_or_override("_clear_auto_samples", "clear_auto_samples")()

    def set_health(self, reason: str, cached: bool = False, relay_intent: bool | None = None) -> object:
        return self._controller_or_override("_set_health", "set_health")(reason, cached, relay_intent=relay_intent)

    def save_runtime_state(self) -> object:
        return self._service._save_runtime_state()

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> object:
        return self._service._write_auto_audit_event(reason, cached)

    def peek_pending_relay_command(self) -> tuple[bool | None, float | None]:
        return _require_pending_relay_command(self._service._peek_pending_relay_command())

    def is_within_auto_daytime_window(self, current_dt: object | None = None) -> bool:
        check = self._controller_or_override(
            "_is_within_auto_daytime_window",
            "is_within_auto_daytime_window",
        )
        return bool(check() if current_dt is None else check(current_dt))

    def get_available_surplus_watts(self, pv_power: float, grid_power: float) -> float:
        return float(self._controller_or_override(
            "_get_available_surplus_watts",
            "get_available_surplus_watts",
        )(pv_power, grid_power))

    def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> object:
        return self._controller_or_override("_add_auto_sample", "add_auto_sample")(
            now,
            surplus_power,
            grid_power,
        )

    def average_auto_metric(self, index: int) -> float | None:
        value = self._controller_or_override("_average_auto_metric", "average_auto_metric")(index)
        return None if value is None else float(value)
