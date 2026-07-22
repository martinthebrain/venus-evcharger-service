# SPDX-License-Identifier: GPL-3.0-or-later
"""IPC boundary for gateway pressure snapshots stored as JSON."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import SupportsFloat, SupportsIndex

from venus_evcharger.ports.gateway_pressure import (
    GatewayPressurePolicy,
    GatewayPressureSnapshot,
    GatewayPressureState,
)

_UNKNOWN: GatewayPressureState = "unknown"
_OK: GatewayPressureState = "ok"
_CONGESTED: GatewayPressureState = "congested"
_SLOW: GatewayPressureState = "slow"
_PROTECTIVE: GatewayPressureState = "protective"

_LIVE_INTERVAL_MULTIPLIERS: Mapping[GatewayPressureState, float] = {
    _UNKNOWN: 1.0,
    _OK: 1.0,
    _CONGESTED: 2.0,
    _SLOW: 3.0,
    _PROTECTIVE: 5.0,
}
_OPTIONAL_INTERVAL_MULTIPLIERS: Mapping[GatewayPressureState, float] = {
    _UNKNOWN: 1.0,
    _OK: 1.0,
    _CONGESTED: 3.0,
    _SLOW: 6.0,
    _PROTECTIVE: 12.0,
}
_AUDIT_INTERVAL_MULTIPLIERS: Mapping[GatewayPressureState, float] = {
    _UNKNOWN: 1.0,
    _OK: 1.0,
    _CONGESTED: 2.0,
    _SLOW: 4.0,
    _PROTECTIVE: 8.0,
}
_NORMALIZED_STATES: Mapping[str, GatewayPressureState] = {
    _OK: _OK,
    _CONGESTED: _CONGESTED,
    _SLOW: _SLOW,
    _PROTECTIVE: _PROTECTIVE,
    "degraded": _SLOW,
}


class CachedGatewayPressurePolicy:
    """Cache and apply normalized gateway pressure from one IPC snapshot file."""

    def __init__(
        self,
        health_path: str,
        *,
        now: Callable[[], float] = time.time,
        max_age_seconds: float = 10.0,
        cache_seconds: float = 1.0,
    ) -> None:
        self.health_path = str(health_path or "").strip()
        self._now = now
        self.max_age_seconds = max(0.0, float(max_age_seconds))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._cached_at = 0.0
        self._cached_snapshot = GatewayPressureSnapshot(_UNKNOWN, 0.0, 0.0, False, "unread")

    def snapshot(self) -> GatewayPressureSnapshot:
        """Return a cached normalized pressure snapshot."""
        now = float(self._now())
        if self._cache_fresh(now):
            return self._cached_snapshot
        snapshot = read_gateway_pressure_snapshot(
            self.health_path,
            now=now,
            max_age_seconds=self.max_age_seconds,
        )
        self._cached_at = now
        self._cached_snapshot = snapshot
        return snapshot

    def state(self) -> GatewayPressureState:
        return self.snapshot().state

    def should_throttle_optional_work(self) -> bool:
        return self.state() in {_CONGESTED, _SLOW, _PROTECTIVE}

    def publish_interval_seconds(self, base_seconds: float, *, group: str) -> float:
        return _non_negative_seconds(base_seconds) * self._publish_multiplier(group)

    def audit_repeat_seconds(self, base_seconds: float) -> float:
        return _non_negative_seconds(base_seconds) * _AUDIT_INTERVAL_MULTIPLIERS[self.state()]

    def audit_cleanup_interval_seconds(self, base_seconds: float) -> float:
        return _non_negative_seconds(base_seconds) * _AUDIT_INTERVAL_MULTIPLIERS[self.state()]

    def optional_work_interval_seconds(self, base_seconds: float) -> float:
        return _non_negative_seconds(base_seconds) * _OPTIONAL_INTERVAL_MULTIPLIERS[self.state()]

    def liveness_timeout_seconds(self, base_seconds: float) -> float:
        return _non_negative_seconds(base_seconds) * _LIVE_INTERVAL_MULTIPLIERS[self.state()]

    def _cache_fresh(self, now: float) -> bool:
        return self._cached_at <= now < self._cached_at + self.cache_seconds

    def _publish_multiplier(self, group: str) -> float:
        state = self.state()
        if str(group) == "live-measurements":
            return _LIVE_INTERVAL_MULTIPLIERS[state]
        return _OPTIONAL_INTERVAL_MULTIPLIERS[state]


def read_gateway_pressure_snapshot(
    health_path: str,
    *,
    now: float,
    max_age_seconds: float,
) -> GatewayPressureSnapshot:
    """Interpret one untrusted gateway-health JSON document at the IPC boundary."""
    normalized_path = str(health_path or "").strip()
    if not normalized_path:
        return GatewayPressureSnapshot(_UNKNOWN, 0.0, 0.0, False, "missing-path")
    payload = _mapping(_read_json(normalized_path))
    return _snapshot_from_payload(
        payload,
        now=float(now),
        max_age_seconds=max(0.0, float(max_age_seconds)),
    )


def _snapshot_from_payload(
    payload: Mapping[str, object],
    *,
    now: float,
    max_age_seconds: float,
) -> GatewayPressureSnapshot:
    if not payload:
        return GatewayPressureSnapshot(_UNKNOWN, 0.0, 0.0, False, "missing-health")
    captured_at = _float_or_zero(payload.get("captured_at"))
    age_s = max(0.0, now - captured_at) if captured_at > 0.0 else 0.0
    if _payload_stale(captured_at, age_s, max_age_seconds):
        return GatewayPressureSnapshot(_SLOW, captured_at, age_s, True, "stale-health")
    state, source = _state_from_health(_health_mapping(payload))
    return GatewayPressureSnapshot(state, captured_at, age_s, False, source)


def service_gateway_pressure_policy(service: object) -> GatewayPressurePolicy:
    """Return the pressure policy composed for one service instance."""
    composed = getattr(service, "gateway_pressure_policy", None)
    if isinstance(composed, GatewayPressurePolicy):
        return composed
    cached = getattr(service, "_gateway_pressure_policy", None)
    if isinstance(cached, GatewayPressurePolicy):
        return cached
    policy = CachedGatewayPressurePolicy(_service_health_path(service))
    _cache_service_policy(service, policy)
    return policy


def normalized_gateway_pressure_state(value: object) -> GatewayPressureState:
    """Normalize an untrusted state value to the public pressure contract."""
    if not isinstance(value, str):
        return _UNKNOWN
    return _NORMALIZED_STATES.get(value.strip().lower(), _UNKNOWN)


def _read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _service_health_path(service: object) -> str:
    path = getattr(service, "gateway_health_path", None)
    return path if isinstance(path, str) else ""


def _cache_service_policy(service: object, policy: GatewayPressurePolicy) -> None:
    try:
        setattr(service, "_gateway_pressure_policy", policy)
    except (AttributeError, TypeError):
        return


def _non_negative_seconds(value: float) -> float:
    return max(0.0, float(value))


def _float_or_zero(value: object) -> float:
    if not isinstance(value, (str, bytes, SupportsFloat, SupportsIndex)):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _payload_stale(captured_at: float, age_s: float, max_age_seconds: float) -> bool:
    return captured_at > 0.0 and max_age_seconds > 0.0 and age_s > max_age_seconds


def _health_mapping(payload: Mapping[str, object]) -> Mapping[str, object]:
    nested = _mapping(payload.get("dbus_health"))
    return nested if nested else payload


def _state_from_health(health: Mapping[str, object]) -> tuple[GatewayPressureState, str]:
    backpressure = _mapping(health.get("backpressure"))
    state = normalized_gateway_pressure_state(backpressure.get("state"))
    if state != _UNKNOWN:
        return state, "backpressure"
    state = normalized_gateway_pressure_state(health.get("state"))
    if state != _UNKNOWN:
        return state, "gateway-health"
    return _resource_state(_mapping(health.get("resources")))


def _resource_state(resources: Mapping[str, object]) -> tuple[GatewayPressureState, str]:
    value = resources.get("state")
    if not isinstance(value, str):
        return _UNKNOWN, "unknown"
    state = value.strip().lower()
    if state == "constrained":
        return _SLOW, "resources"
    if state == "busy":
        return _CONGESTED, "resources"
    if state == "ok":
        return _OK, "resources"
    return _UNKNOWN, "unknown"


__all__ = [
    "CachedGatewayPressurePolicy",
    "service_gateway_pressure_policy",
    "normalized_gateway_pressure_state",
    "read_gateway_pressure_snapshot",
]
