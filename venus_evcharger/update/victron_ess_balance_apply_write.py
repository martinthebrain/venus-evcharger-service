# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway write helpers for Victron ESS balance-bias application."""

from __future__ import annotations

import logging
from typing import Any

from venus_evcharger.dbus_gateway import GatewayClient, gateway_paths
from .victron_ess_balance_apply_sources import VictronEssSourceResolver


VICTRON_ESS_BALANCE_WRITE_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class VictronEssSetpointWriter:
    """Coalesce and enqueue Victron ESS balance-bias setpoint writes through the gateway."""

    def __init__(self, sources: VictronEssSourceResolver) -> None:
        self._sources = sources

    def _victron_ess_balance_should_write(self, svc: Any, now: float, setpoint_w: float) -> bool:
        min_update_seconds = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", None) or 0.0),
        )
        last_write_at = self._sources._optional_float(getattr(svc, "_victron_ess_balance_last_write_at", None))
        if last_write_at is not None and (float(now) - float(last_write_at)) < min_update_seconds:
            return False
        last_setpoint_w = self._victron_ess_balance_last_setpoint(svc)
        if last_setpoint_w is None:
            return True
        return abs(float(setpoint_w) - float(last_setpoint_w)) >= 1.0

    def _victron_ess_balance_last_setpoint(self, svc: Any) -> float | None:
        return self._sources._optional_float(getattr(svc, "_victron_ess_balance_last_setpoint_w", None))

    @staticmethod
    def _victron_ess_balance_write_target(service_name: object, path: object) -> tuple[str, str]:
        return str(service_name or "").strip(), str(path or "").strip()

    @staticmethod
    def _victron_ess_balance_write_payload(dbus_module: Any, value: float) -> Any:
        del dbus_module
        return float(value)

    def _victron_ess_balance_try_write_setpoint(
        self,
        svc: Any,
        normalized_service: str,
        normalized_path: str,
        value: float,
    ) -> None:
        GatewayClient(gateway_paths(str(getattr(svc, "dbus_gateway_run_dir", None) or "") or None)).enqueue_command(
            {
                "kind": "set_value",
                "source": "victron-ess-balance",
                "service": normalized_service,
                "path": normalized_path,
                "value": self._victron_ess_balance_write_payload(None, value),
                "priority": "user",
                "coalesce_key": f"{normalized_service}:{normalized_path}",
            }
        )

    @staticmethod
    def _victron_ess_balance_log_write_retry(
        normalized_service: str,
        normalized_path: str,
        error: Exception,
    ) -> None:
        logging.debug(
            "Victron ESS balance-bias write retry for %s %s after error: %s",
            normalized_service,
            normalized_path,
            error,
        )

    def _victron_ess_balance_write_setpoint(
        self,
        svc: Any,
        service_name: object,
        path: object,
        value: float,
    ) -> bool:
        normalized_service, normalized_path = self._victron_ess_balance_write_target(service_name, path)
        if not normalized_service or not normalized_path:
            return False
        last_error = self._victron_ess_balance_write_error(
            svc,
            normalized_service,
            normalized_path,
            value,
        )
        if last_error is None:
            return True
        svc.runtime.warning_throttled(
            "victron-ess-balance-write-failed",
            self._victron_ess_balance_write_warning_interval_seconds(svc),
            "Victron ESS balance-bias write to %s %s failed: %s",
            normalized_service,
            normalized_path,
            last_error,
        )
        return False

    def _victron_ess_balance_write_error(
        self,
        svc: Any,
        normalized_service: str,
        normalized_path: str,
        value: float,
    ) -> Exception | None:
        try:
            self._victron_ess_balance_try_write_setpoint(svc, normalized_service, normalized_path, value)
            return None
        except VICTRON_ESS_BALANCE_WRITE_ERRORS as error:
            self._victron_ess_balance_log_write_retry(normalized_service, normalized_path, error)
        try:
            self._victron_ess_balance_try_write_setpoint(svc, normalized_service, normalized_path, value)
            return None
        except VICTRON_ESS_BALANCE_WRITE_ERRORS as error:
            return error

    @staticmethod
    def _victron_ess_balance_write_warning_interval_seconds(svc: Any) -> float:
        configured = getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", None)
        return 5.0 if configured is None else max(5.0, float(configured))

    @classmethod
    def _victron_ess_balance_dbus_module(cls) -> Any:
        del cls
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")
