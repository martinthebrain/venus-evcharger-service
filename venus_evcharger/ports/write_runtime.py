# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-facing property surface for the DBus write controller port.

The write controller should not know the concrete service object's internal
attribute layout. This base class provides that narrow boundary as explicit
properties for manual control state, Auto thresholds, scheduled charging,
learning settings, phase switching, and transient runtime flags.

Most properties are intentionally simple pass-throughs with light normalization.
Validation belongs in the control service, write controller, and config loader;
the port only translates between controller concepts and service attributes.
That separation keeps external command behavior stable while allowing the
service runtime to evolve behind the port.

The module is long because the public runtime surface is long, not because the
individual accessors are complex. Keeping the accessor contract in one place
makes tests exercise the same boundary production uses and avoids duplicate
DBus-write glue in controller code.

If this surface grows further, the safe split point is by setting group while
preserving the aggregate runtime-port boundary.
Callers should treat every property here as part of the write-controller port
contract, even when the backing service attribute is private or transitional.
That makes rollback, runtime persistence, and GUI writes share one consistent
attribute vocabulary.
"""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.backend.models import (
    PhaseSelection,
    effective_supported_phase_selections,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.contracts import finite_float_or_none, non_negative_int, normalize_binary_flag
from venus_evcharger.ports.base import _BaseServicePort


def _finite_service_float(service: Any, attr_name: str, default: float = 0.0) -> float:
    coerced = finite_float_or_none(getattr(service, attr_name, None))
    return float(default if coerced is None else coerced)


def _set_finite_service_float(service: Any, attr_name: str, value: object) -> None:
    coerced = finite_float_or_none(value)
    setattr(service, attr_name, float(0.0 if coerced is None else coerced))


def _service_binary_flag(service: Any, attr_name: str) -> int:
    try:
        normalized = int(getattr(service, attr_name))
    except (AttributeError, TypeError, ValueError):
        return 1
    return 0 if normalized <= 0 else 1


def _set_service_binary_flag(service: Any, attr_name: str, value: object, *, as_bool: bool = False) -> None:
    normalized = normalize_binary_flag(value)
    setattr(service, attr_name, bool(normalized) if as_bool else normalized)


class WriteControllerRuntimePort(_BaseServicePort):

    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: object) -> None:
        normalize_mode = getattr(self._service, "_normalize_mode", None)
        self._service.virtual_mode = normalize_mode(value) if callable(normalize_mode) else non_negative_int(value)

    @property
    def virtual_autostart(self) -> int:
        return _service_binary_flag(self._service, "virtual_autostart")

    @virtual_autostart.setter
    def virtual_autostart(self, value: object) -> None:
        _set_service_binary_flag(self._service, "virtual_autostart", value)

    @property
    def virtual_startstop(self) -> int:
        return _service_binary_flag(self._service, "virtual_startstop")

    @virtual_startstop.setter
    def virtual_startstop(self, value: object) -> None:
        _set_service_binary_flag(self._service, "virtual_startstop", value)

    @property
    def virtual_enable(self) -> int:
        return _service_binary_flag(self._service, "virtual_enable")

    @virtual_enable.setter
    def virtual_enable(self, value: object) -> None:
        _set_service_binary_flag(self._service, "virtual_enable", value)

    @property
    def auto_manual_override_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_manual_override_seconds")

    @property
    def virtual_set_current(self) -> float:
        return _finite_service_float(self._service, "virtual_set_current")

    @virtual_set_current.setter
    def virtual_set_current(self, value: object) -> None:
        _set_finite_service_float(self._service, "virtual_set_current", value)

    @property
    def min_current(self) -> float:
        return _finite_service_float(self._service, "min_current")

    @min_current.setter
    def min_current(self, value: object) -> None:
        _set_finite_service_float(self._service, "min_current", value)

    @property
    def max_current(self) -> float:
        return _finite_service_float(self._service, "max_current")

    @max_current.setter
    def max_current(self, value: object) -> None:
        _set_finite_service_float(self._service, "max_current", value)

    @property
    def auto_start_surplus_watts(self) -> float:
        return _finite_service_float(self._service, "auto_start_surplus_watts")

    @auto_start_surplus_watts.setter
    def auto_start_surplus_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_start_surplus_watts", value)

    @property
    def auto_stop_surplus_watts(self) -> float:
        return _finite_service_float(self._service, "auto_stop_surplus_watts")

    @auto_stop_surplus_watts.setter
    def auto_stop_surplus_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_stop_surplus_watts", value)

    @property
    def auto_min_soc(self) -> float:
        return _finite_service_float(self._service, "auto_min_soc")

    @auto_min_soc.setter
    def auto_min_soc(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_min_soc", value)

    @property
    def auto_resume_soc(self) -> float:
        return _finite_service_float(self._service, "auto_resume_soc")

    @auto_resume_soc.setter
    def auto_resume_soc(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_resume_soc", value)

    @property
    def auto_start_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_start_delay_seconds")

    @auto_start_delay_seconds.setter
    def auto_start_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_start_delay_seconds", value)

    @property
    def auto_stop_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_stop_delay_seconds")

    @auto_stop_delay_seconds.setter
    def auto_stop_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_stop_delay_seconds", value)

    @property
    def auto_scheduled_enabled_days(self) -> str:
        return scheduled_enabled_days_text(
            getattr(self._service, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            DEFAULT_SCHEDULED_ENABLED_DAYS,
        )

    @auto_scheduled_enabled_days.setter
    def auto_scheduled_enabled_days(self, value: object) -> None:
        self._service.auto_scheduled_enabled_days = scheduled_enabled_days_text(value, DEFAULT_SCHEDULED_ENABLED_DAYS)

    @property
    def auto_scheduled_night_start_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_scheduled_night_start_delay_seconds")

    @auto_scheduled_night_start_delay_seconds.setter
    def auto_scheduled_night_start_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_scheduled_night_start_delay_seconds", value)

    @property
    def auto_scheduled_latest_end_time(self) -> str:
        return normalize_hhmm_text(getattr(self._service, "auto_scheduled_latest_end_time", "06:30"), "06:30")

    @auto_scheduled_latest_end_time.setter
    def auto_scheduled_latest_end_time(self, value: object) -> None:
        self._service.auto_scheduled_latest_end_time = normalize_hhmm_text(value, "06:30")

    @property
    def auto_scheduled_night_current_amps(self) -> float:
        return _finite_service_float(self._service, "auto_scheduled_night_current_amps")

    @auto_scheduled_night_current_amps.setter
    def auto_scheduled_night_current_amps(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_scheduled_night_current_amps", value)

    @property
    def _software_update_run_requested_at(self) -> float | None:
        return finite_float_or_none(getattr(self._service, "_software_update_run_requested_at", None))

    @_software_update_run_requested_at.setter
    def _software_update_run_requested_at(self, value: object) -> None:
        self._service._software_update_run_requested_at = finite_float_or_none(value)

    @property
    def auto_dbus_backoff_base_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_dbus_backoff_base_seconds")

    @auto_dbus_backoff_base_seconds.setter
    def auto_dbus_backoff_base_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_dbus_backoff_base_seconds", value)

    @property
    def auto_dbus_backoff_max_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_dbus_backoff_max_seconds")

    @auto_dbus_backoff_max_seconds.setter
    def auto_dbus_backoff_max_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_dbus_backoff_max_seconds", value)

    @property
    def auto_grid_recovery_start_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_grid_recovery_start_seconds")

    @auto_grid_recovery_start_seconds.setter
    def auto_grid_recovery_start_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_grid_recovery_start_seconds", value)

    @property
    def auto_stop_surplus_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_stop_surplus_delay_seconds")

    @auto_stop_surplus_delay_seconds.setter
    def auto_stop_surplus_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_stop_surplus_delay_seconds", value)

    @property
    def auto_stop_surplus_volatility_low_watts(self) -> float:
        return _finite_service_float(self._service, "auto_stop_surplus_volatility_low_watts")

    @auto_stop_surplus_volatility_low_watts.setter
    def auto_stop_surplus_volatility_low_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_stop_surplus_volatility_low_watts", value)

    @property
    def auto_stop_surplus_volatility_high_watts(self) -> float:
        return _finite_service_float(self._service, "auto_stop_surplus_volatility_high_watts")

    @auto_stop_surplus_volatility_high_watts.setter
    def auto_stop_surplus_volatility_high_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_stop_surplus_volatility_high_watts", value)

    @property
    def auto_reference_charge_power_watts(self) -> float:
        return _finite_service_float(self._service, "auto_reference_charge_power_watts")

    @auto_reference_charge_power_watts.setter
    def auto_reference_charge_power_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_reference_charge_power_watts", value)

    @property
    def auto_learn_charge_power_enabled(self) -> int:
        return _service_binary_flag(self._service, "auto_learn_charge_power_enabled")

    @auto_learn_charge_power_enabled.setter
    def auto_learn_charge_power_enabled(self, value: object) -> None:
        _set_service_binary_flag(self._service, "auto_learn_charge_power_enabled", value, as_bool=True)

    @property
    def auto_learn_charge_power_min_watts(self) -> float:
        return _finite_service_float(self._service, "auto_learn_charge_power_min_watts")

    @auto_learn_charge_power_min_watts.setter
    def auto_learn_charge_power_min_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_learn_charge_power_min_watts", value)

    @property
    def auto_learn_charge_power_alpha(self) -> float:
        return _finite_service_float(self._service, "auto_learn_charge_power_alpha")

    @auto_learn_charge_power_alpha.setter
    def auto_learn_charge_power_alpha(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_learn_charge_power_alpha", value)

    @property
    def auto_learn_charge_power_start_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_learn_charge_power_start_delay_seconds")

    @auto_learn_charge_power_start_delay_seconds.setter
    def auto_learn_charge_power_start_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_learn_charge_power_start_delay_seconds", value)

    @property
    def auto_learn_charge_power_window_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_learn_charge_power_window_seconds")

    @auto_learn_charge_power_window_seconds.setter
    def auto_learn_charge_power_window_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_learn_charge_power_window_seconds", value)

    @property
    def auto_learn_charge_power_max_age_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_learn_charge_power_max_age_seconds")

    @auto_learn_charge_power_max_age_seconds.setter
    def auto_learn_charge_power_max_age_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_learn_charge_power_max_age_seconds", value)

    @property
    def auto_phase_switching_enabled(self) -> int:
        return _service_binary_flag(self._service, "auto_phase_switching_enabled")

    @auto_phase_switching_enabled.setter
    def auto_phase_switching_enabled(self, value: object) -> None:
        _set_service_binary_flag(self._service, "auto_phase_switching_enabled", value, as_bool=True)

    @property
    def auto_phase_prefer_lowest_when_idle(self) -> int:
        return _service_binary_flag(self._service, "auto_phase_prefer_lowest_when_idle")

    @auto_phase_prefer_lowest_when_idle.setter
    def auto_phase_prefer_lowest_when_idle(self, value: object) -> None:
        _set_service_binary_flag(self._service, "auto_phase_prefer_lowest_when_idle", value, as_bool=True)

    @property
    def auto_phase_upshift_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_phase_upshift_delay_seconds")

    @auto_phase_upshift_delay_seconds.setter
    def auto_phase_upshift_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_phase_upshift_delay_seconds", value)

    @property
    def auto_phase_downshift_delay_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_phase_downshift_delay_seconds")

    @auto_phase_downshift_delay_seconds.setter
    def auto_phase_downshift_delay_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_phase_downshift_delay_seconds", value)

    @property
    def auto_phase_upshift_headroom_watts(self) -> float:
        return _finite_service_float(self._service, "auto_phase_upshift_headroom_watts")

    @auto_phase_upshift_headroom_watts.setter
    def auto_phase_upshift_headroom_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_phase_upshift_headroom_watts", value)

    @property
    def auto_phase_downshift_margin_watts(self) -> float:
        return _finite_service_float(self._service, "auto_phase_downshift_margin_watts")

    @auto_phase_downshift_margin_watts.setter
    def auto_phase_downshift_margin_watts(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_phase_downshift_margin_watts", value)

    @property
    def auto_phase_mismatch_retry_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_phase_mismatch_retry_seconds")

    @auto_phase_mismatch_retry_seconds.setter
    def auto_phase_mismatch_retry_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_phase_mismatch_retry_seconds", value)

    @property
    def auto_phase_mismatch_lockout_count(self) -> int:
        return non_negative_int(getattr(self._service, "auto_phase_mismatch_lockout_count", 0))

    @auto_phase_mismatch_lockout_count.setter
    def auto_phase_mismatch_lockout_count(self, value: object) -> None:
        self._service.auto_phase_mismatch_lockout_count = non_negative_int(value)

    @property
    def auto_phase_mismatch_lockout_seconds(self) -> float:
        return _finite_service_float(self._service, "auto_phase_mismatch_lockout_seconds")

    @auto_phase_mismatch_lockout_seconds.setter
    def auto_phase_mismatch_lockout_seconds(self, value: object) -> None:
        _set_finite_service_float(self._service, "auto_phase_mismatch_lockout_seconds", value)

    @property
    def supported_phase_selections(self) -> tuple[str, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(
            getattr(self._service, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        time_now = getattr(self._service, "_time_now", None)
        current_time = float(time_now()) if callable(time_now) else time.time()
        return effective_supported_phase_selections(
            normalized,
            lockout_selection=getattr(self._service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(self._service, "_phase_switch_lockout_until", None),
            now=current_time,
        )

    @supported_phase_selections.setter
    def supported_phase_selections(self, value: object) -> None:
        self._service.supported_phase_selections = normalize_phase_selection_tuple(value, ("P1",))

    @property
    def requested_phase_selection(self) -> str:
        fallback = normalize_phase_selection(self.supported_phase_selections[0], "P1")
        normalized: PhaseSelection = normalize_phase_selection(
            getattr(self._service, "requested_phase_selection", fallback),
            fallback,
        )
        return str(normalized)

    @requested_phase_selection.setter
    def requested_phase_selection(self, value: object) -> None:
        fallback = normalize_phase_selection(self.supported_phase_selections[0], "P1")
        self._service.requested_phase_selection = normalize_phase_selection(value, fallback)

    @property
    def active_phase_selection(self) -> str:
        fallback = normalize_phase_selection(self.requested_phase_selection, "P1")
        normalized: PhaseSelection = normalize_phase_selection(
            getattr(self._service, "active_phase_selection", fallback),
            fallback,
        )
        return str(normalized)

    @active_phase_selection.setter
    def active_phase_selection(self, value: object) -> None:
        fallback = normalize_phase_selection(self.requested_phase_selection, "P1")
        self._service.active_phase_selection = normalize_phase_selection(value, fallback)

    @property
    def auto_mode_cutover_pending(self) -> bool:
        return bool(getattr(self._service, "_auto_mode_cutover_pending", False))

    @auto_mode_cutover_pending.setter
    def auto_mode_cutover_pending(self, value: object) -> None:
        self._service._auto_mode_cutover_pending = bool(value)

    @property
    def ignore_min_offtime_once(self) -> bool:
        return bool(getattr(self._service, "_ignore_min_offtime_once", False))

    @ignore_min_offtime_once.setter
    def ignore_min_offtime_once(self, value: object) -> None:
        self._service._ignore_min_offtime_once = bool(value)
