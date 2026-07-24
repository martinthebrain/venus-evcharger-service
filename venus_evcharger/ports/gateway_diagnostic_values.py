# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed semantic values carried by a gateway diagnostics snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypeGuard

from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    bounded_float,
    exact_mapping,
    finite_float,
    is_string_object_mapping,
    non_negative_float,
    non_negative_int,
    text,
)

GatewayDiagnosticStatus = Literal[
    "fresh",
    "stale",
    "inactive",
    "unavailable",
    "error",
    "unknown",
]
GatewayDiagnosticApplicability = Literal["applicable", "not-applicable", "unknown"]
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
_DIAGNOSTIC_STATUSES = frozenset(
    {"fresh", "stale", "inactive", "unavailable", "error", "unknown"}
)
_DIAGNOSTIC_APPLICABILITIES = frozenset({"applicable", "not-applicable", "unknown"})
_STATUS_APPLICABILITY: Mapping[
    GatewayDiagnosticStatus,
    GatewayDiagnosticApplicability,
] = {
    "fresh": "applicable",
    "stale": "applicable",
    "inactive": "not-applicable",
    "unavailable": "applicable",
    "error": "applicable",
    "unknown": "unknown",
}


def gateway_diagnostic_field_name(value: object) -> GatewayDiagnosticFieldName:
    if not _is_gateway_diagnostic_field_name(value):
        raise ValueError("gateway diagnostic name is invalid")
    return value


def _is_gateway_diagnostic_field_name(
    value: object,
) -> TypeGuard[GatewayDiagnosticFieldName]:
    return isinstance(value, str) and value in GATEWAY_DIAGNOSTIC_FIELD_NAMES


def gateway_diagnostic_status(value: object) -> GatewayDiagnosticStatus:
    if not _is_gateway_diagnostic_status(value):
        raise ValueError("gateway diagnostic status is invalid")
    return value


def _is_gateway_diagnostic_status(value: object) -> TypeGuard[GatewayDiagnosticStatus]:
    return isinstance(value, str) and value in _DIAGNOSTIC_STATUSES


def gateway_diagnostic_applicability(value: object) -> GatewayDiagnosticApplicability:
    if not _is_gateway_diagnostic_applicability(value):
        raise ValueError("gateway diagnostic applicability is invalid")
    return value


def _is_gateway_diagnostic_applicability(
    value: object,
) -> TypeGuard[GatewayDiagnosticApplicability]:
    return isinstance(value, str) and value in _DIAGNOSTIC_APPLICABILITIES


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
    changed_at: float,
    confirmed_at: float,
    applicability: GatewayDiagnosticApplicability,
    reason_code: str,
) -> None:
    _validate_status_applicability(status, applicability)
    if status in {"fresh", "stale"}:
        _validate_observed_value(status, value, changed_at, confirmed_at)
    elif status == "inactive":
        _validate_inactive_quality(value, changed_at, confirmed_at, reason_code)
    else:
        _validate_unobserved_quality(status, value, reason_code)
    _validate_timestamp_order(changed_at, confirmed_at)


def _validate_timestamp_order(changed_at: float, confirmed_at: float) -> None:
    if changed_at > confirmed_at:
        raise ValueError("diagnostic changed_at must not exceed confirmed_at")


def _validate_status_applicability(
    status: GatewayDiagnosticStatus,
    applicability: GatewayDiagnosticApplicability,
) -> None:
    expected = _STATUS_APPLICABILITY[status]
    if applicability != expected:
        raise ValueError(f"{status} diagnostic requires applicability={expected!r}")


def _validate_inactive_quality(
    value: DiagnosticScalar,
    changed_at: float,
    confirmed_at: float,
    reason_code: str,
) -> None:
    if value is not None:
        _validate_observed_value("inactive", value, changed_at, confirmed_at)
    if not reason_code:
        raise ValueError("inactive diagnostic requires reason_code")


def _validate_unobserved_quality(
    status: GatewayDiagnosticStatus,
    value: DiagnosticScalar,
    reason_code: str,
) -> None:
    _validate_unobserved_value(status, value)
    _validate_unobserved_reason(status, reason_code)


