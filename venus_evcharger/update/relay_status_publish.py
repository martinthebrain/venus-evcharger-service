# SPDX-License-Identifier: GPL-3.0-or-later
"""Relay decision, status derivation, and live publish helpers for the update cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.core.common import evse_fault_reason
from venus_evcharger.core.return_contracts import require_bool
from venus_evcharger.update.learning import _UpdateCycleLearning

RELAY_TARGET_APPLY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class _RelayStatusPublish(_UpdateCycleLearning):
    """Apply relay intent, derive outward status, and publish live state."""

    if TYPE_CHECKING:  # pragma: no cover

        @staticmethod
        def _pm_status_confirmed(pm_status: dict[str, Any]) -> bool: ...

        @staticmethod
        def log_auto_relay_change(svc: Any, desired_relay: bool) -> None: ...

        def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None: ...

        @classmethod
        def _apply_enabled_target(cls, svc: Any, enabled: bool, now: float) -> bool: ...

        @classmethod
        def _enable_control_source_key(cls, svc: Any) -> str: ...

        @classmethod
        def _enable_control_label(cls, svc: Any) -> str: ...

        @classmethod
        def _remember_charger_transport_issue(
            cls,
            svc: Any,
            reason: str,
            source: str,
            error: BaseException,
            now: float | None = None,
        ) -> None: ...

        @classmethod
        def _remember_charger_retry(
            cls,
            svc: Any,
            reason: str,
            source: str,
            now: float | None = None,
        ) -> None: ...

        @classmethod
        def charger_health_override(cls, svc: Any, now: float | None = None) -> str | None: ...

        @classmethod
        def _charger_status_override(
            cls,
            svc: Any,
            auto_mode_active: bool,
            now: float | None = None,
        ) -> tuple[int, str] | None: ...

        @classmethod
        def _effective_enabled_state(cls, svc: Any, relay_on: bool, now: float | None = None) -> bool: ...

        @classmethod
        def _fresh_charger_power_readback(cls, svc: Any, now: float | None = None) -> float | None: ...

        @classmethod
        def _fresh_charger_actual_current_readback(cls, svc: Any, now: float | None = None) -> float | None: ...

        @classmethod
        def _fresh_charger_energy_readback(cls, svc: Any, now: float | None = None) -> float | None: ...

        def _phase_data_for_pm_status(
            self,
            pm_status: dict[str, Any] | None,
            power: float,
            voltage: float,
        ) -> dict[str, dict[str, float]]: ...

        @staticmethod
        def _total_phase_current(phase_data: dict[str, dict[str, float]]) -> float: ...

        def update_virtual_state(self, status: int, current_total_energy: float, relay_on: bool) -> bool: ...

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
        except RELAY_TARGET_APPLY_ERRORS as error:
            self._handle_relay_decision_failure(svc, error)
            return None

    @staticmethod
    def _relay_apply_result(value: object) -> bool:
        return require_bool(value, "_apply_enabled_target")

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

    def _resolved_live_readbacks(
        self,
        power: float,
        energy_forward: float,
        now: float,
    ) -> tuple[float, float, float]:
        svc = self.service
        resolved_power = self._fresh_charger_power_readback(svc, now)
        resolved_current = self._fresh_charger_actual_current_readback(svc, now)
        resolved_energy_forward = self._fresh_charger_energy_readback(svc, now)
        return (
            float(power) if resolved_power is None else resolved_power,
            0.0 if resolved_current is None else resolved_current,
            float(energy_forward) if resolved_energy_forward is None else resolved_energy_forward,
        )

    @classmethod
    def _resolved_total_current(
        cls,
        phase_data: dict[str, dict[str, float]],
        resolved_current: float,
    ) -> float:
        if resolved_current > 0.0:
            return float(resolved_current)
        return cls._total_phase_current(phase_data)

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
        resolved_power, resolved_current, resolved_energy_forward = self._resolved_live_readbacks(
            power,
            energy_forward,
            now,
        )
        phase_data = self._phase_data_for_pm_status(pm_status, resolved_power, voltage)
        total_current = self._resolved_total_current(phase_data, resolved_current)

        measurements_changed = svc._publish_live_measurements(resolved_power, voltage, total_current, phase_data, now)
        state_changed = self.update_virtual_state(status, resolved_energy_forward, relay_on)
        return bool(measurements_changed or state_changed)
