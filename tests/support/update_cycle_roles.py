# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit role adapters for legacy-shaped update-cycle scenario fixtures."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from typing import Protocol, TypeVar, cast


class _FixtureObject(Protocol):
    """Marker protocol for mutable scenario fixtures."""


_T = TypeVar("_T")


def _fixture_attr(owner: object, name: str) -> object:
    try:
        return getattr(owner, name)
    except AttributeError as error:
        raise AttributeError(f"update-cycle fixture does not provide {name}") from error


def _fixture_callable(owner: object, name: str) -> Callable[..., object]:
    value = _fixture_attr(owner, name)
    if not callable(value):
        raise TypeError(f"update-cycle fixture attribute {name} is not callable")
    return cast(Callable[..., object], value)


class UpdateCycleAutoRole:
    """Expose the canonical Auto port over explicit scenario callbacks."""

    def __init__(self, owner: object) -> None:
        self._owner = owner

    def mode_uses_auto_logic(self, mode: object) -> bool:
        return bool(_fixture_callable(self._owner, "_mode_uses_auto_logic")(mode))

    def decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> bool:
        return bool(
            _fixture_callable(self._owner, "_auto_decide_relay")(
                relay_on,
                pv_power,
                battery_soc,
                grid_power,
            )
        )

    def set_health(self, reason: str, *, cached: bool) -> None:
        _fixture_callable(self._owner, "_set_health")(reason, cached=cached)

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        _fixture_callable(self._owner, "_mark_relay_changed")(relay_on, now)


class UpdateCycleRuntimeRole:
    """Expose runtime effects through the canonical update-cycle port."""

    def __init__(self, owner: object) -> None:
        self._owner = owner

    def ensure_observability_state(self) -> None:
        _fixture_callable(self._owner, "_ensure_observability_state")()

    def ensure_auto_input_helper(self, now: float | None = None) -> None:
        effective_now = now
        if effective_now is None:
            clock = getattr(self._owner, "time_now", None)
            effective_now = float(clock()) if callable(clock) else 0.0
        _fixture_callable(self._owner, "_ensure_auto_input_helper_process")(effective_now)

    def recover_watchdog(self, now: float) -> None:
        _fixture_callable(self._owner, "_watchdog_recover")(now)

    def refresh_auto_input_snapshot(self, now: float) -> None:
        _fixture_callable(self._owner, "_refresh_auto_input_snapshot")(now)

    def worker_snapshot(self) -> dict[str, object]:
        value = _fixture_callable(self._owner, "_get_worker_snapshot")()
        if not isinstance(value, dict):
            raise TypeError("update-cycle worker snapshot must be a dict")
        return cast(dict[str, object], value)

    def start_io_worker(self) -> None:
        _fixture_callable(self._owner, "_start_io_worker")()

    def ensure_worker_state(self) -> None:
        _fixture_callable(self._owner, "_ensure_worker_state")()

    def update_worker_snapshot(self, **fields: object) -> None:
        _fixture_callable(self._owner, "_update_worker_snapshot")(**fields)

    def mark_failure(self, source_key: str) -> None:
        _fixture_callable(self._owner, "_mark_failure")(source_key)

    def mark_recovery(self, source_key: str, message: str, *args: object) -> None:
        _fixture_callable(self._owner, "_mark_recovery")(source_key, message, *args)

    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        _fixture_callable(self._owner, "_warning_throttled")(
            warning_key,
            interval_seconds,
            warning_message,
            *args,
            **kwargs,
        )

    def source_retry_ready(self, source_key: str, now: float) -> bool:
        return bool(_fixture_callable(self._owner, "_source_retry_ready")(source_key, now))

    def source_retry_remaining(self, source_key: str, now: float | None = None) -> int:
        return int(_fixture_callable(self._owner, "_source_retry_remaining")(source_key, now))

    def delay_source_retry(
        self,
        source_key: str,
        now: float,
        delay_seconds: float | None = None,
    ) -> None:
        _fixture_callable(self._owner, "_delay_source_retry")(source_key, now, delay_seconds)

    def queue_relay_command(self, relay_on: bool, current_time: float) -> object:
        return _fixture_callable(self._owner, "_queue_relay_command")(relay_on, current_time)

    def publish_local_pm_status(self, relay_on: bool, now: float) -> object:
        return _fixture_callable(self._owner, "_publish_local_pm_status")(relay_on, now)

    def pending_relay_command(self) -> tuple[bool | None, float | None]:
        value = _fixture_callable(self._owner, "_peek_pending_relay_command")()
        if not isinstance(value, tuple) or len(value) != 2:
            raise TypeError("pending relay command must be a two-item tuple")
        return cast(tuple[bool | None, float | None], value)

    def phase_selection_requires_pause(self) -> bool:
        return bool(_fixture_callable(self._owner, "_phase_selection_requires_pause")())

    def apply_phase_selection(self, selection: str) -> str:
        return str(_fixture_callable(self._owner, "_apply_phase_selection")(selection))


