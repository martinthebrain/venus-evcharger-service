# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend, charger, Shelly, and error diagnostic values."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.core.contracts import non_negative_int
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_ports import DiagnosticsLearnedPort, SourceRuntimeViewPort
from venus_evcharger.publish.dbus_shared import DbusPublishContext, PublishServicePort, runtime_text_attribute


_BACKEND_TYPE_FIELDS = (
    ("auto_meter_backend", "meter_backend_type", "shelly_meter"),
    ("auto_switch_backend", "switch_backend_type", "shelly_contactor_switch"),
    ("auto_charger_backend", "charger_backend_type", ""),
)
_ERROR_COUNTER_FIELDS = (
    ("auto_dbus_read_errors", "dbus"),
    ("auto_shelly_read_errors", "shelly"),
    ("auto_charger_write_errors", "charger"),
    ("auto_pv_read_errors", "pv"),
    ("auto_battery_read_errors", "battery"),
    ("auto_grid_read_errors", "grid"),
)
_CHARGER_TEXT_FIELDS = (
    ("auto_charger_status", "_last_charger_state_status"),
    ("auto_charger_fault", "_last_charger_state_fault"),
)
_SHELLY_TEXT_FIELDS = (
    ("auto_shelly_state", "_shelly_state", "unknown"),
    ("auto_shelly_last_error", "_shelly_last_error_reason", ""),
)


class DbusDiagnosticsSources:
    """Build backend, charger, Shelly, and error diagnostics."""

    def __init__(
        self,
        context: DbusPublishContext,
        runtime_view: SourceRuntimeViewPort,
        learned: DiagnosticsLearnedPort,
    ) -> None:
        self.service: PublishServicePort = context.service
        self.runtime_view = runtime_view
        self.learned = learned

    def backend_values(self) -> dict[str, DiagnosticValue]:
        """Return backend-composition and runtime-override diagnostics."""
        values: dict[str, DiagnosticValue] = {
            "auto_backend_mode": self.runtime_view.backend_mode_value(self.service),
            "auto_runtime_overrides_active": self._bool_attribute(self.service, "_runtime_overrides_active"),
            "auto_runtime_overrides_path": runtime_text_attribute(self.service, "runtime_overrides_path"),
        }
        for output_key, attribute_name, default in _BACKEND_TYPE_FIELDS:
            values[output_key] = self.runtime_view.backend_type_value(self.service, attribute_name, default)
        return values

    def charger_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return charger, transport, and retry diagnostics."""
        values: dict[str, DiagnosticValue] = {
            "auto_charger_fault_active": self._bool_attribute(self.service, "_last_charger_fault_active"),
            "auto_charger_estimate_active": self.learned.charger_estimate_active(),
            "auto_charger_estimate_source": self.learned.charger_estimate_source(),
            "auto_charger_transport_active": self.learned.charger_transport_active(now),
            "auto_charger_transport_reason": self.learned.charger_transport_reason(now),
            "auto_charger_transport_source": self.learned.charger_transport_source(now),
            "auto_charger_transport_detail": self.learned.charger_transport_detail(now),
            "auto_charger_retry_active": self.learned.charger_retry_active(now),
            "auto_charger_retry_reason": self.learned.charger_retry_reason(now),
            "auto_charger_retry_source": self.learned.charger_retry_source(now),
            "auto_charger_current_target": self.runtime_view.charger_current_target_value(self.service),
        }
        values.update(
            {
                output_key: self.learned.charger_text_observed(attribute_name)
                for output_key, attribute_name in _CHARGER_TEXT_FIELDS
            }
        )
        return values

    @staticmethod
    def error_values(error_state: Mapping[str, object]) -> dict[str, int]:
        """Return aggregate error counters sourced from the runtime error state."""
        counters = {
            output_key: DbusDiagnosticsSources._error_counter_value(error_state, input_key)
            for output_key, input_key in _ERROR_COUNTER_FIELDS
        }
        counters["auto_error_count"] = sum(counters[output_key] for output_key, _input_key in _ERROR_COUNTER_FIELDS)
        counters["auto_input_cache_hits"] = DbusDiagnosticsSources._error_counter_value(error_state, "cache_hits")
        return counters

    @staticmethod
    def _error_counter_value(error_state: Mapping[str, object], input_key: str) -> int:
        """Return one normalized error counter, treating absent keys as zero."""
        if input_key not in error_state:
            return 0
        return int(non_negative_int(error_state[input_key]))

    def shelly_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return Shelly transport and retry diagnostics."""
        svc = self.service
        values: dict[str, DiagnosticValue] = {
            "auto_shelly_retry_remaining": self._shelly_retry_remaining_value(svc, now),
            "auto_shelly_consecutive_errors": self._non_negative_int_attribute(svc, "_shelly_consecutive_errors"),
        }
        values.update(
            {
                output_key: runtime_text_attribute(svc, attribute_name, fallback)
                for output_key, attribute_name, fallback in _SHELLY_TEXT_FIELDS
            }
        )
        return values

    @staticmethod
    def _shelly_retry_remaining_value(svc: object, now: float) -> int:
        """Return remaining Shelly retry delay in seconds."""
        runtime = getattr(svc, "runtime", None)
        source_retry_remaining = getattr(runtime, "source_retry_remaining", None)
        if callable(source_retry_remaining):
            return int(non_negative_int(source_retry_remaining("shelly", now)))
        if not hasattr(svc, "_shelly_retry_after"):
            return 0
        retry_after = getattr(svc, "_shelly_retry_after")
        if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool):
            return 0
        return max(0, int(float(retry_after) - float(now)))

    @staticmethod
    def _bool_attribute(svc: object, attribute_name: str) -> int:
        """Return one boolean runtime flag as a diagnostics integer."""
        if not hasattr(svc, attribute_name):
            return 0
        return int(bool(getattr(svc, attribute_name)))

    @staticmethod
    def _non_negative_int_attribute(svc: object, attribute_name: str) -> int:
        """Return one runtime integer attribute clamped to diagnostics range."""
        if not hasattr(svc, attribute_name):
            return 0
        return int(non_negative_int(getattr(svc, attribute_name)))
