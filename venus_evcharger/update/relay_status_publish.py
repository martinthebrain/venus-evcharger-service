# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
# pyright: reportAttributeAccessIssue=false
"""Relay decision, status derivation, and live publish helpers for the update cycle."""

from __future__ import annotations

from typing import Any

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.core.common import evse_fault_reason


class _RelayStatusPublishMixin:
    """Apply relay intent, derive outward status, and publish live state."""

    def apply_relay_decision(
        self,
        desired_relay: bool,
        relay_on: bool,
        pm_status: dict[str, Any],
        power: float,
        current: float,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        svc = self.service
        pm_confirmed = self._pm_status_confirmed(pm_status)
        if self._relay_decision_noop(svc, desired_relay, relay_on):
            return relay_on, power, current, pm_confirmed
        self._log_auto_relay_change_if_needed(svc, desired_relay, auto_mode_active)
        pending_result = self._unsuccessful_relay_decision_result(
            svc,
            desired_relay,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
        )
        if pending_result is not None:
            return pending_result
        return self._successful_relay_decision_result(desired_relay, now)

    def _log_auto_relay_change_if_needed(self, svc: Any, desired_relay: bool, auto_mode_active: bool) -> None:
        if auto_mode_active and svc.auto_audit_log:
            self.log_auto_relay_change(svc, desired_relay)

    def _unsuccessful_relay_decision_result(
        self,
        svc: Any,
        desired_relay: bool,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
    ) -> tuple[bool, float, float, bool] | None:
        applied = self._apply_relay_target_best_effort(svc, desired_relay, now)
        if applied:
            return None
        return relay_on, power, current, pm_confirmed

    def _successful_relay_decision_result(self, desired_relay: bool, now: float) -> tuple[bool, float, float, bool]:
        relay_on = bool(desired_relay)
        self._publish_local_pm_status_best_effort(relay_on, now)
        return relay_on, 0.0, 0.0, False

    def _apply_relay_target_best_effort(self, svc: Any, desired_relay: bool, now: float) -> bool | None:
        try:
            return self._relay_apply_result(self._apply_enabled_target(svc, desired_relay, now))
        except Exception as error:
            self._handle_relay_decision_failure(svc, error)
            return None

    @staticmethod
    def _relay_apply_result(value: Any) -> bool:
        if not isinstance(value, bool):
            raise TypeError(f"_apply_enabled_target must return bool, got {type(value).__name__}")
        return value

    @classmethod
    def _relay_decision_noop(cls, svc: Any, desired_relay: bool, relay_on: bool) -> bool:
        if desired_relay == relay_on:
            return True
        return getattr(svc, "_relay_sync_expected_state", None) == bool(desired_relay)

    @classmethod
    def _handle_relay_decision_failure(cls, svc: Any, error: Exception) -> None:
        source_key = cls._enable_control_source_key(svc)
        source_label = cls._enable_control_label(svc)
        transport_reason = modbus_transport_issue_reason(error)
        if source_key == "charger" and transport_reason is not None:
            cls._remember_charger_transport_issue(svc, transport_reason, "enable", error)
            cls._remember_charger_retry(svc, transport_reason, "enable")
        svc._mark_failure(source_key)
        svc._warning_throttled(
            f"{source_key}-switch-failed",
            svc.auto_shelly_soft_fail_seconds,
            "%s switch request failed: %s",
            source_label,
            error,
            exc_info=error,
        )

    @classmethod
    def derive_status_code(
        cls,
        svc: Any,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
        health_reason: str | None = None,
    ) -> int:
        hard_fault_status = cls._hard_evse_fault_status_override(svc, health_reason)
        if hard_fault_status is not None:
            return hard_fault_status
        fault_status = cls._charger_fault_status_override(svc, now)
        if fault_status is not None:
            return fault_status
        status_override = cls._charger_status_override(svc, auto_mode_active, now)
        if status_override is not None:
            status_code, status_source = status_override
            svc._last_status_source = status_source
            return int(status_code)
        return cls._fallback_status_code(svc, relay_on, power, auto_mode_active, now)

    @classmethod
    def _charger_fault_status_override(cls, svc: Any, now: float | None = None) -> int | None:
        if cls.charger_health_override(svc, now) != "charger-fault":
            svc._last_charger_fault_active = 0
            return None
        svc._last_status_source = "charger-fault"
        svc._last_charger_fault_active = 1
        return 0

    @staticmethod
    def _evse_fault_status_source(reason: str) -> str:
        return {
            "contactor-feedback-mismatch": "contactor-feedback-fault",
            "contactor-lockout-open": "contactor-lockout-open",
            "contactor-lockout-welded": "contactor-lockout-welded",
        }.get(reason, "evse-fault")

    @classmethod
    def _hard_evse_fault_status_override(
        cls,
        svc: Any,
        health_reason: object | None = None,
    ) -> int | None:
        fault_reason = evse_fault_reason(getattr(svc, "_last_health_reason", None) if health_reason is None else health_reason)
        if fault_reason not in {"contactor-feedback-mismatch", "contactor-lockout-open", "contactor-lockout-welded"}:
            return None
        svc._last_status_source = cls._evse_fault_status_source(fault_reason)
        return 0

    @classmethod
    def _fallback_status_code(
        cls,
        svc: Any,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> int:
        enabled_state = cls._effective_enabled_state(svc, relay_on, now)
        if enabled_state:
            return cls._enabled_fallback_status_code(svc, power)
        return cls._disabled_fallback_status_code(svc, auto_mode_active)

    @staticmethod
    def _enabled_fallback_status_code(svc: Any, power: float) -> int:
        if power >= svc.charging_threshold_watts:
            svc._last_status_source = "charging"
            return 2
        svc._last_status_source = "enabled-idle"
        return int(svc.idle_status)

    @staticmethod
    def _disabled_fallback_status_code(svc: Any, auto_mode_active: bool) -> int:
        svc._last_status_source = "auto-waiting" if auto_mode_active else "manual-off"
        return 4 if auto_mode_active else 6

    def publish_online_update(
        self,
        pm_status: dict[str, Any],
        status: int,
        energy_forward: float,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
    ) -> bool:
        svc = self.service
        resolved_power = self._fresh_charger_power_readback(svc, now)
        if resolved_power is None:
            resolved_power = float(power)
        resolved_current = self._fresh_charger_actual_current_readback(svc, now)
        if resolved_current is None:
            resolved_current = 0.0
        resolved_energy_forward = self._fresh_charger_energy_readback(svc, now)
        if resolved_energy_forward is None:
            resolved_energy_forward = float(energy_forward)

        phase_data = self._phase_data_for_pm_status(pm_status, resolved_power, voltage, resolved_current)
        total_current = self._total_phase_current(phase_data)
        if resolved_current > 0.0:
            total_current = float(resolved_current)

        changed = False
        changed |= svc._publish_live_measurements(resolved_power, voltage, total_current, phase_data, now)
        changed |= self.update_virtual_state(status, resolved_energy_forward, relay_on)
        return bool(changed)
