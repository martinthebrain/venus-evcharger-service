# SPDX-License-Identifier: GPL-3.0-or-later
"""Health and publication contracts for semantic gateway diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    exact_mapping,
    non_negative_float,
    non_negative_int,
    text,
)
from venus_evcharger.ports.gateway_diagnostic_resource import (
    GatewayResourceState,
    ResourcePressureSummary,
    resource_state as _resource_state,
)

GatewayHealthState = Literal["unknown", "ok", "degraded", "protective", "unavailable"]
_HealthContract = Literal["base", "trigger", "resource"]

_HEALTH_STATES = frozenset({"unknown", "ok", "degraded", "protective", "unavailable"})
_HEALTH_BASE_FIELDS = {
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
_HEALTH_TRIGGER_FIELDS = {
    "active_protective_trigger",
    "last_protective_trigger",
}
_HEALTH_RESOURCE_FIELDS = {
    "operational_state",
    "performance_state",
    "resource_state",
    "protective_cause",
    "resource_evidence",
}


@dataclass(frozen=True, slots=True)
class ProtectiveTriggerSummary:
    """Bounded evidence explaining one gateway protective transition."""

    triggered_at: float
    protective_until: float
    timeout_count_60s: int
    operation_kind: str
    source: str
    error_code: str
    latency_ms: float | None

    def __post_init__(self) -> None:
        triggered_at = non_negative_float(
            self.triggered_at,
            "gateway protective trigger triggered_at",
        )
        protective_until = non_negative_float(
            self.protective_until,
            "gateway protective trigger protective_until",
        )
        if triggered_at <= 0.0:
            raise ValueError("gateway protective trigger requires positive triggered_at")
        if protective_until < triggered_at:
            raise ValueError("gateway protective trigger protective_until precedes triggered_at")
        if (
            non_negative_int(
                self.timeout_count_60s,
                "gateway protective trigger timeout_count_60s",
            )
            <= 0
        ):
            raise ValueError("gateway protective trigger timeout_count_60s must be positive")
        text(self.operation_kind, "gateway protective trigger operation_kind")
        text(self.source, "gateway protective trigger source", allow_empty=True)
        text(self.error_code, "gateway protective trigger error_code")
        if self.latency_ms is not None:
            non_negative_float(
                self.latency_ms,
                "gateway protective trigger latency_ms",
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "triggered_at": self.triggered_at,
            "protective_until": self.protective_until,
            "timeout_count_60s": self.timeout_count_60s,
            "operation_kind": self.operation_kind,
            "source": self.source,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_payload(cls, payload: object) -> ProtectiveTriggerSummary:
        item = exact_mapping(
            payload,
            "gateway protective trigger",
            {
                "triggered_at",
                "protective_until",
                "timeout_count_60s",
                "operation_kind",
                "source",
                "error_code",
                "latency_ms",
            },
        )
        latency = item["latency_ms"]
        return cls(
            triggered_at=non_negative_float(
                item["triggered_at"],
                "gateway protective trigger triggered_at",
            ),
            protective_until=non_negative_float(
                item["protective_until"],
                "gateway protective trigger protective_until",
            ),
            timeout_count_60s=non_negative_int(
                item["timeout_count_60s"],
                "gateway protective trigger timeout_count_60s",
            ),
            operation_kind=text(
                item["operation_kind"],
                "gateway protective trigger operation_kind",
            ),
            source=text(
                item["source"],
                "gateway protective trigger source",
                allow_empty=True,
            ),
            error_code=text(
                item["error_code"],
                "gateway protective trigger error_code",
            ),
            latency_ms=(
                None
                if latency is None
                else non_negative_float(
                    latency,
                    "gateway protective trigger latency_ms",
                )
            ),
        )


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
    active_protective_trigger: ProtectiveTriggerSummary | None = None
    last_protective_trigger: ProtectiveTriggerSummary | None = None
    operational_state: GatewayHealthState = "unknown"
    performance_state: GatewayHealthState = "unknown"
    resource_state: GatewayResourceState = "unknown"
    protective_cause: str = ""
    resource_evidence: ResourcePressureSummary | None = None

    def __post_init__(self) -> None:
        _validate_health_measurements(self)
        _validate_health_triggers(self)
        _validate_health_states(self)
        _validate_health_resource_evidence(self)

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
            "active_protective_trigger": _trigger_payload(self.active_protective_trigger),
            "last_protective_trigger": _trigger_payload(self.last_protective_trigger),
            "operational_state": self.operational_state,
            "performance_state": self.performance_state,
            "resource_state": self.resource_state,
            "protective_cause": self.protective_cause,
            "resource_evidence": (None if self.resource_evidence is None else self.resource_evidence.to_payload()),
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayHealthSummary:
        contract, expected = _health_contract(payload)
        item = exact_mapping(payload, "gateway health summary", expected)
        (
            active_trigger,
            last_trigger,
            operational_state,
            performance_state,
            resource_state,
            protective_cause,
            resource_evidence,
        ) = _health_extensions(item, contract)
        return cls(
            state=_health_state(item["state"]),
            stale=boolean(item["stale"], "gateway health stale"),
            timeouts_60s=non_negative_int(item["timeouts_60s"], "gateway health timeouts_60s"),
            average_latency_ms=non_negative_float(item["average_latency_ms"], "gateway health average_latency_ms"),
            maximum_latency_ms=non_negative_float(item["maximum_latency_ms"], "gateway health maximum_latency_ms"),
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
            last_success_at=non_negative_float(item["last_success_at"], "gateway health last_success_at"),
            last_error_code=text(item["last_error_code"], "gateway health last_error_code", allow_empty=True),
            active_protective_trigger=active_trigger,
            last_protective_trigger=last_trigger,
            operational_state=operational_state,
            performance_state=performance_state,
            resource_state=resource_state,
            protective_cause=protective_cause,
            resource_evidence=resource_evidence,
        )


def _validate_health_measurements(value: GatewayHealthSummary) -> None:
    _health_state(value.state)
    boolean(value.stale, "gateway health stale")
    non_negative_int(value.timeouts_60s, "gateway health timeouts_60s")
    average = non_negative_float(
        value.average_latency_ms,
        "gateway health average_latency_ms",
    )
    maximum = non_negative_float(
        value.maximum_latency_ms,
        "gateway health maximum_latency_ms",
    )
    if maximum < average:
        raise ValueError("gateway health maximum_latency_ms must be at least average_latency_ms")
    non_negative_int(
        value.pending_gateway_commands,
        "gateway health pending_gateway_commands",
    )
    non_negative_int(value.pending_core_commands, "gateway health pending_core_commands")
    non_negative_float(
        value.maximum_event_loop_gap_ms_60s,
        "gateway health maximum_event_loop_gap_ms_60s",
    )
    non_negative_float(value.last_success_at, "gateway health last_success_at")
    text(value.last_error_code, "gateway health last_error_code", allow_empty=True)


def _validate_health_triggers(value: GatewayHealthSummary) -> None:
    _optional_protective_trigger(
        value.active_protective_trigger,
        "gateway health active_protective_trigger",
    )
    _optional_protective_trigger(
        value.last_protective_trigger,
        "gateway health last_protective_trigger",
    )
    active = value.active_protective_trigger
    if active is not None and active != value.last_protective_trigger:
        raise ValueError("active gateway protective trigger must equal last_protective_trigger")


def _validate_health_states(value: GatewayHealthSummary) -> None:
    _health_state(value.operational_state)
    _health_state(value.performance_state)
    _resource_state(value.resource_state)
    text(value.protective_cause, "gateway health protective_cause", allow_empty=True)
    if value.state != "protective" and value.protective_cause:
        raise ValueError("non-protective gateway health cannot have protective_cause")


def _validate_health_resource_evidence(value: GatewayHealthSummary) -> None:
    evidence = value.resource_evidence
    _validate_resource_evidence_type(evidence)
    _validate_active_resource_evidence(evidence, value.resource_state)


def _validate_resource_evidence_type(evidence: object) -> None:
    if evidence is not None and not isinstance(evidence, ResourcePressureSummary):
        raise TypeError("gateway health resource_evidence must be ResourcePressureSummary or None")


def _validate_active_resource_evidence(
    evidence: ResourcePressureSummary | None,
    resource_state: GatewayResourceState,
) -> None:
    if evidence is not None and evidence.active and resource_state != "constrained":
        raise ValueError("active gateway resource evidence requires constrained resource_state")


def _health_contract(payload: object) -> tuple[_HealthContract, set[str]]:
    names = set(payload) if isinstance(payload, Mapping) else set()
    trigger_fields = _HEALTH_BASE_FIELDS | _HEALTH_TRIGGER_FIELDS
    if names == _HEALTH_BASE_FIELDS:
        return "base", set(_HEALTH_BASE_FIELDS)
    if names == trigger_fields:
        return "trigger", trigger_fields
    return "resource", trigger_fields | _HEALTH_RESOURCE_FIELDS


def _health_extensions(
    item: Mapping[str, object],
    contract: _HealthContract,
) -> tuple[
    ProtectiveTriggerSummary | None,
    ProtectiveTriggerSummary | None,
    GatewayHealthState,
    GatewayHealthState,
    GatewayResourceState,
    str,
    ResourcePressureSummary | None,
]:
    if contract == "base":
        return None, None, "unknown", "unknown", "unknown", "", None
    active = _protective_trigger(item["active_protective_trigger"])
    latest = _protective_trigger(item["last_protective_trigger"])
    if contract == "trigger":
        return active, latest, "unknown", "unknown", "unknown", "", None
    return (
        active,
        latest,
        _health_state(item["operational_state"]),
        _health_state(item["performance_state"]),
        _resource_state(item["resource_state"]),
        text(
            item["protective_cause"],
            "gateway health protective_cause",
            allow_empty=True,
        ),
        _resource_evidence(item["resource_evidence"]),
    )


def _resource_evidence(value: object) -> ResourcePressureSummary | None:
    return None if value is None else ResourcePressureSummary.from_payload(value)


def _optional_protective_trigger(value: object, label: str) -> None:
    if value is not None and not isinstance(value, ProtectiveTriggerSummary):
        raise TypeError(f"{label} must be ProtectiveTriggerSummary or None")


def _protective_trigger(value: object) -> ProtectiveTriggerSummary | None:
    return None if value is None else ProtectiveTriggerSummary.from_payload(value)


def _trigger_payload(value: ProtectiveTriggerSummary | None) -> object:
    return None if value is None else value.to_payload()


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
    "GatewayResourceState",
    "ProtectiveTriggerSummary",
    "ResourcePressureSummary",
]
