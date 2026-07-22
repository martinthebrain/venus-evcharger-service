# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated wire contracts for semantic system-gateway operations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ports.gateway_operations import EssSetpointIntent, GxRelayContactMode

GX_RELAY_REFRESH_KIND = "gx_relay_refresh"
GX_RELAY_SET_KIND = "gx_relay_set_enabled"
ESS_GRID_SETPOINT_KIND = "ess_grid_setpoint"
SEMANTIC_GATEWAY_KINDS = frozenset((GX_RELAY_REFRESH_KIND, GX_RELAY_SET_KIND, ESS_GRID_SETPOINT_KIND))

RelayPhase = Literal["manual_read", "manual_write", "output", "verify", "retry"]


@dataclass(frozen=True, slots=True)
class GxRelayRefreshOperation:
    relay_index: int


@dataclass(frozen=True, slots=True)
class GxRelaySetOperation:
    relay_index: int
    contact_mode: GxRelayContactMode
    enabled: bool
    ensure_manual: bool
    verify_settle_seconds: float
    verify_retry_seconds: float
    phase: RelayPhase
    manual_target: int
    retries: int
    not_before: float

    @property
    def target_state(self) -> int:
        energized = self.enabled if self.contact_mode == "NO" else not self.enabled
        return int(energized)


@dataclass(frozen=True, slots=True)
class EssGridSetpointOperation:
    watts: float
    intent: EssSetpointIntent


def gx_relay_state_key(relay_index: int) -> str:
    """Return the transport-neutral cache key for one GX relay state."""
    return f"system:gx-relay:{_relay_index(relay_index)}:state"


def gx_relay_refresh_command(relay_index: int) -> CommandPayload:
    index = _relay_index(relay_index)
    return {
        "kind": GX_RELAY_REFRESH_KIND,
        "source": "gx-relay-backend",
        "relay_index": index,
        "priority": "read",
        "coalesce_key": f"gateway-operation:gx-relay:{index}:refresh",
    }


def gx_relay_set_command(
    relay_index: int,
    contact_mode: GxRelayContactMode,
    enabled: bool,
    *,
    ensure_manual: bool,
    verify_settle_seconds: float,
    verify_retry_seconds: float,
) -> CommandPayload:
    index = _relay_index(relay_index)
    mode = _contact_mode(contact_mode)
    target_enabled = _strict_bool(enabled, "enabled")
    manual = _strict_bool(ensure_manual, "ensure_manual")
    settle_seconds = _non_negative_seconds(verify_settle_seconds)
    retry_seconds = _non_negative_seconds(verify_retry_seconds)
    payload: CommandPayload = {
        "kind": GX_RELAY_SET_KIND,
        "source": "gx-relay-backend",
        "relay_index": index,
        "contact_mode": mode,
        "enabled": target_enabled,
        "ensure_manual": manual,
        "verify_settle_seconds": settle_seconds,
        "verify_retry_seconds": retry_seconds,
        "phase": "manual_read" if manual else "output",
        "retries": 0,
        "not_before": 0.0,
        "priority": "user" if target_enabled else "safety",
        "coalesce_key": f"gateway-operation:gx-relay:{index}:enabled",
    }
    if target_enabled:
        payload["deadline_s"] = max(10.0, (2.0 * settle_seconds) + retry_seconds + 5.0)
    return payload


def ess_grid_setpoint_command(watts: float, *, intent: EssSetpointIntent) -> CommandPayload:
    normalized_intent = _ess_intent(intent)
    return {
        "kind": ESS_GRID_SETPOINT_KIND,
        "source": "victron-ess-balance",
        "watts": _finite_float(watts, "watts"),
        "intent": normalized_intent,
        "priority": "safety" if normalized_intent == "restore" else "user",
        "coalesce_key": "gateway-operation:ess-grid-setpoint",
    }


def parse_gx_relay_refresh(command: CommandMapping) -> GxRelayRefreshOperation | None:
    if command.get("kind") != GX_RELAY_REFRESH_KIND:
        return None
    index = _parsed_relay_index(command.get("relay_index"))
    return None if index is None else GxRelayRefreshOperation(index)


