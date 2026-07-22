#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the public semantic diagnostics document inside the adapter."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import cast

from venus_evcharger.dbus_adapter.process.protocols.io import DbusAdapterDiagnosticsContext
from venus_evcharger.dbus_adapter.publication.registry import PublicationFieldObservation
from venus_evcharger.ipc.energy import EnergyTopologySnapshot
from venus_evcharger.ipc.gateway_diagnostics import (
    gateway_diagnostics_path,
    gateway_diagnostics_payload,
)
from venus_evcharger.ports.gateway_diagnostic_values import (
    DiagnosticScalar,
    GatewayDiagnosticFieldName,
    GatewayDiagnosticSample,
    GatewayDiagnosticStatus,
)
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsSnapshot,
    GatewayDiscoveryState,
    GatewayDiscoverySummary,
    GatewayHealthState,
    GatewayHealthSummary,
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
_HEALTH_STATES = frozenset({"ok", "degraded", "protective"})


class DbusAdapterDiagnostics:
    """Translate adapter-private state into the public diagnostics contract."""

    def write_gateway_diagnostics(
        self: DbusAdapterDiagnosticsContext,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
    ) -> None:
        snapshot = self.gateway_diagnostics_snapshot(
            health=health,
            topology=topology,
            captured_at=captured_at,
        )
        path = gateway_diagnostics_path(self.paths.run_dir)
        self.json_writer.write(path, gateway_diagnostics_payload(snapshot))

    def gateway_diagnostics_snapshot(
        self: DbusAdapterDiagnosticsContext,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
    ) -> GatewayDiagnosticsSnapshot:
        return GatewayDiagnosticsSnapshot(
            sequence=self.cache.sequence,
            captured_at=captured_at,
            health=_health_summary(health, max_tick_seconds=self.max_tick_seconds),
            discovery=_discovery_summary(
                health,
                topology,
                pending_work=self._introspection_queue_depth,
            ),
            ev_charger=_evcs_samples(
                self.publication_registry,
                captured_at=captured_at,
                stale_after_seconds=self.slo_gui_max_age_seconds,
            ),
        )


def _health_summary(
    health: Mapping[str, object],
    *,
    max_tick_seconds: float,
) -> GatewayHealthSummary:
    state = _health_state(health.get("state"))
    average = _non_negative_float(health.get("avg_latency_ms"))
    maximum = max(average, _non_negative_float(health.get("max_latency_ms")))
    heartbeat_age = _non_negative_float(health.get("mainloop_heartbeat_age_s"))
    last_tick_at = _non_negative_float(health.get("last_tick_at"))
    eventloop = _mapping(health.get("eventloop"))
    return GatewayHealthSummary(
        state=state,
        stale=last_tick_at <= 0.0 or heartbeat_age > max(1.0, max_tick_seconds * 2.0),
        timeouts_60s=_non_negative_int(health.get("timeouts_60s")),
        average_latency_ms=average,
        maximum_latency_ms=maximum,
        pending_gateway_commands=_non_negative_int(health.get("pending_command_count")),
        pending_core_commands=_non_negative_int(health.get("core_command_count")),
        maximum_event_loop_gap_ms_60s=_non_negative_float(eventloop.get("max_tick_gap_ms_60s")),
        last_success_at=_non_negative_float(health.get("last_success_at")),
        last_error_code=_last_error_code(health.get("last_error")),
    )


def _discovery_summary(
    health: Mapping[str, object],
    topology: EnergyTopologySnapshot,
    *,
    pending_work: int,
) -> GatewayDiscoverySummary:
    sources = topology.sources
    return GatewayDiscoverySummary(
        enabled=True,
        state=_discovery_state(health, topology, pending_work=pending_work),
        pending_work=max(0, int(pending_work)),
        discovered_source_count=len(sources),
        unusable_source_count=sum(source.state != "online" for source in sources),
    )


def _discovery_state(
    health: Mapping[str, object],
    topology: EnergyTopologySnapshot,
    *,
    pending_work: int,
) -> GatewayDiscoveryState:
    circuit_state = _health_state(health.get("state"))
    if circuit_state == "protective":
        return "protective"
    if circuit_state == "degraded":
        return "degraded"
    return _normal_discovery_state(health, topology, pending_work=pending_work)


def _normal_discovery_state(
    health: Mapping[str, object],
    topology: EnergyTopologySnapshot,
    *,
    pending_work: int,
) -> GatewayDiscoveryState:
    if pending_work > 0:
        return "running"
    if topology.generation == 0:
        return "unknown"
    if _text(health.get("discovery_last_error")):
        return "error"
    return "idle"


def _evcs_samples(
    registry: object,
    *,
    captured_at: float,
    stale_after_seconds: float,
) -> tuple[GatewayDiagnosticSample, ...]:
    observation = getattr(registry, "evcs_field_observation", None)
    registered = bool(getattr(registry, "evcs_registered", False))
    if not registered or not callable(observation):
        return tuple(_unknown_sample(name) for name in _DIAGNOSTIC_FIELDS)
    field_reader = cast(Callable[[str], PublicationFieldObservation | None], observation)
    active = _observed_sample(
        "runtime_overrides_active",
        field_reader("auto_runtime_overrides_active"),
        _boolean,
        captured_at,
        stale_after_seconds,
    )
    samples = {
        "operating_mode": _observed_sample(
            "operating_mode", field_reader("mode"), _mode, captured_at, stale_after_seconds
        ),
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
    return tuple(samples[name] for name in _DIAGNOSTIC_FIELDS)


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
        observed_at=active.observed_at,
        confidence=active.confidence,
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
        return GatewayDiagnosticSample(name, None, "unavailable", 0.0, 0.0, "field-unavailable")
    try:
        value = converter(observation.value)
    except (TypeError, ValueError):
        return GatewayDiagnosticSample(name, None, "error", 0.0, 0.0, "invalid-publication-value")
    return _valid_observed_sample(
        name,
        value,
        observation.observed_at,
        captured_at=captured_at,
        stale_after_seconds=stale_after_seconds,
    )


def _valid_observed_sample(
    name: GatewayDiagnosticFieldName,
    value: DiagnosticScalar,
    observed_at: float,
    *,
    captured_at: float,
    stale_after_seconds: float,
) -> GatewayDiagnosticSample:
    stale = captured_at - observed_at > max(0.0, stale_after_seconds)
    status: GatewayDiagnosticStatus = "stale" if stale else "fresh"
    return GatewayDiagnosticSample(
        name=name,
        value=value,
        status=status,
        observed_at=observed_at,
        confidence=0.5 if stale else 1.0,
        reason_code="publication-stale" if stale else "",
    )


def _unknown_sample(name: GatewayDiagnosticFieldName) -> GatewayDiagnosticSample:
    return GatewayDiagnosticSample(name, None, "unknown", 0.0, 0.0)


def _health_state(value: object) -> GatewayHealthState:
    normalized = _text(value)
    if normalized in _HEALTH_STATES:
        return cast(GatewayHealthState, normalized)
    return "unknown"


def _last_error_code(value: object) -> str:
    message = _text(value).lower()
    if not message:
        return ""
    if _contains_any(message, ("timeout", "no reply", "noreply")):
        return "timeout"
    if _contains_any(message, ("disconnect", "connection")):
        return "connection-failed"
    return "gateway-operation-failed"


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    untyped = cast(Mapping[object, object], value)
    return {str(key): item for key, item in untyped.items()}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _non_negative_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    return max(0.0, result) if math.isfinite(result) else 0.0


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


__all__ = ["DbusAdapterDiagnostics"]
