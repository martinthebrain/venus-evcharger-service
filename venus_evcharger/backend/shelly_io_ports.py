# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow service ports consumed by the composed Shelly I/O components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeGuard

from venus_evcharger.backend.shelly_io_types import (
    JsonObject,
    ShellyHttpSession,
    ShellyPmStatus,
    _LockLike,
    _WorkerStopEventLike,
    _WorkerThreadLike,
)
from venus_evcharger.ports.readback import MutableReadbackStore


class ShellyRuntimeOperations(Protocol):
    """Runtime operations consumed by Shelly components."""

    def ensure_worker_state(self) -> None: ...  # pragma: no cover
    def update_worker_snapshot(self, **fields: object) -> None: ...  # pragma: no cover
    def mark_failure(self, source_key: str) -> None: ...  # pragma: no cover
    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...  # pragma: no cover
    def mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...  # pragma: no cover
    def worker_snapshot(self) -> Mapping[str, object]: ...  # pragma: no cover
    def ensure_auto_input_helper(self, now: float | None = None) -> None: ...  # pragma: no cover
    def source_retry_ready(self, source_key: str, now: float) -> bool: ...  # pragma: no cover
    def source_retry_remaining(self, source_key: str, now: float | None = None) -> int: ...  # pragma: no cover
    def delay_source_retry(self, source_key: str, now: float, delay_seconds: float | None = None) -> None: ...  # pragma: no cover


class ShellyAutoOperations(Protocol):
    """Auto operations consumed by Shelly components."""

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None: ...  # pragma: no cover
    def mode_uses_auto_logic(self, mode: object) -> bool: ...  # pragma: no cover


class ShellyRequestHost(Protocol):
    """Configuration and sessions required by the HTTP/RPC component."""

    session: ShellyHttpSession
    use_digest_auth: bool
    username: str
    password: str
    host: str
    shelly_request_timeout_seconds: float
    pm_component: str
    pm_id: int
    _worker_session: ShellyHttpSession


class ShellyCapabilityHost(Protocol):
    """Runtime values mutated by capability and phase-selection handling."""

    auto_shelly_soft_fail_seconds: float
    _last_pm_status: ShellyPmStatus | JsonObject | None
    _last_pm_status_confirmed: bool
    supported_phase_selections: tuple[str, ...]
    requested_phase_selection: str
    active_phase_selection: str
    _readback_store: MutableReadbackStore
    _last_switch_feedback_closed: bool | None
    _last_switch_interlock_ok: bool | None
    _last_switch_feedback_at: float | None
    runtime: ShellyRuntimeOperations


class ShellyRuntimeHost(Protocol):
    """Charger readback, estimate, and retry state owned by the runtime component."""

    auto_shelly_soft_fail_seconds: float
    virtual_mode: int
    virtual_startstop: int
    virtual_enable: int
    virtual_set_current: float
    supported_phase_selections: tuple[str, ...]
    requested_phase_selection: str
    active_phase_selection: str
    _readback_store: MutableReadbackStore
    _last_voltage: float | None
    _last_charger_state_enabled: bool | None
    _last_charger_state_current_amps: float | None
    _last_charger_state_phase_selection: object | None
    _last_charger_state_actual_current_amps: float | None
    _last_charger_state_power_w: float | None
    _last_charger_state_energy_kwh: float | None
    _last_charger_state_status: str | None
    _last_charger_state_fault: str | None
    _last_charger_state_at: float | None
    _last_charger_estimate_source: str | None
    _last_charger_estimate_at: float | None
    _charger_estimated_energy_kwh: float | None
    _charger_estimated_energy_at: float | None
    _charger_estimated_power_w: float | None
    _last_charger_transport_reason: str | None
    _last_charger_transport_source: str | None
    _last_charger_transport_detail: str | None
    _last_charger_transport_at: float | None
    _charger_retry_reason: str | None
    _charger_retry_source: str | None
    _charger_retry_until: float | None
    _source_retry_after: dict[str, float]
    _charger_target_current_amps: float | None
    _charger_target_current_applied_at: float | None
    runtime: ShellyRuntimeOperations
    auto: ShellyAutoOperations

    def time_now(self) -> float: ...  # pragma: no cover