def parse_gx_relay_set(command: CommandMapping) -> GxRelaySetOperation | None:
    if command.get("kind") != GX_RELAY_SET_KIND:
        return None
    identity = _parsed_relay_set_identity(command)
    timing = _parsed_relay_set_timing(command)
    progress = _parsed_relay_set_progress(command)
    if identity is None or timing is None or progress is None:
        return None
    index, mode, phase, enabled, ensure_manual = identity
    settle, retry, not_before = timing
    manual_target, retries = progress
    return GxRelaySetOperation(
        index,
        mode,
        enabled,
        ensure_manual,
        settle,
        retry,
        phase,
        manual_target,
        retries,
        not_before,
    )


def _parsed_relay_set_identity(
    command: CommandMapping,
) -> tuple[int, GxRelayContactMode, RelayPhase, bool, bool] | None:
    index = _parsed_relay_index(command.get("relay_index"))
    mode = command.get("contact_mode")
    phase = command.get("phase")
    enabled = command.get("enabled")
    ensure_manual = command.get("ensure_manual")
    valid = all(
        (
            index is not None,
            _is_contact_mode(mode),
            _is_relay_phase(phase),
            type(enabled) is bool,
            type(ensure_manual) is bool,
        )
    )
    if not valid:
        return None
    assert index is not None and _is_contact_mode(mode) and _is_relay_phase(phase)
    assert type(enabled) is bool and type(ensure_manual) is bool
    return index, mode, phase, enabled, ensure_manual


def _parsed_relay_set_timing(command: CommandMapping) -> tuple[float, float, float] | None:
    settle = _parsed_non_negative_float(command.get("verify_settle_seconds"))
    retry = _parsed_non_negative_float(command.get("verify_retry_seconds"))
    not_before = _parsed_non_negative_float(command.get("not_before"))
    if settle is None or retry is None or not_before is None:
        return None
    return settle, retry, not_before


def _parsed_relay_set_progress(command: CommandMapping) -> tuple[int, int] | None:
    retries = command.get("retries")
    manual_target = command.get("manual_target", 0)
    valid = all(
        (
            type(manual_target) is int,
            manual_target in (0, 1),
            type(retries) is int,
            isinstance(retries, int) and retries >= 0,
        )
    )
    if not valid:
        return None
    assert type(manual_target) is int and type(retries) is int
    return manual_target, retries


def parse_ess_grid_setpoint(command: CommandMapping) -> EssGridSetpointOperation | None:
    if command.get("kind") != ESS_GRID_SETPOINT_KIND:
        return None
    watts = _parsed_finite_float(command.get("watts"))
    intent = command.get("intent")
    if watts is None or not _is_ess_intent(intent):
        return None
    return EssGridSetpointOperation(watts, intent)


def _relay_index(value: int) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError("GX relay index must be 0 or 1")
    return value


def _parsed_relay_index(value: object) -> int | None:
    return value if type(value) is int and value in (0, 1) else None


def _contact_mode(value: object) -> GxRelayContactMode:
    if not _is_contact_mode(value):
        raise ValueError("GX relay contact mode must be NO or NC")
    return value


def _ess_intent(value: object) -> EssSetpointIntent:
    if not _is_ess_intent(value):
        raise ValueError("ESS setpoint intent must be tracking or restore")
    return value


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field} must be bool")
    return value


def _non_negative_seconds(value: object) -> float:
    seconds = _finite_float(value, "seconds")
    if seconds < 0.0:
        raise ValueError("seconds must be non-negative")
    return seconds


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    return normalized


def _parsed_finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _parsed_non_negative_float(value: object) -> float | None:
    normalized = _parsed_finite_float(value)
    return normalized if normalized is not None and normalized >= 0.0 else None


def _is_contact_mode(value: object) -> TypeGuard[GxRelayContactMode]:
    return isinstance(value, str) and value in ("NO", "NC")


def _is_ess_intent(value: object) -> TypeGuard[EssSetpointIntent]:
    return isinstance(value, str) and value in ("tracking", "restore")


def _is_relay_phase(value: object) -> TypeGuard[RelayPhase]:
    return isinstance(value, str) and value in ("manual_read", "manual_write", "output", "verify", "retry")
