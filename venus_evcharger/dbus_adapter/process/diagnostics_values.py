#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize semantic EVCS publication fields for diagnostics."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.publication.registry import (
    GatewayPublicationRegistry,
    PublicationFieldObservation,
)
from venus_evcharger.ports.gateway_diagnostic_values import (
    DiagnosticScalar,
    GatewayDiagnosticFieldName,
    GatewayDiagnosticSample,
    GatewayDiagnosticStatus,
)

_DIAGNOSTIC_FIELDS: tuple[GatewayDiagnosticFieldName, ...] = (
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
)
_AUTO_ONLY_FIELDS = frozenset(
    {
        "decision_reason",
        "decision_state",
        "last_health_reason",
        "runtime_overrides_active",
        "runtime_overrides_source",
    }
)


@dataclass(frozen=True, slots=True)
class _FreshnessWindow:
    captured_at: float
    stale_after_seconds: float


def evcs_samples(
    registry: GatewayPublicationRegistry,
    *,
    captured_at: float,
    stale_after_seconds: float,
) -> tuple[GatewayDiagnosticSample, ...]:
    if not registry.evcs_registered:
        return tuple(_unknown_sample(name) for name in _DIAGNOSTIC_FIELDS)
    samples = _observed_evcs_samples(
        registry.evcs_field_observation,
        captured_at,
        stale_after_seconds,
    )
    if _non_auto_mode(samples["operating_mode"]):
        samples = _inactive_auto_samples(samples)
    return tuple(samples[name] for name in _DIAGNOSTIC_FIELDS)


def _observed_evcs_samples(
    field_reader: Callable[[str], PublicationFieldObservation | None],
    captured_at: float,
    stale_after_seconds: float,
) -> dict[GatewayDiagnosticFieldName, GatewayDiagnosticSample]:
    operating_mode = _observed_sample(
        "operating_mode",
        field_reader("mode"),
        _mode,
        captured_at,
        stale_after_seconds,
    )
    active = _observed_sample(
        "runtime_overrides_active",
        field_reader("auto_runtime_overrides_active"),
        _boolean,
        captured_at,
        stale_after_seconds,
    )
    return {
        "operating_mode": operating_mode,
        "charging_enabled": _charging_enabled_sample(field_reader, captured_at, stale_after_seconds),
        "auto_start_enabled": _observed_sample(
            "auto_start_enabled", field_reader("auto_start"), _boolean, captured_at, stale_after_seconds
        ),
        "ac_power_w": _observed_sample(
            "ac_power_w", field_reader("ac_power_w"), _finite_float, captured_at, stale_after_seconds
        ),
        "charger_state_code": _observed_sample(
            "charger_state_code", field_reader("status"), _non_negative_integer, captured_at, stale_after_seconds
        ),
        "decision_reason": _observed_sample(
            "decision_reason", field_reader("auto_decision_reason"), _text, captured_at, stale_after_seconds
        ),
        "decision_state": _observed_sample(
            "decision_state", field_reader("auto_decision_state"), _text, captured_at, stale_after_seconds
        ),
        "last_health_reason": _observed_sample(
            "last_health_reason", field_reader("auto_health"), _text, captured_at, stale_after_seconds
        ),
        "runtime_overrides_active": active,
        "runtime_overrides_source": _runtime_overrides_source(active),
    }


def _inactive_auto_samples(
    samples: dict[GatewayDiagnosticFieldName, GatewayDiagnosticSample],
) -> dict[GatewayDiagnosticFieldName, GatewayDiagnosticSample]:
    return {
        name: _inactive_sample(sample) if name in _AUTO_ONLY_FIELDS else sample
        for name, sample in samples.items()
    }


def _non_auto_mode(mode: GatewayDiagnosticSample) -> bool:
    return mode.status in {"fresh", "stale"} and mode.value in {0, 2}


def _inactive_sample(sample: GatewayDiagnosticSample) -> GatewayDiagnosticSample:
    return GatewayDiagnosticSample(
        name=sample.name,
        value=sample.value,
        status="inactive",
        changed_at=sample.changed_at,
        confirmed_at=sample.confirmed_at,
        confidence=1.0,
        applicability="not-applicable",
        reason_code="operating-mode-not-auto",
    )


def _charging_enabled_sample(
    field_reader: Callable[[str], PublicationFieldObservation | None],
    captured_at: float,
    stale_after_seconds: float,
) -> GatewayDiagnosticSample:
    observation = field_reader("start_stop") or field_reader("enable")
    return _observed_sample(
        "charging_enabled",
        observation,
        _boolean,
        captured_at,
        stale_after_seconds,
    )


def _runtime_overrides_source(active: GatewayDiagnosticSample) -> GatewayDiagnosticSample:
    if active.value is None:
        return _unknown_sample("runtime_overrides_source")
    return GatewayDiagnosticSample(
        name="runtime_overrides_source",
        value="runtime-overrides" if active.value else "static-configuration",
        status=active.status,
        changed_at=active.changed_at,
        confirmed_at=active.confirmed_at,
        confidence=active.confidence,
        applicability=active.applicability,
        reason_code=active.reason_code,
    )


def _observed_sample(
    name: GatewayDiagnosticFieldName,
    observation: PublicationFieldObservation | None,
    converter: Callable[[object], DiagnosticScalar],
    captured_at: float,
    stale_after_seconds: float,
) -> GatewayDiagnosticSample:
    if observation is None:
        return GatewayDiagnosticSample(
            name,
            None,
            "unavailable",
            0.0,
            0.0,
            0.0,
            reason_code="field-unavailable",
        )
    try:
        value = converter(observation.value)
    except (TypeError, ValueError):
        return GatewayDiagnosticSample(
            name,
            None,
            "error",
            0.0,
            0.0,
            0.0,
            reason_code="invalid-publication-value",
        )
    return _valid_observed_sample(
        name,
        value,
        observation.changed_at,
        observation.confirmed_at,
        window=_FreshnessWindow(captured_at, stale_after_seconds),
    )


def _valid_observed_sample(
    name: GatewayDiagnosticFieldName,
    value: DiagnosticScalar,
    changed_at: float,
    confirmed_at: float,
    *,
    window: _FreshnessWindow,
) -> GatewayDiagnosticSample:
    stale = window.captured_at - confirmed_at > max(0.0, window.stale_after_seconds)
    status: GatewayDiagnosticStatus = "stale" if stale else "fresh"
    return GatewayDiagnosticSample(
        name=name,
        value=value,
        status=status,
        changed_at=changed_at,
        confirmed_at=confirmed_at,
        confidence=0.5 if stale else 1.0,
        reason_code="publication-stale" if stale else "",
    )


def _unknown_sample(name: GatewayDiagnosticFieldName) -> GatewayDiagnosticSample:
    return GatewayDiagnosticSample(
        name,
        None,
        "unknown",
        0.0,
        0.0,
        0.0,
        applicability="unknown",
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mode(value: object) -> int:
    result = _non_negative_integer(value)
    if result not in {0, 1, 2}:
        raise ValueError("invalid operating mode")
    return result


def _non_negative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError("integer value required")
    result = int(value)
    if result < 0:
        raise ValueError("non-negative integer required")
    return result


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    raise TypeError("binary value required")


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric value required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("finite value required")
    return result


__all__ = ["evcs_samples"]
