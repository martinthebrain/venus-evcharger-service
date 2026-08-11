# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured values used by semantic energy IPC snapshots."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass

from venus_evcharger.ipc.energy_types import EnergyValueStatus
from venus_evcharger.ipc.energy_validation import (
    bounded_float,
    exact_fields,
    mapping,
    non_negative_float,
    optional_finite_float,
    text,
    text_tuple,
    unique_text_tuple,
    value_status,
)


@dataclass(frozen=True, slots=True)
class MeasuredValue:
    """One semantic numeric measurement and its transport-neutral quality."""

    value: float | None
    observed_at: float
    status: EnergyValueStatus
    confidence: float
    source_ids: tuple[str, ...] = ()
    reason_code: str = ""
    _: KW_ONLY
    observed_monotonic: float

    def __post_init__(self) -> None:
        optional_finite_float(self.value, "measurement value")
        observed_at = non_negative_float(self.observed_at, "measurement observed_at")
        observed_monotonic = non_negative_float(
            self.observed_monotonic,
            "measurement observed_monotonic",
        )
        status = value_status(self.status)
        bounded_float(self.confidence, "measurement confidence", 0.0, 1.0)
        unique_text_tuple(self.source_ids, "measurement source_ids")
        text(self.reason_code, "measurement reason_code", allow_empty=True)
        _validate_observed_value(status, self.value, observed_at, observed_monotonic)

    def to_payload(self) -> dict[str, object]:
        return {
            "value": self.value,
            "observed_at": self.observed_at,
            "observed_monotonic": self.observed_monotonic,
            "status": self.status,
            "confidence": self.confidence,
            "source_ids": list(self.source_ids),
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: object) -> MeasuredValue:
        item = mapping(payload, "measured value")
        exact_fields(
            item,
            required={
                "value",
                "observed_at",
                "observed_monotonic",
                "status",
                "confidence",
                "source_ids",
                "reason_code",
            },
            label="measured value",
        )
        return cls(
            value=optional_finite_float(item["value"], "measurement value"),
            observed_at=non_negative_float(item["observed_at"], "measurement observed_at"),
            observed_monotonic=non_negative_float(
                item["observed_monotonic"],
                "measurement observed_monotonic",
            ),
            status=value_status(item["status"]),
            confidence=bounded_float(item["confidence"], "measurement confidence", 0.0, 1.0),
            source_ids=text_tuple(item["source_ids"], "measurement source_ids"),
            reason_code=text(item["reason_code"], "measurement reason_code", allow_empty=True),
        )


def _validate_observed_value(
    status: EnergyValueStatus,
    value: float | None,
    observed_at: float,
    observed_monotonic: float,
) -> None:
    """Enforce the atomic value/timestamp contract for observed measurements."""
    if status not in {"fresh", "stale"}:
        return
    if value is None:
        raise ValueError(f"{status} measurement requires a value")
    _require_positive_timestamp(status, observed_at, "observed_at")
    _require_positive_timestamp(status, observed_monotonic, "observed_monotonic")


def _require_positive_timestamp(
    status: EnergyValueStatus,
    timestamp: float,
    field_name: str,
) -> None:
    """Reject missing observation clocks for a value claimed as observed."""
    if timestamp <= 0.0:
        raise ValueError(f"{status} measurement requires a positive {field_name}")


__all__ = ["MeasuredValue"]