class UpdateCycleStateRole:
    """Expose persistence and publication effects through one typed role."""

    def __init__(self, owner: object) -> None:
        self._owner = owner

    def save_runtime_state(self) -> object:
        return _fixture_callable(self._owner, "_save_runtime_state")()

    def flush_runtime_overrides(self, now: float | None = None) -> None:
        callback = getattr(self._owner, "_flush_runtime_overrides", None)
        if callable(callback):
            callback(now)

    def summary(self) -> str:
        return str(_fixture_callable(self._owner, "_state_summary")())

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: dict[str, dict[str, float]],
        now: float,
    ) -> bool:
        return bool(
            _fixture_callable(self._owner, "_publish_live_measurements")(
                power,
                voltage,
                total_current,
                phase_data,
                now,
            )
        )

    def publish_energy_time_measurements(
        self,
        total_energy: float,
        phase_energies: dict[str, float],
        charging_time: int,
        session_energy: float,
        now: float,
    ) -> bool:
        return bool(
            _fixture_callable(self._owner, "_publish_energy_time_measurements")(
                total_energy,
                phase_energies,
                charging_time,
                session_energy,
                now,
            )
        )

    def publish_config_paths(self, startstop_display: int, now: float) -> bool:
        return bool(_fixture_callable(self._owner, "_publish_config_paths")(startstop_display, now))

    def publish_diagnostic_paths(self, now: float) -> bool:
        return bool(_fixture_callable(self._owner, "_publish_diagnostic_paths")(now))

    def publish_field(self, field: str, value: object, now: float, *, force: bool = False) -> bool:
        return bool(_fixture_callable(self._owner, "_publish_dbus_field")(field, value, now, force=force))

    def last_accepted_field(self, field: str) -> object:
        values = getattr(self._owner, "_accepted_publication_fields", {})
        if not isinstance(values, Mapping):
            raise TypeError("accepted publication fields must be a mapping")
        return values.get(field)

    def publish_companion_bridge(self, now: float | None = None) -> bool:
        return bool(_fixture_callable(self._owner, "_publish_companion_dbus_bridge")(now))


def install_update_cycle_roles(service: _T) -> _T:
    """Attach canonical roles to one mutable test fixture when they are absent."""
    fixture = cast(_FixtureObject, service)
    _install_fixture_defaults(fixture)
    if not hasattr(fixture, "auto"):
        setattr(fixture, "auto", UpdateCycleAutoRole(fixture))
    if not hasattr(fixture, "runtime"):
        setattr(fixture, "runtime", UpdateCycleRuntimeRole(fixture))
    if not hasattr(fixture, "state"):
        setattr(fixture, "state", UpdateCycleStateRole(fixture))
    if not hasattr(fixture, "topology_configured"):
        setattr(fixture, "topology_configured", bool(getattr(fixture, "_host_configured", False)))
    if not hasattr(fixture, "host_configured"):
        setattr(fixture, "host_configured", bool(getattr(fixture, "_host_configured", False)))
    return service