class ShellyReadbackCacheHost(Protocol):
    """Atomic charger snapshot store needed by cache resolution."""

    _readback_store: MutableReadbackStore


class ShellyReadbackHost(Protocol):
    """Configuration values used while synthesizing normalized PM readback."""

    auto_shelly_soft_fail_seconds: float
    requested_phase_selection: str
    active_phase_selection: str


class ShellyTransportHost(Protocol):
    """Shelly network-health and session state owned by the transport component."""

    session: ShellyHttpSession
    _worker_session: ShellyHttpSession
    auto_shelly_soft_fail_seconds: float
    _shelly_state: str
    _shelly_last_error_reason: str | None
    _shelly_last_error_detail: str | None
    _shelly_last_error_at: float | None
    _shelly_consecutive_errors: int
    _shelly_retry_after: float
    _shelly_offline_since: float | None
    _shelly_last_ok_at: float | None
    _shelly_session_reset_count: int
    _source_retry_after: dict[str, float]
    runtime: ShellyRuntimeOperations


class ShellyWorkerHost(Protocol):
    """Relay queue, PM cache, and worker snapshot state owned by the worker."""

    auto_shelly_soft_fail_seconds: float
    virtual_mode: int
    _worker_session: ShellyHttpSession
    _worker_poll_interval_seconds: float
    _worker_stop_event: _WorkerStopEventLike
    _last_pm_status: ShellyPmStatus | JsonObject | None
    _last_pm_status_at: float | None
    _last_pm_status_confirmed: bool
    _last_voltage: float | None
    _relay_command_lock: _LockLike
    _pending_relay_state: bool | None
    _pending_relay_requested_at: float | None
    relay_sync_timeout_seconds: float
    _relay_sync_expected_state: bool | None
    _relay_sync_requested_at: float | None
    _relay_sync_deadline_at: float | None
    _relay_sync_failure_reported: bool
    runtime: ShellyRuntimeOperations
    auto: ShellyAutoOperations

    def time_now(self) -> float: ...  # pragma: no cover


class ShellyLifecycleHost(Protocol):
    """Thread and stop-event state owned by the worker lifecycle component."""

    shelly_request_timeout_seconds: float
    relay_sync_timeout_seconds: float
    _worker_poll_interval_seconds: float
    _worker_stop_event: _WorkerStopEventLike
    _worker_thread: _WorkerThreadLike | None
    _worker_session: ShellyHttpSession
    runtime: ShellyRuntimeOperations


def is_shelly_request_host(value: object) -> TypeGuard[ShellyRequestHost]:
    return callable(getattr(getattr(value, "session", None), "get", None)) and all(
        hasattr(value, name)
        for name in ("use_digest_auth", "username", "password", "host", "pm_component", "pm_id")
    )


def is_shelly_capability_host(value: object) -> TypeGuard[ShellyCapabilityHost]:
    store = getattr(value, "_readback_store", None)
    runtime = getattr(value, "runtime", None)
    return callable(getattr(store, "replace_switch", None)) and callable(getattr(runtime, "warning_throttled", None))


def is_shelly_runtime_host(value: object) -> TypeGuard[ShellyRuntimeHost]:
    store = getattr(value, "_readback_store", None)
    runtime = getattr(value, "runtime", None)
    auto = getattr(value, "auto", None)
    return all(
        (
            callable(getattr(store, "replace_charger", None)),
            callable(getattr(value, "time_now", None)),
            callable(getattr(runtime, "mark_failure", None)),
            callable(getattr(runtime, "warning_throttled", None)),
            callable(getattr(runtime, "mark_recovery", None)),
            callable(getattr(auto, "mode_uses_auto_logic", None)),
        )
    )


def is_shelly_readback_cache_host(value: object) -> TypeGuard[ShellyReadbackCacheHost]:
    store = getattr(value, "_readback_store", None)
    return callable(getattr(store, "snapshot", None))


