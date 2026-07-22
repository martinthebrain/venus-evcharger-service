# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral pressure and health contracts for the system gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

GatewayPressureState = Literal["unknown", "ok", "congested", "slow", "protective"]


@dataclass(frozen=True, slots=True)
class GatewayPressureSnapshot:
    """Normalized gateway pressure suitable for domain-side scheduling."""

    state: GatewayPressureState
    captured_at: float
    age_s: float
    stale: bool
    source: str


@runtime_checkable
class GatewayPressurePolicy(Protocol):  # pragma: no cover - declarative port
    """Scheduling decisions exposed without leaking gateway transport details."""

    def snapshot(self) -> GatewayPressureSnapshot: ...

    def state(self) -> GatewayPressureState: ...

    def should_throttle_optional_work(self) -> bool: ...

    def publish_interval_seconds(self, base_seconds: float, *, group: str) -> float: ...

    def audit_repeat_seconds(self, base_seconds: float) -> float: ...

    def audit_cleanup_interval_seconds(self, base_seconds: float) -> float: ...

    def optional_work_interval_seconds(self, base_seconds: float) -> float: ...

    def liveness_timeout_seconds(self, base_seconds: float) -> float: ...


__all__ = [
    "GatewayPressurePolicy",
    "GatewayPressureSnapshot",
    "GatewayPressureState",
]
