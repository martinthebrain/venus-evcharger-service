# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic operations exposed by the dedicated system gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

GxRelayContactMode = Literal["NO", "NC"]
EssSetpointIntent = Literal["tracking", "restore"]


@dataclass(frozen=True, slots=True)
class GatewayOperationReceipt:
    """Result of accepting one asynchronous gateway operation."""

    accepted: bool
    command_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GxRelaySetRequest:
    """Complete semantic intent for one verified GX relay operation."""

    relay_index: int
    contact_mode: GxRelayContactMode
    enabled: bool
    ensure_manual: bool
    verify_settle_seconds: float
    verify_retry_seconds: float


@runtime_checkable
class GatewayOperationsPort(Protocol):  # pragma: no cover
    """Transport-independent system operations consumed by domain code."""

    def read_gx_relay_state(
        self,
        relay_index: int,
        *,
        max_age_seconds: float,
    ) -> int | None: ...

    def set_gx_relay_enabled(
        self,
        request: GxRelaySetRequest,
    ) -> GatewayOperationReceipt: ...

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt: ...


class UnavailableGatewayOperations:
    """Explicit unavailable port for configurations that never use gateway control."""

    def read_gx_relay_state(self, relay_index: int, *, max_age_seconds: float) -> int | None:
        del relay_index, max_age_seconds
        return None

    def set_gx_relay_enabled(
        self,
        request: GxRelaySetRequest,
    ) -> GatewayOperationReceipt:
        del request
        return GatewayOperationReceipt(accepted=False)

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt:
        del watts, intent
        return GatewayOperationReceipt(accepted=False)


def require_gateway_operations(host: object) -> GatewayOperationsPort:
    """Return the composed semantic gateway port or fail at the boundary."""
    operations = getattr(host, "gateway_operations", None)
    if not isinstance(operations, GatewayOperationsPort):
        raise RuntimeError("Semantic gateway operations are not configured")
    return operations
