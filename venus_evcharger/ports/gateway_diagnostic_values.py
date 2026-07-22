# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed semantic values carried by a gateway diagnostics snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    bounded_float,
    exact_mapping,
    finite_float,
    member_text,
    non_negative_float,
    non_negative_int,
    text,
)

GatewayDiagnosticStatus = Literal["fresh", "stale", "unavailable", "error", "unknown"]
GatewayDiagnosticFieldName = Literal[
    "operating_mode",
    "charging_enabled",
    "auto_start_enabled",
    "ac_power_w",
    "charger_state_code",
    "decision_reason",
    "decision_state",
    "last_health_reason",
    "runtime_overrides_active",
    "runtime_overrides_source",
]
DiagnosticScalar: TypeAlias = str | int | float | bool | None

GATEWAY_DIAGNOSTIC_FIELD_NAMES = frozenset(
    {
        "operating_mode",
        "charging_enabled",
        "auto_start_enabled",
        "ac_power_w",
        "charger_state_code",
        "decision_reason",
        "decision_state",
        "last_health_reason",
        "runtime_overrides_active",
        "runtime_overrides_source",
    }
)
CRITICAL_GATEWAY_DIAGNOSTIC_FIELDS: tuple[GatewayDiagnosticFieldName, ...] = (
    "operating_mode",
    "charging_enabled",
    "ac_power_w",
)
_DIAGNOSTIC_STATUSES = frozenset({"fresh", "stale", "unavailable", "error", "unknown"})


def gateway_diagnostic_field_name(value: object) -> GatewayDiagnosticFieldName:
    normalized = member_text(value, GATEWAY_DIAGNOSTIC_FIELD_NAMES, "gateway diagnostic name")
    return cast(GatewayDiagnosticFieldName, normalized)


def gateway_diagnostic_status(value: object) -> GatewayDiagnosticStatus:
    normalized = member_text(value, _DIAGNOSTIC_STATUSES, "gateway diagnostic status")
    return cast(GatewayDiagnosticStatus, normalized)


def _operating_mode(value: object) -> int:
    result = non_negative_int(value, "operating_mode value")
    if result not in {0, 1, 2}:
        raise ValueError("operating_mode value must be 0, 1, or 2")
    return result


def _state_code(value: object) -> int:
    return non_negative_int(value, "charger_state_code value")


def _charging_enabled(value: object) -> bool:
    return boolean(value, "charging_enabled value")


def _auto_start_enabled(value: object) -> bool:
    return boolean(value, "auto_start_enabled value")


def _runtime_overrides_active(value: object) -> bool:
    return boolean(value, "runtime_overrides_active value")


def _ac_power(value: object) -> float:
    return finite_float(value, "ac_power_w value")


def _semantic_text(value: object) -> str:
    return text(value, "semantic diagnostic value", allow_empty=True)


_VALUE_READERS: Mapping[GatewayDiagnosticFieldName, Callable[[object], DiagnosticScalar]] = {
    "operating_mode": _operating_mode,
    "charging_enabled": _charging_enabled,
    "auto_start_enabled": _auto_start_enabled,
    "ac_power_w": _ac_power,
    "charger_state_code": _state_code,
    "decision_reason": _semantic_text,
    "decision_state": _semantic_text,
    "last_health_reason": _semantic_text,
    "runtime_overrides_active": _runtime_overrides_active,
    "runtime_overrides_source": _semantic_text,
}


def gateway_diagnostic_value(name: GatewayDiagnosticFieldName, value: object) -> DiagnosticScalar:
    if value is None:
        return None
    return _VALUE_READERS[name](value)


def _validate_sample_quality(
    status: GatewayDiagnosticStatus,
    value: DiagnosticScalar,
    observed_at: float,
    reason_code: str,
) -> None:
    if status in {"fresh", "stale"}:
        _validate_observed_value(status, value, observed_at)
        return
    if value is not None:
        raise ValueError(f"{status} diagnostic must not carry a value")
    if status in {"unavailable", "error"} and not reason_code:
        raise ValueError(f"{status} diagnostic requires reason_code")


def _validate_observed_value(
    status: GatewayDiagnosticStatus,
    value: DiagnosticScalar,
    observed_at: float,
) -> None:
    if value is None:
        raise ValueError(f"{status} diagnostic requires a value and positive observed_at")
    if observed_at <= 0.0:
        raise ValueError(f"{status} diagnostic requires a value and positive observed_at")


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticSample:
    """One named EV-charger diagnostic value with explicit quality metadata."""

    name: GatewayDiagnosticFieldName
    value: DiagnosticScalar
    status: GatewayDiagnosticStatus
    observed_at: float
    confidence: float
    reason_code: str = ""

    def __post_init__(self) -> None:
        name = gateway_diagnostic_field_name(self.name)
        status = gateway_diagnostic_status(self.status)
        value = gateway_diagnostic_value(name, self.value)
        observed_at = non_negative_float(self.observed_at, "diagnostic observed_at")
        bounded_float(self.confidence, "diagnostic confidence", 0.0, 1.0)
        reason = text(self.reason_code, "diagnostic reason_code", allow_empty=True)
        _validate_sample_quality(status, value, observed_at, reason)

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "observed_at": self.observed_at,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayDiagnosticSample:
        names = {"name", "value", "status", "observed_at", "confidence", "reason_code"}
        item = exact_mapping(payload, "gateway diagnostic sample", names)
        name = gateway_diagnostic_field_name(item["name"])
        return cls(
            name=name,
            value=gateway_diagnostic_value(name, item["value"]),
            status=gateway_diagnostic_status(item["status"]),
            observed_at=non_negative_float(item["observed_at"], "diagnostic observed_at"),
            confidence=bounded_float(item["confidence"], "diagnostic confidence", 0.0, 1.0),
            reason_code=text(item["reason_code"], "diagnostic reason_code", allow_empty=True),
        )


__all__ = [
    "CRITICAL_GATEWAY_DIAGNOSTIC_FIELDS",
    "DiagnosticScalar",
    "GATEWAY_DIAGNOSTIC_FIELD_NAMES",
    "GatewayDiagnosticFieldName",
    "GatewayDiagnosticSample",
    "GatewayDiagnosticStatus",
    "gateway_diagnostic_field_name",
    "gateway_diagnostic_status",
    "gateway_diagnostic_value",
]
