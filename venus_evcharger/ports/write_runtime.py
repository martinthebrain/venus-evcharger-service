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

from typing import Protocol

from venus_evcharger.backend.models import (
    PhaseSelection,
    effective_supported_phase_selections,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.contracts import finite_float_or_none, non_negative_int, normalize_binary_flag


class WriteRuntimeAutoPort(Protocol):
    """Auto facade operation required by the runtime write boundary."""

    def clear_samples(self) -> object: ...

    def normalize_mode(self, value: object) -> int: ...

    def mode_uses_auto_logic(self, mode: object) -> bool: ...


class WriteRuntimeFacadePort(Protocol):
    """Runtime facade operations used by the complete write controller."""

    def queue_relay_command(self, relay_on: bool, current_time: float) -> object: ...

    def publish_local_pm_status(self, relay_on: bool, current_time: float) -> object: ...

    def worker_snapshot(self) -> object: ...

    def pending_relay_command(self) -> object: ...

    def update_worker_snapshot(self, **kwargs: object) -> object: ...

    def phase_selection_requires_pause(self) -> bool: ...

    def apply_phase_selection(self, selection: object) -> object: ...


class WriteRuntimeStatePort(Protocol):
    """State facade operations used by the complete write controller."""

    def publish_field(
        self,
        field: str,
        value: object,
        current_time: float,
        *,
        force: bool,
    ) -> object: ...

    def summary(self) -> object: ...

    def save_runtime_state(self) -> object: ...

    def save_runtime_overrides(self) -> None: ...

    def validate_runtime_config(self) -> None: ...


class WriteRuntimeServicePort(Protocol):
    """Mutable service state owned by the runtime write boundary."""

    auto: WriteRuntimeAutoPort
    runtime: WriteRuntimeFacadePort
    state: WriteRuntimeStatePort
    auto_policy: AutoPolicy
    virtual_mode: object
    virtual_autostart: object
    virtual_startstop: object
    virtual_enable: object
    auto_manual_override_seconds: object
    auto_start_condition_since: object
    auto_stop_condition_since: object
    virtual_set_current: object
    min_current: object
    max_current: object
    auto_start_delay_seconds: object
    auto_stop_delay_seconds: object
    auto_scheduled_enabled_days: object
    auto_scheduled_night_start_delay_seconds: object
    auto_scheduled_latest_end_time: object
    auto_scheduled_night_current_amps: object
    _software_update_run_requested_at: object
    auto_dbus_backoff_base_seconds: object
    auto_dbus_backoff_max_seconds: object
    supported_phase_selections: object
    requested_phase_selection: object
    active_phase_selection: object
    _phase_switch_lockout_selection: object
    _phase_switch_lockout_until: object
    _auto_mode_cutover_pending: object
    _ignore_min_offtime_once: object
    manual_override_until: object

    def time_now(self) -> float: ...


def _finite_service_float(service: object, attr_name: str, default: float = 0.0) -> float:
    coerced = finite_float_or_none(getattr(service, attr_name, None))
    return float(default if coerced is None else coerced)


def _set_finite_service_float(service: object, attr_name: str, value: object) -> None:
    coerced = finite_float_or_none(value)
    setattr(service, attr_name, float(0.0 if coerced is None else coerced))


def _service_binary_flag(service: object, attr_name: str) -> int:
    try:
        normalized = int(getattr(service, attr_name))
    except (AttributeError, TypeError, ValueError):
        return 1
    return 0 if normalized <= 0 else 1


def _set_service_binary_flag(service: object, attr_name: str, value: object, *, as_bool: bool = False) -> None:
    normalized = normalize_binary_flag(value)
    setattr(service, attr_name, bool(normalized) if as_bool else normalized)


class WriteControllerRuntimePort:
    """Explicit normalized runtime state boundary for write handling."""

    def __init__(self, service: WriteRuntimeServicePort) -> None:
        self._service = service

    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: object) -> None:
        self._service.virtual_mode = int(self._service.auto.normalize_mode(value))

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
    def auto_start_condition_since(self) -> float | None:
        return finite_float_or_none(getattr(self._service, "auto_start_condition_since", None))

    @auto_start_condition_since.setter
    def auto_start_condition_since(self, value: object) -> None:
        self._service.auto_start_condition_since = finite_float_or_none(value)

    @property
    def auto_stop_condition_since(self) -> float | None:
        return finite_float_or_none(getattr(self._service, "auto_stop_condition_since", None))

    @auto_stop_condition_since.setter
    def auto_stop_condition_since(self, value: object) -> None:
        self._service.auto_stop_condition_since = finite_float_or_none(value)

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
    def auto_policy(self) -> AutoPolicy:
        policy: AutoPolicy = self._service.auto_policy
        return policy

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
    def supported_phase_selections(self) -> tuple[str, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(
            getattr(self._service, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        current_time = float(self._service.time_now())
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

    @property
    def manual_override_until(self) -> float:
        return _finite_service_float(self._service, "manual_override_until")

    @manual_override_until.setter
    def manual_override_until(self, value: object) -> None:
        _set_finite_service_float(self._service, "manual_override_until", value)