def is_shelly_readback_host(value: object) -> TypeGuard[ShellyReadbackHost]:
    return all(
        hasattr(value, name)
        for name in ("auto_shelly_soft_fail_seconds", "requested_phase_selection", "active_phase_selection")
    )


def is_shelly_transport_host(value: object) -> TypeGuard[ShellyTransportHost]:
    runtime = getattr(value, "runtime", None)
    return callable(getattr(runtime, "mark_recovery", None)) and hasattr(value, "_worker_session")


def is_shelly_worker_host(value: object) -> TypeGuard[ShellyWorkerHost]:
    runtime = getattr(value, "runtime", None)
    auto = getattr(value, "auto", None)
    return callable(getattr(value, "time_now", None)) and all(
        callable(getattr(owner, name, None))
        for owner, name in (
            (runtime, "ensure_worker_state"),
            (runtime, "update_worker_snapshot"),
            (runtime, "mark_failure"),
            (runtime, "warning_throttled"),
            (runtime, "mark_recovery"),
            (auto, "mark_relay_changed"),
            (auto, "mode_uses_auto_logic"),
        )
    )


def is_shelly_lifecycle_host(value: object) -> TypeGuard[ShellyLifecycleHost]:
    runtime = getattr(value, "runtime", None)
    return all(
        callable(getattr(runtime, name, None))
        for name in ("ensure_worker_state", "warning_throttled", "ensure_auto_input_helper")
    )


def require_shelly_request_host(value: object) -> ShellyRequestHost:
    if is_shelly_request_host(value):
        return value
    raise TypeError("Shelly request component requires HTTP/RPC configuration and a session")


def require_shelly_capability_host(value: object) -> ShellyCapabilityHost:
    if is_shelly_capability_host(value):
        return value
    raise TypeError("Shelly capability component requires warning and readback-store ports")


def require_shelly_runtime_host(value: object) -> ShellyRuntimeHost:
    if is_shelly_runtime_host(value):
        return value
    raise TypeError("Shelly runtime component requires clock, health, and readback-store ports")


def require_shelly_readback_cache_host(value: object) -> ShellyReadbackCacheHost:
    if is_shelly_readback_cache_host(value):
        return value
    raise TypeError("Shelly readback cache requires an atomic readback-store port")


def require_shelly_readback_host(value: object) -> ShellyReadbackHost:
    if is_shelly_readback_host(value):
        return value
    raise TypeError("Shelly readback component requires freshness and phase-selection configuration")


def require_shelly_transport_host(value: object) -> ShellyTransportHost:
    if is_shelly_transport_host(value):
        return value
    raise TypeError("Shelly transport component requires session and recovery ports")


def require_shelly_worker_host(value: object) -> ShellyWorkerHost:
    if is_shelly_worker_host(value):
        return value
    raise TypeError("Shelly worker component requires queue, snapshot, health, and clock ports")


def require_shelly_lifecycle_host(value: object) -> ShellyLifecycleHost:
    if is_shelly_lifecycle_host(value):
        return value
    raise TypeError("Shelly lifecycle component requires worker-state and helper-process ports")


__all__ = [
    "ShellyAutoOperations",
    "ShellyCapabilityHost",
    "ShellyLifecycleHost",
    "ShellyRequestHost",
    "ShellyReadbackCacheHost",
    "ShellyReadbackHost",
    "ShellyRuntimeHost",
    "ShellyRuntimeOperations",
    "ShellyTransportHost",
    "ShellyWorkerHost",
    "is_shelly_capability_host",
    "is_shelly_lifecycle_host",
    "is_shelly_readback_cache_host",
    "is_shelly_readback_host",
    "is_shelly_request_host",
    "is_shelly_runtime_host",
    "is_shelly_transport_host",
    "is_shelly_worker_host",
    "require_shelly_capability_host",
    "require_shelly_lifecycle_host",
    "require_shelly_request_host",
    "require_shelly_readback_cache_host",
    "require_shelly_readback_host",
    "require_shelly_runtime_host",
    "require_shelly_transport_host",
    "require_shelly_worker_host",
]
