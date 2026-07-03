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
            "auto_backend_mode": backend_mode_for_service(self.service, self._backend_mode_value(self.service)),
            "auto_meter_backend": backend_type_for_service(
                self.service,
                "meter",
                self._backend_type_value(self.service, "meter_backend_type", "shelly_meter"),
            ),
            "auto_switch_backend": backend_type_for_service(
                self.service,
                "switch",
                self._backend_type_value(self.service, "switch_backend_type", "shelly_contactor_switch"),
            ),
            "auto_charger_backend": backend_type_for_service(
                self.service,
                "charger",
                self._backend_type_value(self.service, "charger_backend_type"),
            ),
            "auto_runtime_overrides_active": int(bool(getattr(self.service, "_runtime_overrides_active", False))),
            "auto_runtime_overrides_path": str(getattr(self.service, "runtime_overrides_path", "") or ""),
        }

    def _charger_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return charger, transport, and retry diagnostics."""
        return {
            "auto_charger_status": self._charger_text_observed("_last_charger_state_status"),
            "auto_charger_fault": self._charger_text_observed("_last_charger_state_fault"),
            "auto_charger_fault_active": int(bool(getattr(self.service, "_last_charger_fault_active", 0))),
            "auto_charger_estimate_active": self._charger_estimate_active(),
            "auto_charger_estimate_source": self._charger_estimate_source(),
            "auto_charger_transport_active": self._charger_transport_active(now),
            "auto_charger_transport_reason": self._charger_transport_reason(now),
            "auto_charger_transport_source": self._charger_transport_source(now),
            "auto_charger_transport_detail": self._charger_transport_detail(now),
            "auto_charger_retry_active": self._charger_retry_active(now),
            "auto_charger_retry_reason": self._charger_retry_reason(now),
            "auto_charger_retry_source": self._charger_retry_source(now),
            "auto_charger_current_target": self._charger_current_target_value(self.service),
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
            "auto_error_count": error_count,
            "auto_dbus_read_errors": int(error_state.get("dbus", 0)),
            "auto_shelly_read_errors": int(error_state.get("shelly", 0)),
            "auto_charger_write_errors": int(error_state.get("charger", 0)),
            "auto_pv_read_errors": int(error_state.get("pv", 0)),
            "auto_battery_read_errors": int(error_state.get("battery", 0)),
            "auto_grid_read_errors": int(error_state.get("grid", 0)),
            "auto_input_cache_hits": int(error_state.get("cache_hits", 0)),
        }

    def _shelly_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return Shelly transport and retry diagnostics."""
        svc = self.service
        return {
            "auto_shelly_state": str(getattr(svc, "_shelly_state", "unknown") or "unknown"),
            "auto_shelly_last_error": str(getattr(svc, "_shelly_last_error_reason", "") or ""),
            "auto_shelly_retry_remaining": self._shelly_retry_remaining_value(svc, now),
            "auto_shelly_consecutive_errors": int(getattr(svc, "_shelly_consecutive_errors", 0) or 0),
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
