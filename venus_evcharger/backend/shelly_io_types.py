# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared types and small helpers for Shelly I/O support."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Protocol, TypeGuard, TypedDict

from requests.auth import HTTPDigestAuth

from venus_evcharger.backend.models import (
    ChargerState,
    MeterReading,
    PhaseSelection,
    SwitchCapabilities,
    SwitchState,
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


def is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    """Return whether one dynamic value is a plain JSON-style object."""
    return isinstance(value, dict)


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Return whether one dynamic value exposes an object-keyed mapping."""
    return isinstance(value, Mapping)


def is_object_sequence(value: object) -> TypeGuard[tuple[object, ...] | list[object]]:
    """Return whether one dynamic value is a tuple/list of boundary objects."""
    return isinstance(value, (tuple, list))


def normalized_json_object(value: object, *, error_message: str) -> JsonObject:
    """Normalize one dynamic JSON object or reject the boundary value."""
    if not is_object_dict(value):
        raise ValueError(error_message)
    return {str(key): item for key, item in value.items()}


def optional_json_object(value: object) -> JsonObject | None:
    """Normalize one optional dynamic JSON object without accepting scalars."""
    if not is_object_dict(value):
        return None
    return {str(key): item for key, item in value.items()}


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


class ShellyHttpResponse(Protocol):
    """Response surface consumed by Shelly HTTP/RPC requests."""

    def raise_for_status(self) -> None: ...  # pragma: no cover

    def json(self) -> object: ...  # pragma: no cover


class ShellyHttpSession(Protocol):
    """Structurally requests-compatible HTTP session used by Shelly adapters."""

    def get(
        self,
        *,
        url: str,
        timeout: float,
        auth: HTTPDigestAuth | tuple[str, str] | None = None,
    ) -> ShellyHttpResponse: ...  # pragma: no cover


class _WorkerStopEventLike(Protocol):
    """Threading event subset used by the background I/O worker."""

    def is_set(self) -> bool: ...  # pragma: no cover

    def wait(self, timeout: float) -> bool: ...  # pragma: no cover


class _WorkerThreadLike(Protocol):
    """Thread subset used for the optional background I/O worker."""

    def is_alive(self) -> bool: ...  # pragma: no cover

    def start(self) -> None: ...  # pragma: no cover


class _LockLike(Protocol):
    """Context-manager lock subset used for relay command state."""

    def __enter__(self) -> object: ...  # pragma: no cover

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...  # pragma: no cover


class _CloseableLike(Protocol):
    """Object subset for sessions/transports that can be closed."""

    def close(self) -> None: ...  # pragma: no cover


class _SettableEventLike(Protocol):
    """Object subset for event-like objects that can be set."""

    def set(self) -> None: ...  # pragma: no cover


class _MeterBackendLike(Protocol):
    """Meter backend subset consumed by Shelly I/O split mode."""

    def read_meter(self) -> MeterReading: ...  # pragma: no cover


class _EnableBackendLike(Protocol):
    """Backend subset that can switch charging output on or off."""

    def set_enabled(self, enabled: bool) -> None: ...  # pragma: no cover


class _PhaseSelectionBackendLike(Protocol):
    """Backend subset that can apply one phase-selection target."""

    def set_phase_selection(self, selection: PhaseSelection) -> None: ...  # pragma: no cover


class _SwitchStateBackendLike(Protocol):
    """Switch backend subset that can read normalized switch state."""

    def read_switch_state(self) -> SwitchState: ...  # pragma: no cover


class _SwitchCapabilitiesBackendLike(Protocol):
    """Switch backend subset that exposes normalized capabilities."""

    def capabilities(self) -> SwitchCapabilities: ...  # pragma: no cover


class _ChargerStateBackendLike(Protocol):
    """Charger backend subset that can read normalized charger state."""

    def read_charger_state(self) -> ChargerState: ...  # pragma: no cover


class _TransportSessionResetBackendLike(Protocol):
    """Backend subset that accepts a new shared transport session."""

    def reset_transport_session(self, session: ShellyHttpSession) -> None: ...  # pragma: no cover


def is_meter_backend(value: object) -> TypeGuard[_MeterBackendLike]:
    """Return whether one object exposes the meter-backend capability."""
    return callable(getattr(value, "read_meter", None))


def is_enable_backend(value: object) -> TypeGuard[_EnableBackendLike]:
    """Return whether one object exposes the enable/disable capability."""
    return callable(getattr(value, "set_enabled", None))


def is_phase_selection_backend(value: object) -> TypeGuard[_PhaseSelectionBackendLike]:
    """Return whether one object exposes the phase-selection capability."""
    return callable(getattr(value, "set_phase_selection", None))


def is_switch_state_backend(value: object) -> TypeGuard[_SwitchStateBackendLike]:
    """Return whether one object exposes normalized switch-state readback."""
    return callable(getattr(value, "read_switch_state", None))


def is_switch_capabilities_backend(value: object) -> TypeGuard[_SwitchCapabilitiesBackendLike]:
    """Return whether one object exposes normalized switch capabilities."""
    return callable(getattr(value, "capabilities", None))


def is_charger_state_backend(value: object) -> TypeGuard[_ChargerStateBackendLike]:
    """Return whether one object exposes normalized charger-state readback."""
    return callable(getattr(value, "read_charger_state", None))


def is_transport_session_reset_backend(value: object) -> TypeGuard[_TransportSessionResetBackendLike]:
    """Return whether one object accepts replacement transport sessions."""
    return callable(getattr(value, "reset_transport_session", None))


def is_closeable(value: object) -> TypeGuard[_CloseableLike]:
    """Return whether one object exposes a close method."""
    return callable(getattr(value, "close", None))


def is_settable_event(value: object) -> TypeGuard[_SettableEventLike]:
    """Return whether one object exposes an event-style set method."""
    return callable(getattr(value, "set", None))


def is_session_like(value: object) -> TypeGuard[ShellyHttpSession]:
    """Return whether one dynamic object exposes the requests-session subset."""
    return callable(getattr(value, "get", None))


def require_session(value: object) -> ShellyHttpSession:
    """Return one requests-like session or reject the dynamic boundary."""
    if is_session_like(value):
        return value
    raise TypeError("Shelly session must expose get()")


class _RequestAuthKwargs(TypedDict, total=False):
    """Optional auth kwargs accepted by ``requests.Session.get``."""

    auth: HTTPDigestAuth | tuple[str, str]


class _RequestKwargs(_RequestAuthKwargs):
    """Common kwargs used for main and worker Shelly HTTP requests."""

    url: str
    timeout: float


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
    "ShellyPmStatus",
    "ShellyHttpResponse",
    "ShellyHttpSession",
    "ShellyRpcScalar",
    "_RequestAuthKwargs",
    "_RequestKwargs",
    "_ChargerStateBackendLike",
    "_CloseableLike",
    "_EnableBackendLike",
    "_LockLike",
    "_MeterBackendLike",
    "_PhaseSelectionBackendLike",
    "_SettableEventLike",
    "_SwitchCapabilitiesBackendLike",
    "_SwitchStateBackendLike",
    "_TransportSessionResetBackendLike",
    "_WorkerStopEventLike",
    "_WorkerThreadLike",
    "_distributed_phase_vector",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
    "is_object_dict",
    "is_object_mapping",
    "is_object_sequence",
    "normalize_phase_value",
    "normalized_json_object",
    "normalize_supported_phase_tuple",
    "optional_json_object",
    "is_session_like",
    "require_session",
]