def _validate_unobserved_value(
    status: GatewayDiagnosticStatus,
    value: DiagnosticScalar,
) -> None:
    if value is not None:
        raise ValueError(f"{status} diagnostic must not carry a value")


def _validate_unobserved_reason(status: GatewayDiagnosticStatus, reason_code: str) -> None:
    if status in {"unavailable", "error"} and not reason_code:
        raise ValueError(f"{status} diagnostic requires reason_code")


def _validate_observed_value(
    status: GatewayDiagnosticStatus,
    value: DiagnosticScalar,
    changed_at: float,
    confirmed_at: float,
) -> None:
    if value is None:
        raise ValueError(f"{status} diagnostic requires a value and positive timestamps")
    if changed_at <= 0.0 or confirmed_at <= 0.0:
        raise ValueError(f"{status} diagnostic requires a value and positive timestamps")


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticSample:
    """One named EV-charger diagnostic value with explicit quality metadata."""

    name: GatewayDiagnosticFieldName
    value: DiagnosticScalar
    status: GatewayDiagnosticStatus
    changed_at: float
    confirmed_at: float
    confidence: float
    applicability: GatewayDiagnosticApplicability = "applicable"
    reason_code: str = ""

    def __post_init__(self) -> None:
        name = gateway_diagnostic_field_name(self.name)
        status = gateway_diagnostic_status(self.status)
        value = gateway_diagnostic_value(name, self.value)
        changed_at = non_negative_float(self.changed_at, "diagnostic changed_at")
        confirmed_at = non_negative_float(self.confirmed_at, "diagnostic confirmed_at")
        bounded_float(self.confidence, "diagnostic confidence", 0.0, 1.0)
        applicability = gateway_diagnostic_applicability(self.applicability)
        reason = text(self.reason_code, "diagnostic reason_code", allow_empty=True)
        _validate_sample_quality(
            status,
            value,
            changed_at,
            confirmed_at,
            applicability,
            reason,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "changed_at": self.changed_at,
            "confirmed_at": self.confirmed_at,
            "confidence": self.confidence,
            "applicability": self.applicability,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: object) -> GatewayDiagnosticSample:
        item = _sample_mapping(payload)
        name = gateway_diagnostic_field_name(item["name"])
        return cls(
            name=name,
            value=gateway_diagnostic_value(name, item["value"]),
            status=gateway_diagnostic_status(item["status"]),
            changed_at=non_negative_float(item["changed_at"], "diagnostic changed_at"),
            confirmed_at=non_negative_float(item["confirmed_at"], "diagnostic confirmed_at"),
            confidence=bounded_float(item["confidence"], "diagnostic confidence", 0.0, 1.0),
            applicability=gateway_diagnostic_applicability(item["applicability"]),
            reason_code=text(item["reason_code"], "diagnostic reason_code", allow_empty=True),
        )


def _sample_mapping(payload: object) -> Mapping[str, object]:
    current_names = {
        "name",
        "value",
        "status",
        "changed_at",
        "confirmed_at",
        "confidence",
        "applicability",
        "reason_code",
    }
    if is_string_object_mapping(payload) and set(payload) == current_names:
        return payload
    legacy_names = {"name", "value", "status", "observed_at", "confidence", "reason_code"}
    legacy = exact_mapping(payload, "gateway diagnostic sample", legacy_names)
    observed_at = legacy["observed_at"]
    status = gateway_diagnostic_status(legacy["status"])
    return {
        "name": legacy["name"],
        "value": legacy["value"],
        "status": status,
        "changed_at": observed_at,
        "confirmed_at": observed_at,
        "confidence": legacy["confidence"],
        "applicability": _STATUS_APPLICABILITY[status],
        "reason_code": legacy["reason_code"],
    }


__all__ = [
    "CRITICAL_GATEWAY_DIAGNOSTIC_FIELDS",
    "DiagnosticScalar",
    "GATEWAY_DIAGNOSTIC_FIELD_NAMES",
    "GatewayDiagnosticApplicability",
    "GatewayDiagnosticFieldName",
    "GatewayDiagnosticSample",
    "GatewayDiagnosticStatus",
    "gateway_diagnostic_applicability",
    "gateway_diagnostic_field_name",
    "gateway_diagnostic_status",
    "gateway_diagnostic_value",
]
