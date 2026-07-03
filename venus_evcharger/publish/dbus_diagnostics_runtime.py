# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime timing and auto-decision diagnostic values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.core.contracts import finite_float_or_none, sanitized_auto_metrics
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_schedule import _DbusDiagnosticsSchedule


class _DbusDiagnosticsRuntime(_DbusDiagnosticsSchedule):
    def _runtime_timing_values(self, now: float) -> dict[str, int | float]:
        """Return timing and queue diagnostics for async runtime health."""
        svc = self.service
        mainloop_heartbeat_at = getattr(svc, "_mainloop_heartbeat_at", None)
        heartbeat_age = (
            max(0.0, now - float(mainloop_heartbeat_at))
            if isinstance(mainloop_heartbeat_at, (int, float))
            else -1.0
        )
        return {
            "auto_update_worker_duration_seconds": float(getattr(svc, "_last_update_cycle_duration_seconds", 0.0)),
            "auto_update_worker_pending": int(bool(getattr(svc, "_update_worker_pending", False))),
            "auto_update_worker_skipped": int(getattr(svc, "_update_worker_skipped_count", 0)),
            "auto_publish_flush_duration_seconds": float(getattr(svc, "_last_publish_flush_duration_seconds", 0.0)),
            "auto_publish_queue_lag_seconds": float(getattr(svc, "_last_dbus_publish_queue_lag_seconds", 0.0)),
            "auto_publish_queue_dropped": int(getattr(svc, "_dbus_publish_dropped_count", 0)),
            "auto_write_command_duration_seconds": float(getattr(svc, "_last_write_command_duration_seconds", 0.0)),
            "auto_write_command_queue_lag_seconds": float(getattr(svc, "_last_write_command_queue_lag_seconds", 0.0)),
            "auto_mainloop_heartbeat_age": heartbeat_age,
        }

    @staticmethod
    def _auto_decision_metric_float(metrics: Mapping[str, Any], field_name: str) -> float:
        value = finite_float_or_none(metrics.get(field_name))
        return -1.0 if value is None else float(value)

    @staticmethod
    def _auto_decision_metric_text(metrics: Mapping[str, Any], field_name: str) -> str:
        value = metrics.get(field_name)
        return "" if value is None else str(value).strip()

    @staticmethod
    def _auto_decision_relay_intent(metrics: Mapping[str, Any]) -> int:
        value = metrics.get("relay_intent")
        if value is None:
            return -1
        return int(bool(value))

    def _auto_decision_counter_values(
        self,
        auto_state: str,
        auto_state_code: int,
    ) -> dict[str, DiagnosticValue]:
        """Return the compact 'why did it start/stop?' diagnostic surface."""
        metrics = sanitized_auto_metrics(self._auto_metrics(self.service))
        return {
            "auto_decision_reason": str(self.service._last_health_reason),
            "auto_decision_state": auto_state,
            "auto_decision_state_code": auto_state_code,
            "auto_decision_relay_intent": self._auto_decision_relay_intent(metrics),
            "auto_decision_surplus_watts": self._auto_decision_metric_float(metrics, "surplus"),
            "auto_decision_grid_watts": self._auto_decision_metric_float(metrics, "grid"),
            "auto_decision_soc_percent": self._auto_decision_metric_float(metrics, "soc"),
            "auto_decision_start_threshold_watts": self._auto_decision_metric_float(metrics, "start_threshold"),
            "auto_decision_stop_threshold_watts": self._auto_decision_metric_float(metrics, "stop_threshold"),
            "auto_decision_profile": self._auto_decision_metric_text(metrics, "profile"),
            "auto_decision_threshold_mode": self._auto_decision_metric_text(metrics, "threshold_mode"),
        }
