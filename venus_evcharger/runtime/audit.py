# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Venus EV charger service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
import time
from typing import Any

from venus_evcharger.core.common import _fresh_confirmed_relay_output
from venus_evcharger.core.contracts import normalized_auto_state_pair, sanitized_auto_metrics
from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.runtime.audit_fields import (
    _RuntimeAuditFields,
)

ErrorState = dict[str, int]
FailureState = dict[str, bool]
DefaultFactory = Callable[[], Any]

AUDIT_LOG_READ_ERRORS = (OSError, RuntimeError, UnicodeDecodeError)
AUDIT_LOG_WRITE_ERRORS = (OSError, RuntimeError, TypeError, UnicodeEncodeError)


class _RuntimeAudit(_RuntimeAuditFields):

    @staticmethod
    def _relay_state_for_audit(svc: Any) -> int:
        """Return the best-known relay state for audit output."""
        current_time = _RuntimeAudit._callable_time_or_none(getattr(svc, "_time_now", None))
        relay_on = _fresh_confirmed_relay_output(svc, current_time)
        return int(bool(relay_on))

    @staticmethod
    def _auto_audit_reason_detail(svc: Any, reason: str) -> str | None:
        """Return an optional audit detail for broad health reasons."""
        if reason != "auto-stop":
            return None
        stop_reason = getattr(svc, "auto_stop_condition_reason", None)
        if not isinstance(stop_reason, str):
            return None
        detail_map = {
            "auto-stop-surplus": "surplus",
            "auto-stop-grid": "grid",
            "auto-stop-soc": "soc",
        }
        return detail_map.get(stop_reason)

    @staticmethod
    def _bucket_metric(value: Any, *, step: float) -> float | None:
        """Bucket one metric so audit dedupe notices only material changes."""
        if value is None:
            return None
        if step <= 0:
            return float(value)
        return round(float(value) / step) * step

    @classmethod
    def _auto_audit_key(
        cls,
        svc: Any,
        reason: str,
        cached: bool,
    ) -> tuple[object, ...]:
        """Return a de-duplication key for audit entries."""
        metrics = cls._normalized_auto_audit_metrics(svc)
        state, _state_code = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", None),
            getattr(svc, "_last_auto_state_code", None),
        )
        return (
            str(reason),
            cls._auto_audit_reason_detail(svc, reason),
            int(bool(cached)),
            cls._relay_state_for_audit(svc),
            int(getattr(svc, "virtual_mode", 0)),
            int(bool(getattr(svc, "virtual_enable", 0))),
            int(bool(getattr(svc, "virtual_autostart", 0))),
            cls._string_metric(state),
            cls._string_metric(metrics.get("profile")),
            cls._string_metric(metrics.get("stop_alpha_stage")),
            cls._string_metric(metrics.get("threshold_mode")),
            cls._string_metric(metrics.get("learned_charge_power_state")),
            cls._bucket_metric(metrics.get("start_threshold"), step=50.0),
            cls._bucket_metric(metrics.get("stop_threshold"), step=50.0),
            cls._bucket_metric(metrics.get("threshold_scale"), step=0.02),
            cls._backend_value(svc, "backend_mode", "combined"),
            cls._backend_value(svc, "meter_backend_type", "shelly_meter"),
            cls._backend_value(svc, "switch_backend_type", "shelly_contactor_switch"),
            cls._backend_value(svc, "charger_backend_type", "na"),
            cls._bucket_metric(cls._charger_target_for_audit(svc), step=1.0),
            cls._string_metric(cls._charger_transport_reason_for_audit(svc)),
            cls._string_metric(cls._charger_transport_source_for_audit(svc)),
            cls._string_metric(cls._charger_retry_reason_for_audit(svc)),
            cls._string_metric(cls._charger_retry_source_for_audit(svc)),
            cls._string_metric(cls._observed_phase_for_audit(svc)),
            int(cls._phase_mismatch_active_for_audit(svc)),
            cls._string_metric(cls._phase_lockout_target_for_audit(svc)),
            int(cls._phase_lockout_active_for_audit(svc)),
            cls._string_metric(cls._phase_supported_effective_for_audit(svc)),
            int(cls._phase_degraded_active_for_audit(svc)),
            metrics.get("switch_feedback"),
            metrics.get("switch_interlock"),
            int(metrics.get("switch_feedback_mismatch", 0)),
            int(metrics.get("contactor_fault_count", 0)),
            cls._string_metric(metrics.get("contactor_lockout_reason")),
            int(metrics.get("contactor_lockout", 0)),
            int(metrics.get("fault", 0)),
            cls._string_metric(metrics.get("fault_reason")),
            int(metrics.get("recovery", 0)),
        )

    @staticmethod
    def _string_metric(value: Any) -> str | None:
        """Return one optional metric value as normalized text."""
        return None if value is None else str(value)

    @classmethod
    def _format_auto_audit_line(cls, svc: Any, reason: str, cached: bool, now: float) -> str:
        """Return one human-readable audit line describing the current Auto state."""
        fields = cls._auto_audit_display_fields(svc)
        state, _state_code = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", None),
            getattr(svc, "_last_auto_state_code", None),
        )
        local_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        return (
            f"{int(now)}\t{local_time}\t"
            f"reason={reason}\t"
            f"detail={cls._auto_audit_reason_detail(svc, reason) or 'na'}\t"
            f"cached={int(bool(cached))}\t"
            f"state={state}\t"
            f"relay={cls._relay_state_for_audit(svc)}\t"
            f"mode={getattr(svc, 'virtual_mode', 'na')}\t"
            f"enable={int(bool(getattr(svc, 'virtual_enable', 0)))}\t"
            f"autostart={int(bool(getattr(svc, 'virtual_autostart', 0)))}\t"
            f"profile={fields['profile']}\t"
            f"start_threshold={fields['start_threshold']}\t"
            f"stop_threshold={fields['stop_threshold']}\t"
            f"learned_charge_power={fields['learned_charge_power']}\t"
            f"learned_charge_power_state={fields['learned_charge_power_state']}\t"
            f"threshold_scale={fields['threshold_scale']}\t"
            f"threshold_mode={fields['threshold_mode']}\t"
            f"backend_mode={fields['backend_mode']}\t"
            f"meter_backend={fields['meter_backend']}\t"
            f"switch_backend={fields['switch_backend']}\t"
            f"charger_backend={fields['charger_backend']}\t"
            f"charger_target={fields['charger_target']}\t"
            f"charger_transport_reason={fields['charger_transport_reason']}\t"
            f"charger_transport_source={fields['charger_transport_source']}\t"
            f"charger_retry_reason={fields['charger_retry_reason']}\t"
            f"charger_retry_source={fields['charger_retry_source']}\t"
            f"phase_observed={fields['phase_observed']}\t"
            f"phase_mismatch={fields['phase_mismatch']}\t"
            f"phase_lockout_target={fields['phase_lockout_target']}\t"
            f"phase_lockout={fields['phase_lockout']}\t"
            f"phase_effective={fields['phase_effective']}\t"
            f"phase_degraded={fields['phase_degraded']}\t"
            f"switch_feedback={fields['switch_feedback']}\t"
            f"switch_interlock={fields['switch_interlock']}\t"
            f"switch_feedback_mismatch={fields['switch_feedback_mismatch']}\t"
            f"contactor_fault_count={fields['contactor_fault_count']}\t"
            f"contactor_lockout_reason={fields['contactor_lockout_reason']}\t"
            f"contactor_lockout={fields['contactor_lockout']}\t"
            f"fault={fields['fault']}\t"
            f"fault_reason={fields['fault_reason']}\t"
            f"recovery={fields['recovery']}\t"
            f"stop_alpha={fields['stop_alpha']}\t"
            f"stop_alpha_stage={fields['stop_alpha_stage']}\t"
            f"surplus_volatility={fields['surplus_volatility']}\t"
            f"surplus={fields['surplus']}\t"
            f"grid={fields['grid']}\t"
            f"soc={fields['soc']}\n"
        )

    @classmethod
    def _auto_audit_display_fields(cls, svc: Any) -> dict[str, str]:
        """Return the formatted metric values used in one audit line."""
        metrics = cls._normalized_auto_audit_metrics(svc)
        specs = {
            "surplus": ("surplus", "{:.0f}W"),
            "grid": ("grid", "{:.0f}W"),
            "soc": ("soc", "{:.1f}%"),
            "profile": ("profile", None),
            "start_threshold": ("start_threshold", "{:.0f}W"),
            "stop_threshold": ("stop_threshold", "{:.0f}W"),
            "learned_charge_power": ("learned_charge_power", "{:.0f}W"),
            "learned_charge_power_state": ("learned_charge_power_state", None),
            "threshold_scale": ("threshold_scale", "{:.3f}"),
            "threshold_mode": ("threshold_mode", None),
            "backend_mode": ("backend_mode", None),
            "meter_backend": ("meter_backend", None),
            "switch_backend": ("switch_backend", None),
            "charger_backend": ("charger_backend", None),
            "charger_target": ("charger_target", "{:.1f}A"),
            "charger_transport_reason": ("charger_transport_reason", None),
            "charger_transport_source": ("charger_transport_source", None),
            "charger_retry_reason": ("charger_retry_reason", None),
            "charger_retry_source": ("charger_retry_source", None),
            "phase_observed": ("phase_observed", None),
            "phase_mismatch": ("phase_mismatch", None),
            "phase_lockout_target": ("phase_lockout_target", None),
            "phase_lockout": ("phase_lockout", None),
            "phase_effective": ("phase_effective", None),
            "phase_degraded": ("phase_degraded", None),
            "switch_feedback": ("switch_feedback", None),
            "switch_interlock": ("switch_interlock", None),
            "switch_feedback_mismatch": ("switch_feedback_mismatch", None),
            "contactor_fault_count": ("contactor_fault_count", None),
            "contactor_lockout_reason": ("contactor_lockout_reason", None),
            "contactor_lockout": ("contactor_lockout", None),
            "fault": ("fault", None),
            "fault_reason": ("fault_reason", None),
            "recovery": ("recovery", None),
            "stop_alpha": ("stop_alpha", "{:.2f}"),
            "stop_alpha_stage": ("stop_alpha_stage", None),
            "surplus_volatility": ("surplus_volatility", "{:.0f}W"),
        }
        return {name: cls._auto_audit_value_text(metrics.get(key), fmt) for name, (key, fmt) in specs.items()}

    @staticmethod
    def _normalized_auto_audit_metrics(svc: Any) -> dict[str, Any]:
        """Return one sanitized metric payload suitable for outward audit formatting."""
        metrics = sanitized_auto_metrics(getattr(svc, "_last_auto_metrics", {}) or {})
        metrics["backend_mode"] = _RuntimeAudit._backend_value(svc, "backend_mode", "combined")
        metrics["meter_backend"] = _RuntimeAudit._backend_value(
            svc,
            "meter_backend_type",
            "shelly_meter",
        )
        metrics["switch_backend"] = _RuntimeAudit._backend_value(
            svc,
            "switch_backend_type",
            "shelly_contactor_switch",
        )
        metrics["charger_backend"] = _RuntimeAudit._backend_value(
            svc,
            "charger_backend_type",
            "na",
        )
        metrics["charger_target"] = _RuntimeAudit._charger_target_for_audit(svc)
        metrics["charger_transport_reason"] = _RuntimeAudit._charger_transport_reason_for_audit(svc)
        metrics["charger_transport_source"] = _RuntimeAudit._charger_transport_source_for_audit(svc)
        metrics["charger_retry_reason"] = _RuntimeAudit._charger_retry_reason_for_audit(svc)
        metrics["charger_retry_source"] = _RuntimeAudit._charger_retry_source_for_audit(svc)
        metrics["phase_observed"] = _RuntimeAudit._observed_phase_for_audit(svc)
        metrics["phase_mismatch"] = int(_RuntimeAudit._phase_mismatch_active_for_audit(svc))
        metrics["phase_lockout_target"] = _RuntimeAudit._phase_lockout_target_for_audit(svc)
        metrics["phase_lockout"] = int(_RuntimeAudit._phase_lockout_active_for_audit(svc))
        metrics["phase_effective"] = _RuntimeAudit._phase_supported_effective_for_audit(svc)
        metrics["phase_degraded"] = int(_RuntimeAudit._phase_degraded_active_for_audit(svc))
        switch_feedback = _RuntimeAudit._switch_feedback_closed_for_audit(svc)
        switch_interlock = _RuntimeAudit._switch_interlock_ok_for_audit(svc)
        metrics["switch_feedback"] = None if switch_feedback is None else int(switch_feedback)
        metrics["switch_interlock"] = None if switch_interlock is None else int(switch_interlock)
        metrics["switch_feedback_mismatch"] = int(_RuntimeAudit._switch_feedback_mismatch_for_audit(svc))
        metrics["contactor_fault_count"] = _RuntimeAudit._contactor_fault_count_for_audit(svc)
        metrics["contactor_lockout_reason"] = _RuntimeAudit._contactor_lockout_reason_for_audit(svc)
        metrics["contactor_lockout"] = int(_RuntimeAudit._contactor_lockout_active_for_audit(svc))
        metrics["fault"] = int(_RuntimeAudit._evse_fault_active_for_audit(svc))
        metrics["fault_reason"] = _RuntimeAudit._evse_fault_reason_for_audit(svc)
        metrics["recovery"] = int(_RuntimeAudit._recovery_active_for_audit(svc))
        return metrics

    @staticmethod
    def _auto_audit_value_text(value: Any, fmt: str | None) -> str:
        """Return the human-readable text for one audit metric value."""
        if value is None:
            return "na"
        if fmt is None:
            return str(value)
        return fmt.format(float(value))

    @staticmethod
    def _prune_auto_audit_payload(lines: list[str], cutoff_epoch: float) -> list[str]:
        """Keep only audit entries newer than the supplied cutoff epoch."""
        kept_lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                epoch_text = line.split("\t", 1)[0]
                if float(epoch_text) >= cutoff_epoch:
                    kept_lines.append(line)
            except (TypeError, ValueError):
                kept_lines.append(line)
        return kept_lines

    def _cleanup_auto_audit_log(self, now: float) -> None:
        """Prune old audit entries on a throttled cadence."""
        svc = self.service
        path = getattr(svc, "auto_audit_log_path", "").strip()
        if not self._auto_audit_cleanup_due(path, now):
            return
        svc._last_auto_audit_cleanup_at = now
        cutoff_epoch = self._auto_audit_cutoff_epoch(svc, now)
        if cutoff_epoch is None:
            return
        lines = self._load_auto_audit_lines(path)
        if lines is None:
            return
        kept_lines = self._prune_auto_audit_payload(lines, cutoff_epoch)
        if kept_lines == lines:
            return
        self._write_pruned_auto_audit_lines(path, kept_lines)

    def _auto_audit_cleanup_due(self, path: str, now: float) -> bool:
        """Return whether audit cleanup should run on this cycle."""
        svc = self.service
        if not path:
            return False
        return (now - float(getattr(svc, "_last_auto_audit_cleanup_at", 0.0))) >= 300.0

    @staticmethod
    def _auto_audit_cutoff_epoch(svc: Any, now: float) -> float | None:
        """Return the cutoff epoch for retained audit entries."""
        max_age_hours = float(getattr(svc, "auto_audit_log_max_age_hours", 168.0))
        if max_age_hours <= 0:
            return None
        return now - (max_age_hours * 3600.0)

    @staticmethod
    def _load_auto_audit_lines(path: str) -> list[str] | None:
        """Load existing audit lines, ignoring missing or unreadable files."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.readlines()
        except FileNotFoundError:
            return None
        except AUDIT_LOG_READ_ERRORS as error:
            logging.debug("Auto audit cleanup skipped for %s: %s", path, error)
            return None

    @staticmethod
    def _write_pruned_auto_audit_lines(path: str, kept_lines: list[str]) -> None:
        """Persist one pruned audit payload with debug-only error handling."""
        try:
            write_text_atomically(path, "".join(kept_lines))
        except AUDIT_LOG_WRITE_ERRORS as error:
            logging.debug("Unable to prune auto audit log %s: %s", path, error)

    @staticmethod
    def _auto_audit_repeat_suppressed(
        audit_key: tuple[Any, ...],
        last_audit_key: tuple[Any, ...] | None,
        repeat_seconds: float,
        last_audit_event_at: float | None,
        now: float,
    ) -> bool:
        """Return whether one duplicate audit event should stay suppressed."""
        if audit_key != last_audit_key:
            return False
        if repeat_seconds <= 0:
            return True
        return last_audit_event_at is not None and (now - float(last_audit_event_at)) < repeat_seconds

    @staticmethod
    def _write_auto_audit_line(path: str, line: str) -> None:
        """Append one formatted audit line to disk."""
        log_dir = os.path.dirname(path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> None:
        """Append one audit entry when the Auto reason changes or stays active for long."""
        svc = self.service
        self.ensure_observability_state()
        if not getattr(svc, "auto_audit_log", False):
            return
        now = svc._time_now()
        audit_key = self._auto_audit_key(svc, reason, cached)
        repeat_seconds = float(getattr(svc, "auto_audit_log_repeat_seconds", 30.0))
        last_audit_key = getattr(svc, "_last_auto_audit_key", None)
        last_audit_event_at = getattr(svc, "_last_auto_audit_event_at", None)
        if self._auto_audit_repeat_suppressed(
            audit_key,
            last_audit_key,
            repeat_seconds,
            last_audit_event_at,
            now,
        ):
            self._cleanup_auto_audit_log(now)
            return
        self._cleanup_auto_audit_log(now)
        path = getattr(svc, "auto_audit_log_path", "").strip()
        if not path:
            return
        try:
            self._write_auto_audit_line(path, self._format_auto_audit_line(svc, reason, cached, now))
            svc._last_auto_audit_key = audit_key
            svc._last_auto_audit_event_at = now
        except AUDIT_LOG_WRITE_ERRORS as error:
            logging.debug("Unable to write auto audit log %s: %s", path, error)
__all__ = ["_RuntimeAudit"]
