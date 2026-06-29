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
            "/Auto/UpdateWorkerDurationSeconds": float(getattr(svc, "_last_update_cycle_duration_seconds", 0.0)),
            "/Auto/UpdateWorkerPending": int(bool(getattr(svc, "_update_worker_pending", False))),
            "/Auto/UpdateWorkerSkipped": int(getattr(svc, "_update_worker_skipped_count", 0)),
            "/Auto/PublishFlushDurationSeconds": float(getattr(svc, "_last_publish_flush_duration_seconds", 0.0)),
            "/Auto/PublishQueueLagSeconds": float(getattr(svc, "_last_dbus_publish_queue_lag_seconds", 0.0)),
            "/Auto/PublishQueueDropped": int(getattr(svc, "_dbus_publish_dropped_count", 0)),
            "/Auto/WriteCommandDurationSeconds": float(getattr(svc, "_last_write_command_duration_seconds", 0.0)),
            "/Auto/WriteCommandQueueLagSeconds": float(getattr(svc, "_last_write_command_queue_lag_seconds", 0.0)),
            "/Auto/MainloopHeartbeatAge": heartbeat_age,
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
            "/Auto/DecisionReason": str(self.service._last_health_reason),
            "/Auto/DecisionState": auto_state,
            "/Auto/DecisionStateCode": auto_state_code,
            "/Auto/DecisionRelayIntent": self._auto_decision_relay_intent(metrics),
            "/Auto/DecisionSurplusWatts": self._auto_decision_metric_float(metrics, "surplus"),
            "/Auto/DecisionGridWatts": self._auto_decision_metric_float(metrics, "grid"),
            "/Auto/DecisionSocPercent": self._auto_decision_metric_float(metrics, "soc"),
            "/Auto/DecisionStartThresholdWatts": self._auto_decision_metric_float(metrics, "start_threshold"),
            "/Auto/DecisionStopThresholdWatts": self._auto_decision_metric_float(metrics, "stop_threshold"),
            "/Auto/DecisionProfile": self._auto_decision_metric_text(metrics, "profile"),
            "/Auto/DecisionThresholdMode": self._auto_decision_metric_text(metrics, "threshold_mode"),
        }
