# SPDX-License-Identifier: GPL-3.0-or-later
"""Core-side DBus gateway backpressure policy.

The DBus gateway owns direct Victron DBus access and publishes a RAM-backed
health file.  The core service reads that file as an advisory signal only: bad
gateway health may slow optional work, but must not block safety or user-facing
control commands.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.dbus_gateway_core import float_or_zero, read_json_file

CoreBackpressureState = Literal["unknown", "ok", "congested", "slow", "protective"]

_UNKNOWN: CoreBackpressureState = "unknown"
_OK: CoreBackpressureState = "ok"
_CONGESTED: CoreBackpressureState = "congested"
_SLOW: CoreBackpressureState = "slow"
_PROTECTIVE: CoreBackpressureState = "protective"

_LIVE_INTERVAL_MULTIPLIERS: Mapping[CoreBackpressureState, float] = {
    _UNKNOWN: 1.0,
    _OK: 1.0,
    _CONGESTED: 2.0,
    _SLOW: 3.0,
    _PROTECTIVE: 5.0,
}
_OPTIONAL_INTERVAL_MULTIPLIERS: Mapping[CoreBackpressureState, float] = {
    _UNKNOWN: 1.0,
    _OK: 1.0,
    _CONGESTED: 3.0,
    _SLOW: 6.0,
    _PROTECTIVE: 12.0,
}
_AUDIT_INTERVAL_MULTIPLIERS: Mapping[CoreBackpressureState, float] = {
    _UNKNOWN: 1.0,
    _OK: 1.0,
    _CONGESTED: 2.0,
    _SLOW: 4.0,
    _PROTECTIVE: 8.0,
}
_NORMALIZED_STATES: Mapping[str, CoreBackpressureState] = {
    _OK: _OK,
    _CONGESTED: _CONGESTED,
    _SLOW: _SLOW,
    _PROTECTIVE: _PROTECTIVE,
    "degraded": _SLOW,
}


@dataclass(frozen=True)
class CoreDbusBackpressureSnapshot:
    """Normalized view of the gateway health file for the core service."""

    state: CoreBackpressureState
    captured_at: float
    age_s: float
    stale: bool
    source: str


class CoreDbusBackpressurePolicy:
    """Read gateway health and map it to conservative core throttling decisions."""

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
        self._cached_snapshot = CoreDbusBackpressureSnapshot(_UNKNOWN, 0.0, 0.0, False, "unread")

    def snapshot(self) -> CoreDbusBackpressureSnapshot:
        """Return a cached normalized health snapshot."""
        now = float(self._now())
        if self._cache_fresh(now):
            return self._cached_snapshot
        snapshot = self._load_snapshot(now)
        self._cached_at = now
        self._cached_snapshot = snapshot
        return snapshot

    def state(self) -> CoreBackpressureState:
        """Return the current normalized core throttling state."""
        return self.snapshot().state

    def should_throttle_optional_work(self) -> bool:
        """Return whether noncritical work should be slowed or skipped."""
        return self.state() in {_CONGESTED, _SLOW, _PROTECTIVE}

    def publish_interval_seconds(self, base_seconds: float, *, group: str) -> float:
        """Return the effective publish interval for one optional publish group."""
        return _non_negative_seconds(base_seconds) * self._publish_multiplier(group)

    def audit_repeat_seconds(self, base_seconds: float) -> float:
        """Return the effective auto-audit repeat interval."""
        return _non_negative_seconds(base_seconds) * _AUDIT_INTERVAL_MULTIPLIERS[self.state()]

    def audit_cleanup_interval_seconds(self, base_seconds: float) -> float:
        """Return the effective auto-audit cleanup interval."""
        return _non_negative_seconds(base_seconds) * _AUDIT_INTERVAL_MULTIPLIERS[self.state()]

    def optional_work_interval_seconds(self, base_seconds: float) -> float:
        """Return the effective interval for noncritical background work."""
        return _non_negative_seconds(base_seconds) * _OPTIONAL_INTERVAL_MULTIPLIERS[self.state()]

    def liveness_timeout_seconds(self, base_seconds: float) -> float:
        """Return a relaxed liveness timeout while the DBus gateway is stressed."""
        return _non_negative_seconds(base_seconds) * _LIVE_INTERVAL_MULTIPLIERS[self.state()]

    def _cache_fresh(self, now: float) -> bool:
        return self._cached_at <= now < self._cached_at + self.cache_seconds

    def _load_snapshot(self, now: float) -> CoreDbusBackpressureSnapshot:
        if not self.health_path:
            return CoreDbusBackpressureSnapshot(_UNKNOWN, 0.0, 0.0, False, "missing-path")
        payload = _mapping(read_json_file(self.health_path))
        if not payload:
            return CoreDbusBackpressureSnapshot(_UNKNOWN, 0.0, 0.0, False, "missing-health")
        captured_at = float_or_zero(payload.get("captured_at"))
        age_s = max(0.0, now - captured_at) if captured_at > 0.0 else 0.0
        if _payload_stale(captured_at, age_s, self.max_age_seconds):
            return CoreDbusBackpressureSnapshot(_SLOW, captured_at, age_s, True, "stale-health")
        health = _health_mapping(payload)
        state, source = _state_from_health(health)
        return CoreDbusBackpressureSnapshot(state, captured_at, age_s, False, source)

    def _publish_multiplier(self, group: str) -> float:
        state = self.state()
        if str(group) == "live-measurements":
            return _LIVE_INTERVAL_MULTIPLIERS[state]
        return _OPTIONAL_INTERVAL_MULTIPLIERS[state]


def service_dbus_backpressure_policy(service: object) -> CoreDbusBackpressurePolicy:
    """Return the RAM-cached policy attached to one service instance."""
    existing = getattr(service, "_dbus_backpressure_policy", None)
    if isinstance(existing, CoreDbusBackpressurePolicy):
        return existing
    policy = CoreDbusBackpressurePolicy(_service_health_path(service))
    try:
        setattr(service, "_dbus_backpressure_policy", policy)
    except (AttributeError, TypeError):
        pass
    return policy


def normalized_core_backpressure_state(value: object) -> CoreBackpressureState:
    """Return one of the core backpressure states for arbitrary health values."""
    if not isinstance(value, str):
        return _UNKNOWN
    text = value.strip().lower()
    return _NORMALIZED_STATES.get(text, _UNKNOWN)


def _service_health_path(service: object) -> str:
    try:
        value = getattr(service, "dbus_gateway_health_path")
    except AttributeError:
        return ""
    return value if isinstance(value, str) else ""


def _non_negative_seconds(value: float) -> float:
    return max(0.0, float(value))


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _payload_stale(captured_at: float, age_s: float, max_age_seconds: float) -> bool:
    return captured_at > 0.0 and max_age_seconds > 0.0 and age_s > max_age_seconds


def _health_mapping(payload: Mapping[str, object]) -> Mapping[str, object]:
    nested = _mapping(payload.get("dbus_health"))
    return nested if nested else payload


def _state_from_health(health: Mapping[str, object]) -> tuple[CoreBackpressureState, str]:
    backpressure = _mapping(health.get("backpressure"))
    state = normalized_core_backpressure_state(backpressure.get("state"))
    if state != _UNKNOWN:
        return state, "backpressure"
    state = normalized_core_backpressure_state(health.get("state"))
    if state != _UNKNOWN:
        return state, "dbus-health"
    return _resource_state(_mapping(health.get("resources")))


def _resource_state(resources: Mapping[str, object]) -> tuple[CoreBackpressureState, str]:
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
    "CoreBackpressureState",
    "CoreDbusBackpressurePolicy",
    "CoreDbusBackpressureSnapshot",
    "normalized_core_backpressure_state",
    "service_dbus_backpressure_policy",
]
