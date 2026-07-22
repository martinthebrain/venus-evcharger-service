# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway write helpers for Victron ESS balance-bias application."""

from __future__ import annotations

import logging
from typing import Protocol

from venus_evcharger.ports.gateway_operations import EssSetpointIntent, GatewayOperationsPort
from .victron_ess_balance_apply_sources import VictronEssSourceResolver


VICTRON_ESS_BALANCE_WRITE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _WarningRuntime(Protocol):  # pragma: no cover
    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
    ) -> None: ...


class VictronEssWriteService(Protocol):  # pragma: no cover
    runtime: _WarningRuntime
    auto_battery_discharge_balance_victron_bias_min_update_seconds: float
    _victron_ess_balance_last_write_at: float | None
    _victron_ess_balance_last_setpoint_w: float | None


class VictronEssSetpointWriter:
    """Coalesce and enqueue Victron ESS balance-bias setpoint writes through the gateway."""

    def __init__(self, sources: VictronEssSourceResolver, gateway: GatewayOperationsPort) -> None:
        self._sources = sources
        self._gateway = gateway

    def should_write(self, svc: VictronEssWriteService, now: float, setpoint_w: float) -> bool:
        min_update_seconds = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", None) or 0.0),
        )
        last_write_at = self._sources._optional_float(getattr(svc, "_victron_ess_balance_last_write_at", None))
        if last_write_at is not None and (float(now) - float(last_write_at)) < min_update_seconds:
            return False
        last_setpoint_w = self.last_setpoint(svc)
        if last_setpoint_w is None:
            return True
        return abs(float(setpoint_w) - float(last_setpoint_w)) >= 1.0

    def last_setpoint(self, svc: VictronEssWriteService) -> float | None:
        return self._sources._optional_float(getattr(svc, "_victron_ess_balance_last_setpoint_w", None))

    def write_setpoint(
        self,
        svc: VictronEssWriteService,
        value: float,
        *,
        intent: EssSetpointIntent,
    ) -> bool:
        error = self._write_error(value, intent=intent)
        if error is None:
            return True
        svc.runtime.warning_throttled(
            "victron-ess-balance-write-failed",
            self.warning_interval_seconds(svc),
            "Victron ESS balance-bias %s operation was rejected: %s",
            intent,
            error,
        )
        return False

    def _write_error(self, value: float, *, intent: EssSetpointIntent) -> Exception | None:
        try:
            receipt = self._gateway.set_ess_grid_setpoint(float(value), intent=intent)
            if not receipt.accepted:
                raise RuntimeError("gateway did not accept the operation")
            return None
        except VICTRON_ESS_BALANCE_WRITE_ERRORS as error:
            logging.debug("Victron ESS balance-bias %s enqueue failed: %s", intent, error)
            return error

    @staticmethod
    def warning_interval_seconds(svc: VictronEssWriteService) -> float:
        configured = getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", None)
        return 5.0 if configured is None else max(5.0, float(configured))
