#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Health, discovery, and publication summaries for gateway diagnostics."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TypeGuard

from venus_evcharger.dbus_adapter.publication.registry import GatewayPublicationRegistry
from venus_evcharger.dbus_gateway_core import normalized_object_mapping
from venus_evcharger.ipc.energy import EnergySourceDescriptor, EnergyTopologySnapshot
from venus_evcharger.ports.gateway_diagnostic_discovery import (
    GatewayDiscoveryState,
    GatewayDiscoverySummary,
    GatewaySourceSummary,
)
from venus_evcharger.ports.gateway_diagnostic_health import (
    GatewayHealthState,
    GatewayHealthSummary,
    GatewayPublicationSummary,
)

_PRESSURE_DEADLINE_MULTIPLIERS = {
    "ok": 2.0,
    "busy": 3.0,
    "congested": 3.0,
    "slow": 5.0,
    "degraded": 5.0,
    "constrained": 8.0,
    "protective": 8.0,
}


def health_summary(
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


def discovery_summary(
    health: Mapping[str, object],
    topology: EnergyTopologySnapshot,
    *,
    pending_work: int,
) -> GatewayDiscoverySummary:
    sources = topology.sources
    dormant_source_ids = _text_values(health.get("dormant_energy_source_ids"))
    unavailability_reasons = _text_mapping(
        health.get("energy_source_unavailability_reasons")
    )
    source_summaries = tuple(
        _source_summary(
            source,
            dormant_source_ids=dormant_source_ids,
            unavailability_reasons=unavailability_reasons,
        )
        for source in sources
    )
    return GatewayDiscoverySummary(
        enabled=True,
        state=_discovery_state(health, topology, pending_work=pending_work),
        pending_work=max(0, int(pending_work)),
        discovered_source_count=len(sources),
        unusable_source_count=sum(
            source.availability in {"unavailable", "unknown"}
            for source in source_summaries
        ),
        dormant_source_count=sum(
            source.availability == "dormant"
            for source in source_summaries
        ),
        sources=source_summaries,
    )


def publication_summary(
    registry: GatewayPublicationRegistry,
    *,
    captured_at: float,
    stale_after_seconds: float,
) -> GatewayPublicationSummary:
    if not registry.evcs_registered:
        return GatewayPublicationSummary(False, 0.0, True)
    heartbeat = _non_negative_float(registry.evcs_publication_heartbeat_at)
    stale = heartbeat <= 0.0 or captured_at - heartbeat > max(0.0, stale_after_seconds)
    return GatewayPublicationSummary(True, heartbeat, stale)


def diagnostic_freshness_deadline(
    health: Mapping[str, object],
    configured_seconds: float,
) -> float:
    states = (
        _text(health.get("state")),
        _text(_mapping(health.get("backpressure")).get("state")),
        _text(_mapping(health.get("resources")).get("state")),
    )
    multiplier = max(_PRESSURE_DEADLINE_MULTIPLIERS.get(state, 2.0) for state in states)
    tick_seconds = _non_negative_float(health.get("adaptive_tick_seconds"))
    return max(0.0, configured_seconds, tick_seconds * multiplier)


def _source_summary(
    source: EnergySourceDescriptor,
    *,
    dormant_source_ids: frozenset[str],
    unavailability_reasons: Mapping[str, str],
) -> GatewaySourceSummary:
    if source.state == "online":
        return GatewaySourceSummary(source.source_id, source.kind, "available")
    if source.state == "offline":
        return _offline_source_summary(
            source,
            dormant_source_ids,
            unavailability_reasons,
        )
    return GatewaySourceSummary(
        source.source_id,
        source.kind,
        "unknown",
        "source-state-unknown",
    )


def _offline_source_summary(
    source: EnergySourceDescriptor,
    dormant_source_ids: frozenset[str],
    unavailability_reasons: Mapping[str, str],
) -> GatewaySourceSummary:
    if source.kind in {"pv_ac", "pv_dc"} and source.source_id in dormant_source_ids:
        return GatewaySourceSummary(
            source.source_id,
            source.kind,
            "dormant",
            "pv-sleep-confirmed",
        )
    return GatewaySourceSummary(
        source.source_id,
        source.kind,
        "unavailable",
        unavailability_reasons.get(
            source.source_id,
            "source-not-advertising",
        ),
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


def _health_state(value: object) -> GatewayHealthState:
    normalized = _text(value)
    if normalized == "ok":
        return "ok"
    if normalized == "degraded":
        return "degraded"
    if normalized == "protective":
        return "protective"
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
    return normalized_object_mapping(value) or {}


def _text_mapping(value: object) -> dict[str, str]:
    return {
        str(key): item
        for key, item in _mapping(value).items()
        if isinstance(item, str) and item
    }


def _text_values(value: object) -> frozenset[str]:
    if not _is_object_collection(value):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str) and item)


def _is_object_collection(
    value: object,
) -> TypeGuard[list[object] | tuple[object, ...] | set[object] | frozenset[object]]:
    return isinstance(value, (list, tuple, set, frozenset))


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


__all__ = [
    "diagnostic_freshness_deadline",
    "discovery_summary",
    "health_summary",
    "publication_summary",
]
