# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-pressure policies shared by contract and scenario harnesses."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.ports.gateway_pressure import (
    GatewayPressureSnapshot,
    GatewayPressureState,
)


@dataclass(frozen=True, slots=True)
class FreshOkGatewayPressurePolicy:
    """Represent an explicitly fresh and healthy gateway baseline."""

    captured_at: float = 100.0

    def snapshot(self) -> GatewayPressureSnapshot:
        return GatewayPressureSnapshot(
            "ok",
            self.captured_at,
            0.0,
            False,
            "test-harness",
        )

    def state(self) -> GatewayPressureState:
        return "ok"

    def should_throttle_optional_work(self) -> bool:
        return False

    def publish_interval_seconds(self, base_seconds: float, *, group: str) -> float:
        del group
        return base_seconds

    def audit_repeat_seconds(self, base_seconds: float) -> float:
        return base_seconds

    def audit_cleanup_interval_seconds(self, base_seconds: float) -> float:
        return base_seconds

    def optional_work_interval_seconds(self, base_seconds: float) -> float:
        return base_seconds

    def liveness_timeout_seconds(self, base_seconds: float) -> float:
        return base_seconds


__all__ = ["FreshOkGatewayPressurePolicy"]
