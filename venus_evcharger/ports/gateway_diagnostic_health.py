# SPDX-License-Identifier: GPL-3.0-or-later
"""Health and publication contracts for semantic gateway diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    exact_mapping,
    non_negative_float,
    non_negative_int,
    text,
)

GatewayHealthState = Literal["unknown", "ok", "degraded", "protective", "unavailable"]

_HEALTH_STATES = frozenset({"unknown", "ok", "degraded", "protective", "unavailable"})


@dataclass(frozen=True, slots=True)
class GatewayPublicationSummary:
    """Service-level publication heartbeat independent of field changes."""

    registered: bool
    heartbeat_at: float
    stale: bool

    def __post_init__(self) -> None:
        registered = boolean(self.registered, "gateway publication registered")
        heartbeat = non_negative_float(self.heartbeat_at, "gateway publication heartbeat_at")
        boolean(self.stale, "gateway publication stale")
        if registered and heartbeat <= 0.0:
            raise ValueError("registered gateway publication requires positive heartbeat_at")
        if not registered and heartbeat != 0.0:
            raise ValueError("unregistered gateway publication requires heartbeat_at=0")

    def to_payload(self) -> dict[str, object]:
        return {
            "registered": self.registered,
            "heartbeat_at": self.heartbeat_at,
            "stale": self.stale,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayPublicationSummary:
        item = exact_mapping(
            payload,
            "gateway publication summary",
            {"registered", "heartbeat_at", "stale"},
        )
        return cls(
            registered=boolean(item["registered"], "gateway publication registered"),
            heartbeat_at=non_negative_float(
                item["heartbeat_at"],
                "gateway publication heartbeat_at",
            ),
            stale=boolean(item["stale"], "gateway publication stale"),
        )


@dataclass(frozen=True, slots=True)
class GatewayHealthSummary:
    """Operational gateway health without transport-specific details."""

    state: GatewayHealthState
    stale: bool
    timeouts_60s: int
    average_latency_ms: float
    maximum_latency_ms: float
    pending_gateway_commands: int
    pending_core_commands: int
    maximum_event_loop_gap_ms_60s: float
    last_success_at: float
    last_error_code: str = ""

    def __post_init__(self) -> None:
        _health_state(self.state)
        boolean(self.stale, "gateway health stale")
        non_negative_int(self.timeouts_60s, "gateway health timeouts_60s")
        average = non_negative_float(self.average_latency_ms, "gateway health average_latency_ms")
        maximum = non_negative_float(self.maximum_latency_ms, "gateway health maximum_latency_ms")
        if maximum < average:
            raise ValueError(
                "gateway health maximum_latency_ms must be at least average_latency_ms"
            )
        non_negative_int(self.pending_gateway_commands, "gateway health pending_gateway_commands")
        non_negative_int(self.pending_core_commands, "gateway health pending_core_commands")
        non_negative_float(
            self.maximum_event_loop_gap_ms_60s,
            "gateway health maximum_event_loop_gap_ms_60s",
        )
        non_negative_float(self.last_success_at, "gateway health last_success_at")
        text(self.last_error_code, "gateway health last_error_code", allow_empty=True)

    def to_payload(self) -> dict[str, object]:
        return {
            "state": self.state,
            "stale": self.stale,
            "timeouts_60s": self.timeouts_60s,
            "average_latency_ms": self.average_latency_ms,
            "maximum_latency_ms": self.maximum_latency_ms,
            "pending_gateway_commands": self.pending_gateway_commands,
            "pending_core_commands": self.pending_core_commands,
            "maximum_event_loop_gap_ms_60s": self.maximum_event_loop_gap_ms_60s,
            "last_success_at": self.last_success_at,
            "last_error_code": self.last_error_code,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayHealthSummary:
        names = {
            "state",
            "stale",
            "timeouts_60s",
            "average_latency_ms",
            "maximum_latency_ms",
            "pending_gateway_commands",
            "pending_core_commands",
            "maximum_event_loop_gap_ms_60s",
            "last_success_at",
            "last_error_code",
        }
        item = exact_mapping(payload, "gateway health summary", names)
        return cls(
            state=_health_state(item["state"]),
            stale=boolean(item["stale"], "gateway health stale"),
            timeouts_60s=non_negative_int(item["timeouts_60s"], "gateway health timeouts_60s"),
            average_latency_ms=non_negative_float(
                item["average_latency_ms"], "gateway health average_latency_ms"
            ),
            maximum_latency_ms=non_negative_float(
                item["maximum_latency_ms"], "gateway health maximum_latency_ms"
            ),
            pending_gateway_commands=non_negative_int(
                item["pending_gateway_commands"], "gateway health pending_gateway_commands"
            ),
            pending_core_commands=non_negative_int(
                item["pending_core_commands"], "gateway health pending_core_commands"
            ),
            maximum_event_loop_gap_ms_60s=non_negative_float(
                item["maximum_event_loop_gap_ms_60s"],
                "gateway health maximum_event_loop_gap_ms_60s",
            ),
            last_success_at=non_negative_float(
                item["last_success_at"], "gateway health last_success_at"
            ),
            last_error_code=text(
                item["last_error_code"], "gateway health last_error_code", allow_empty=True
            ),
        )


def _health_state(value: object) -> GatewayHealthState:
    if not _is_health_state(value):
        raise ValueError("gateway health state is invalid")
    return value


def _is_health_state(value: object) -> TypeGuard[GatewayHealthState]:
    return isinstance(value, str) and value in _HEALTH_STATES


__all__ = [
    "GatewayHealthState",
    "GatewayHealthSummary",
    "GatewayPublicationSummary",
]
