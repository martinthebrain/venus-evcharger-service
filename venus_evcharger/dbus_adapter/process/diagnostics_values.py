#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize semantic EVCS publication fields for diagnostics."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.health.freshness import (
    publication_freshness_monotonic,
)
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
from venus_evcharger.ports.gateway_diagnostics_validation import (
    normalized_epoch_timestamp,
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
_SERVICE_HEARTBEAT_FIELDS = frozenset(
    {
        "operating_mode",
        "charging_enabled",
        "auto_start_enabled",
    }
)


@dataclass(frozen=True, slots=True)
class _FreshnessWindow:
    captured_at: float
    captured_monotonic: float
    stale_after_seconds: float


def evcs_samples(
    registry: GatewayPublicationRegistry,
    *,
    captured_at: float,
    captured_monotonic: float,
    stale_after_seconds: float,
) -> tuple[GatewayDiagnosticSample, ...]:
    if not registry.evcs_registered:
        return tuple(_unknown_sample(name) for name in _DIAGNOSTIC_FIELDS)
    samples = _observed_evcs_samples(
        registry.evcs_field_observation,
        captured_at,
        captured_monotonic,
        stale_after_seconds,
    )
    if _non_auto_mode(samples["operating_mode"]):
        samples = _inactive_auto_samples(samples)
    return tuple(samples[name] for name in _DIAGNOSTIC_FIELDS)


def _observed_evcs_samples(
    field_reader: Callable[[str], PublicationFieldObservation | None],
    captured_at: float,
    captured_monotonic: float,
    stale_after_seconds: float,
) -> dict[GatewayDiagnosticFieldName, GatewayDiagnosticSample]:
    window = _FreshnessWindow(
        captured_at,
        captured_monotonic,
        stale_after_seconds,
    )
    operating_mode = _observed_sample(
        "operating_mode",
        field_reader("mode"),
        _mode,
        window,
    )
    active = _observed_sample(
        "runtime_overrides_active",
        field_reader("auto_runtime_overrides_active"),
        _boolean,
        window,
    )
    return {
        "operating_mode": operating_mode,
        "charging_enabled": _charging_enabled_sample(
            field_reader,
            window,
        ),
        "auto_start_enabled": _observed_sample(
            "auto_start_enabled",
            field_reader("auto_start"),
            _boolean,
            window,
        ),
        "ac_power_w": _observed_sample(
            "ac_power_w",
            field_reader("ac_power_w"),
            _finite_float,
            window,
        ),
        "charger_state_code": _observed_sample(
            "charger_state_code",
            field_reader("status"),
            _non_negative_integer,
            window,
        ),
        "decision_reason": _observed_sample(
            "decision_reason",
            field_reader("auto_decision_reason"),
            _text,
            window,
        ),
        "decision_state": _observed_sample(
            "decision_state",
            field_reader("auto_decision_state"),
            _text,
            window,
        ),
        "last_health_reason": _observed_sample(
            "last_health_reason",
            field_reader("auto_health"),
            _text,
            window,
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
    window: _FreshnessWindow,
) -> GatewayDiagnosticSample:
    observation = field_reader("start_stop") or field_reader("enable")
    return _observed_sample(
        "charging_enabled",
        observation,
        _boolean,
        window,
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
    window: _FreshnessWindow,
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
        observation,
        window=window,
    )


def _valid_observed_sample(
    name: GatewayDiagnosticFieldName,
    value: DiagnosticScalar,
    observation: PublicationFieldObservation,
    *,
    window: _FreshnessWindow,
) -> GatewayDiagnosticSample:
    freshness_at = publication_freshness_monotonic(
        observation,
        use_service_heartbeat=name in _SERVICE_HEARTBEAT_FIELDS,
    )
    stale = (
        window.captured_monotonic - freshness_at
        > max(0.0, window.stale_after_seconds)
    )
    status: GatewayDiagnosticStatus = "stale" if stale else "fresh"
    return GatewayDiagnosticSample(
        name=name,
        value=value,
        status=status,
        changed_at=normalized_epoch_timestamp(
            observation.changed_at,
            window.captured_at,
        ),
        confirmed_at=normalized_epoch_timestamp(
            observation.confirmed_at,
            window.captured_at,
        ),
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
