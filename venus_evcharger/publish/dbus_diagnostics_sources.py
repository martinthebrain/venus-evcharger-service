# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend, charger, Shelly, and error diagnostic values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue, _DbusDiagnosticsContracts


class _DbusDiagnosticsSources(_DbusDiagnosticsContracts):
    def _backend_counter_values(self) -> dict[str, DiagnosticValue]:
        """Return backend-composition and runtime-override diagnostics."""
        return {
            "/Auto/BackendMode": backend_mode_for_service(self.service, self._backend_mode_value(self.service)),
            "/Auto/MeterBackend": backend_type_for_service(
                self.service,
                "meter",
                self._backend_type_value(self.service, "meter_backend_type", "shelly_meter"),
            ),
            "/Auto/SwitchBackend": backend_type_for_service(
                self.service,
                "switch",
                self._backend_type_value(self.service, "switch_backend_type", "shelly_contactor_switch"),
            ),
            "/Auto/ChargerBackend": backend_type_for_service(
                self.service,
                "charger",
                self._backend_type_value(self.service, "charger_backend_type"),
            ),
            "/Auto/RuntimeOverridesActive": int(bool(getattr(self.service, "_runtime_overrides_active", False))),
            "/Auto/RuntimeOverridesPath": str(getattr(self.service, "runtime_overrides_path", "") or ""),
        }

    def _charger_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return charger, transport, and retry diagnostics."""
        return {
            "/Auto/ChargerStatus": self._charger_text_observed("_last_charger_state_status"),
            "/Auto/ChargerFault": self._charger_text_observed("_last_charger_state_fault"),
            "/Auto/ChargerFaultActive": int(bool(getattr(self.service, "_last_charger_fault_active", 0))),
            "/Auto/ChargerEstimateActive": self._charger_estimate_active(),
            "/Auto/ChargerEstimateSource": self._charger_estimate_source(),
            "/Auto/ChargerTransportActive": self._charger_transport_active(now),
            "/Auto/ChargerTransportReason": self._charger_transport_reason(now),
            "/Auto/ChargerTransportSource": self._charger_transport_source(now),
            "/Auto/ChargerTransportDetail": self._charger_transport_detail(now),
            "/Auto/ChargerRetryActive": self._charger_retry_active(now),
            "/Auto/ChargerRetryReason": self._charger_retry_reason(now),
            "/Auto/ChargerRetrySource": self._charger_retry_source(now),
            "/Auto/ChargerCurrentTarget": self._charger_current_target_value(self.service),
        }

    @staticmethod
    def _error_counter_values(error_state: Mapping[str, Any]) -> dict[str, int]:
        """Return aggregate error counters sourced from the runtime error state."""
        error_count = int(
            error_state.get("dbus", 0)
            + error_state.get("shelly", 0)
            + error_state.get("charger", 0)
            + error_state.get("pv", 0)
            + error_state.get("battery", 0)
            + error_state.get("grid", 0)
        )
        return {
            "/Auto/ErrorCount": error_count,
            "/Auto/DbusReadErrors": int(error_state.get("dbus", 0)),
            "/Auto/ShellyReadErrors": int(error_state.get("shelly", 0)),
            "/Auto/ChargerWriteErrors": int(error_state.get("charger", 0)),
            "/Auto/PvReadErrors": int(error_state.get("pv", 0)),
            "/Auto/BatteryReadErrors": int(error_state.get("battery", 0)),
            "/Auto/GridReadErrors": int(error_state.get("grid", 0)),
            "/Auto/InputCacheHits": int(error_state.get("cache_hits", 0)),
        }

    def _shelly_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return Shelly transport and retry diagnostics."""
        svc = self.service
        return {
            "/Auto/ShellyState": str(getattr(svc, "_shelly_state", "unknown") or "unknown"),
            "/Auto/ShellyLastError": str(getattr(svc, "_shelly_last_error_reason", "") or ""),
            "/Auto/ShellyRetryRemaining": self._shelly_retry_remaining_value(svc, now),
            "/Auto/ShellyConsecutiveErrors": int(getattr(svc, "_shelly_consecutive_errors", 0) or 0),
        }

    @staticmethod
    def _shelly_retry_remaining_value(svc: Any, now: float) -> int:
        """Return remaining Shelly retry delay in seconds."""
        source_retry_remaining = getattr(svc, "_source_retry_remaining", None)
        if callable(source_retry_remaining):
            return int(source_retry_remaining("shelly", now))
        retry_after = getattr(svc, "_shelly_retry_after", 0.0)
        if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool):
            return 0
        return max(0, int(float(retry_after) - float(now)))
