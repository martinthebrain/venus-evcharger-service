# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared types and small helpers for Shelly I/O support."""

from __future__ import annotations

from typing import Protocol, TypedDict

from requests.auth import HTTPDigestAuth

from venus_evcharger.backend.models import (
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from venus_evcharger.backend.shelly_support_phase import (
    _distributed_phase_vector,
    _single_phase_vector,
    phase_currents_for_selection as _phase_currents_for_selection,
    phase_powers_for_selection as _phase_powers_for_selection,
)


JsonObject = dict[str, object]
ShellyRpcScalar = str | int | float | bool
EncodedRpcScalar = str | int | float
PendingRelayCommand = tuple[bool | None, float | None]


class ShellyEnergyData(TypedDict, total=False):
    """Known Shelly energy counters used by the wallbox service."""

    total: float


class ShellyPmStatus(TypedDict, total=False):
    """Known Shelly PM fields consumed by the wallbox service."""

    output: bool
    apower: float
    current: float
    voltage: float
    aenergy: ShellyEnergyData
    _pm_confirmed: bool
    _phase_selection: str
    _phase_powers_w: tuple[float, float, float]
    _phase_currents_a: tuple[float, float, float]


class _ResponseLike(Protocol):
    """Small protocol for requests-like responses used in tests and runtime."""

    def raise_for_status(self) -> None: ...  # pragma: no cover

    def json(self) -> object: ...  # pragma: no cover


class _SessionLike(Protocol):
    """Requests-session subset used by the Shelly I/O controller."""

    def get(self, **kwargs: object) -> _ResponseLike: ...  # pragma: no cover


class _WorkerStopEventLike(Protocol):
    """Threading event subset used by the background I/O worker."""

    def is_set(self) -> bool: ...  # pragma: no cover

    def wait(self, timeout: float) -> bool: ...  # pragma: no cover


class _WorkerThreadLike(Protocol):
    """Thread subset used for the optional background I/O worker."""

    def is_alive(self) -> bool: ...  # pragma: no cover

    def start(self) -> None: ...  # pragma: no cover


class _RequestAuthKwargs(TypedDict, total=False):
    """Optional auth kwargs accepted by ``requests.Session.get``."""

    auth: HTTPDigestAuth | tuple[str, str]


class _RequestKwargs(_RequestAuthKwargs):
    """Common kwargs used for main and worker Shelly HTTP requests."""

    url: str
    timeout: float


class ShellyIoHost(Protocol):
    """Host attributes and callbacks required by ``ShellyIoController``."""

    session: _SessionLike
    use_digest_auth: bool
    username: str
    password: str
    host: str
    shelly_request_timeout_seconds: float
    pm_component: str
    pm_id: int
    _worker_session: _SessionLike | object
    auto_shelly_soft_fail_seconds: float
    virtual_mode: int
    _worker_poll_interval_seconds: float
    _worker_stop_event: _WorkerStopEventLike
    _worker_thread: _WorkerThreadLike | None
    _last_pm_status: ShellyPmStatus | JsonObject | None
    _last_pm_status_at: float | None
    _last_pm_status_confirmed: bool
    _last_voltage: float | None
    _relay_command_lock: object
    _pending_relay_state: bool | None
    _pending_relay_requested_at: float | None
    relay_sync_timeout_seconds: float
    _relay_sync_expected_state: bool | None
    _relay_sync_requested_at: float | None
    _relay_sync_deadline_at: float | None
    _relay_sync_failure_reported: bool
    supported_phase_selections: tuple[str, ...]
    requested_phase_selection: str
    active_phase_selection: str
    virtual_startstop: int
    virtual_enable: int
    virtual_set_current: float
    _last_charger_state_enabled: bool | None
    _last_charger_state_current_amps: float | None
    _last_charger_state_phase_selection: PhaseSelection | None
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
    _last_switch_feedback_closed: bool | None
    _last_switch_interlock_ok: bool | None
    _last_switch_feedback_at: float | None
    _charger_target_current_amps: float | None
    _charger_target_current_applied_at: float | None

    def _time_now(self) -> float: ...  # pragma: no cover

    def _request(self, url: str) -> JsonObject: ...  # pragma: no cover

    def _request_with_session(self, session: object, url: str) -> JsonObject: ...  # pragma: no cover

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject: ...  # pragma: no cover

    def _rpc_call_with_session(
        self,
        session: object,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject: ...  # pragma: no cover

    def _build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus: ...  # pragma: no cover

    def _publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus: ...  # pragma: no cover

    def _peek_pending_relay_command(self) -> PendingRelayCommand: ...  # pragma: no cover

    def _clear_pending_relay_command(self, relay_on: bool) -> None: ...  # pragma: no cover

    def _worker_fetch_pm_status(self) -> JsonObject: ...  # pragma: no cover

    def _worker_apply_pending_relay_command(self) -> None: ...  # pragma: no cover

    def _ensure_worker_state(self) -> None: ...  # pragma: no cover

    def _update_worker_snapshot(self, **fields: object) -> None: ...  # pragma: no cover

    def _mark_failure(self, source_key: str) -> None: ...  # pragma: no cover

    def _warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...  # pragma: no cover

    def _mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...  # pragma: no cover

    def _source_retry_ready(self, source_key: str, now: float) -> bool: ...  # pragma: no cover

    def _delay_source_retry(
        self,
        source_key: str,
        now: float,
        delay_seconds: float | None = None,
    ) -> None: ...  # pragma: no cover

    def _mark_relay_changed(self, relay_on: bool, changed_at: float) -> None: ...  # pragma: no cover

    def _mode_uses_auto_logic(self, mode: object) -> bool: ...  # pragma: no cover

    def _ensure_auto_input_helper_process(self) -> None: ...  # pragma: no cover


def normalize_supported_phase_tuple(
    supported: object,
    default: tuple[PhaseSelection, ...] = ("P1",),
) -> tuple[PhaseSelection, ...]:
    """Expose one shared normalized phase-tuple helper for split modules."""
    return normalize_phase_selection_tuple(supported, default)


def normalize_phase_value(value: object, default: PhaseSelection = "P1") -> PhaseSelection:
    """Expose one shared phase-normalization helper for split modules."""
    return normalize_phase_selection(value, default)


__all__ = [
    "EncodedRpcScalar",
    "JsonObject",
    "PendingRelayCommand",
    "ShellyEnergyData",
    "ShellyIoHost",
    "ShellyPmStatus",
    "ShellyRpcScalar",
    "_RequestAuthKwargs",
    "_RequestKwargs",
    "_ResponseLike",
    "_SessionLike",
    "_WorkerStopEventLike",
    "_WorkerThreadLike",
    "_distributed_phase_vector",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
    "normalize_phase_value",
    "normalize_supported_phase_tuple",
]
