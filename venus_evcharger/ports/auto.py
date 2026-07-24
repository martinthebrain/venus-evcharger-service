# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit service boundary used by the Auto decision components."""

from __future__ import annotations

from typing import Protocol, TypeGuard

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.core.contracts_basic import non_negative_int, normalize_binary_flag


PendingRelayCommand = tuple[bool | None, float | None]


class AutoDecisionStatePort(Protocol):
    """State operation required by Auto decisions."""

    def save_runtime_state(self) -> object: ...


class AutoDecisionRuntimePort(Protocol):
    """Runtime operations required by Auto decisions."""

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> object: ...

    def pending_relay_command(self) -> object: ...


class AutoDecisionServicePort(Protocol):
    """Exact mutable service surface consumed by :class:`AutoDecisionPort`."""

    virtual_mode: object
    virtual_enable: object
    virtual_autostart: object
    auto_policy: AutoPolicy
    _auto_mode_cutover_pending: object
    _ignore_min_offtime_once: object
    state: AutoDecisionStatePort
    runtime: AutoDecisionRuntimePort


def _is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


def _require_pending_relay_command(value: object) -> PendingRelayCommand:
    if not _is_object_tuple(value):
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


class AutoDecisionPort:
    """Expose Auto side effects without controller callbacks or dynamic forwarding."""

    def __init__(self, service: AutoDecisionServicePort) -> None:
        self.service = service

    def mode(self) -> int:
        return non_negative_int(getattr(self.service, "virtual_mode", 0))

    def controller_enabled(self) -> bool:
        return bool(normalize_binary_flag(getattr(self.service, "virtual_enable", 1), default=1))

    def autostart_enabled(self) -> bool:
        return bool(normalize_binary_flag(getattr(self.service, "virtual_autostart", 1), default=1))

    def auto_policy(self) -> AutoPolicy:
        """Return the canonical Auto policy assembled during bootstrap."""
        policy: AutoPolicy = self.service.auto_policy
        return policy

    def mode_cutover_pending(self) -> bool:
        return getattr(self.service, "_auto_mode_cutover_pending", False) is True

    def minimum_offtime_bypass_active(self) -> bool:
        return getattr(self.service, "_ignore_min_offtime_once", False) is True

    def clear_minimum_offtime_bypass(self) -> None:
        self.service._ignore_min_offtime_once = False

    def reset_mode_cutover(self) -> None:
        self.service._auto_mode_cutover_pending = False
        self.service._ignore_min_offtime_once = False

    def complete_mode_cutover(self) -> None:
        self.service._auto_mode_cutover_pending = False
        self.service._ignore_min_offtime_once = True

    def save_runtime_state(self) -> object:
        return self.service.state.save_runtime_state()

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> object:
        return self.service.runtime.write_auto_audit_event(reason, cached)

    def peek_pending_relay_command(self) -> PendingRelayCommand:
        return _require_pending_relay_command(self.service.runtime.pending_relay_command())