def _install_fixture_defaults(fixture: object) -> None:
    """Complete the neutral parts of the update-cycle test contract."""
    def source_retry_ready(source_key: str, now: float) -> bool:
        retry_after = cast(dict[str, float], getattr(fixture, "_source_retry_after"))
        return float(retry_after.get(source_key, 0.0)) <= float(now)

    def source_retry_remaining(source_key: str, now: float | None = None) -> int:
        clock = getattr(fixture, "time_now", None)
        current = float(clock()) if now is None and callable(clock) else float(now or 0.0)
        retry_after = cast(dict[str, float], getattr(fixture, "_source_retry_after"))
        return max(0, int(float(retry_after.get(source_key, 0.0)) - current))

    def delay_source_retry(source_key: str, now: float, delay_seconds: float | None = None) -> None:
        retry_after = cast(dict[str, float], getattr(fixture, "_source_retry_after"))
        retry_after[source_key] = float(now) + float(1.0 if delay_seconds is None else delay_seconds)

    callback_defaults: dict[str, Callable[..., object]] = {
        "_apply_phase_selection": lambda selection: selection,
        "_auto_decide_relay": lambda relay_on, _pv, _soc, _grid: relay_on,
        "_ensure_auto_input_helper_process": lambda _now: None,
        "_ensure_observability_state": lambda: None,
        "_ensure_worker_state": lambda: None,
        "_get_worker_snapshot": lambda: {},
        "_delay_source_retry": delay_source_retry,
        "_mark_failure": lambda _source: None,
        "_mark_relay_changed": lambda _relay_on, _now=None: None,
        "_mark_recovery": lambda _source, _message, *_args: None,
        "_mode_uses_auto_logic": lambda mode: int(mode) in (1, 2),
        "_peek_pending_relay_command": lambda: (None, None),
        "_phase_selection_requires_pause": lambda: False,
        "_publish_companion_dbus_bridge": lambda _now=None: False,
        "_publish_config_paths": lambda _startstop, _now: False,
        "_publish_diagnostic_paths": lambda _now: False,
        "_publish_dbus_field": lambda _field, _value, _now, **_kwargs: False,
        "_publish_energy_time_measurements": lambda *_args: False,
        "_publish_live_measurements": lambda *_args: False,
        "_publish_local_pm_status": lambda relay_on, _now: {"output": relay_on},
        "_queue_relay_command": lambda _relay_on, _now: None,
        "_refresh_auto_input_snapshot": lambda _now: None,
        "_save_runtime_state": lambda: None,
        "_set_health": lambda _reason, **_kwargs: None,
        "_source_retry_ready": source_retry_ready,
        "_source_retry_remaining": source_retry_remaining,
        "_start_io_worker": lambda: None,
        "_state_summary": lambda: "state",
        "_update_worker_snapshot": lambda **_fields: None,
        "_warning_throttled": lambda *_args, **_kwargs: None,
        "_watchdog_recover": lambda _now: None,
    }
    value_defaults: dict[str, object] = {
        "_contactor_fault_counts": {},
        "_accepted_publication_fields": {},
        "_last_auto_metrics": {},
        "_last_charger_fault_active": 0,
        "_last_charger_transport_source": "",
        "_last_health_reason": "init",
        "_last_status_source": "init",
        "_last_voltage": 0.0,
        "_relay_sync_deadline_at": None,
        "_relay_sync_expected_state": None,
        "_relay_sync_failure_reported": False,
        "_relay_sync_requested_at": None,
        "_source_retry_after": {},
    }
    for name, callback in callback_defaults.items():
        if not hasattr(fixture, name):
            setattr(fixture, name, callback)
    for name, value in value_defaults.items():
        if not hasattr(fixture, name):
            setattr(fixture, name, value.copy() if isinstance(value, dict) else value)


__all__ = [
    "UpdateCycleAutoRole",
    "UpdateCycleRuntimeRole",
    "UpdateCycleStateRole",
    "install_update_cycle_roles",
]
