# SPDX-License-Identifier: GPL-3.0-or-later
"""Relay decisions and outward status publishing component."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.core.common import evse_fault_reason
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.core.return_contracts import require_bool
from venus_evcharger.update.readback_resolver import FreshReadbacks
from venus_evcharger.update.relay_charger_current import (
    ChargerControlService,
    ChargerRuntimePort,
    ChargerTargetController,
)
from venus_evcharger.update.relay_charger_health import ChargerHealthMonitor
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker
from venus_evcharger.update.relay_phase_publish import (
    RelayTelemetry,
    RelayTelemetryRuntimePort,
    RelayTelemetryService,
)

RELAY_TARGET_APPLY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class StatusReadbackPort(Protocol):
    def resolve(self, now: float | None = None) -> FreshReadbacks: ...


class StatusRuntimePort(Protocol):
    def mark_failure(self, source_key: str) -> None: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...


class StatusStatePort(Protocol):
    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: dict[str, dict[str, float]],
        now: float,
    ) -> bool: ...


class RelayStatusRuntimePort(StatusRuntimePort, ChargerRuntimePort, RelayTelemetryRuntimePort, Protocol):
    """Runtime effects required while applying and publishing relay state."""


class RelayStatusService(ChargerControlService, RelayTelemetryService, Protocol):
    @property
    def runtime(self) -> RelayStatusRuntimePort: ...

    @property
    def state(self) -> StatusStatePort: ...

    @property
    def _readback_resolver(self) -> StatusReadbackPort: ...

    auto_audit_log: bool
    auto_shelly_soft_fail_seconds: float
    charging_threshold_watts: float
    idle_status: int
    _last_health_reason: str
    _last_status_source: str
    _last_charger_fault_active: int


class VirtualStatePublisher(Protocol):
    def update_virtual_state(self, status: int, current_total_energy: float, relay_on: bool) -> bool: ...


class RelayStatusPublisher:
    """Apply relay intent, derive outward status, and publish live state."""

    def __init__(
        self,
        telemetry: RelayTelemetry,
        targets: ChargerTargetController,
        health: ChargerHealthMonitor,
        transport: ChargerTransportTracker,
        virtual_state: VirtualStatePublisher,
    ) -> None:
        self._telemetry = telemetry
        self._targets = targets
        self._health = health
        self._transport = transport
        self._virtual_state = virtual_state

    def apply_relay_decision(
        self,
        svc: RelayStatusService,
        desired_relay: bool,
        relay_on: bool,
        pm_status: dict[str, object],
        power: float,
        current: float,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        pm_confirmed = self._telemetry.pm_status_confirmed(pm_status)
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
        return self._successful_relay_decision_result(svc, desired_relay, now)

    def _log_auto_relay_change_if_needed(self, svc: RelayStatusService, desired_relay: bool, auto_mode_active: bool) -> None:
        if auto_mode_active and svc.auto_audit_log:
            self._telemetry.log_auto_relay_change(svc, desired_relay)

    def _unsuccessful_relay_decision_result(
        self,
        svc: RelayStatusService,
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

    def _successful_relay_decision_result(
        self,
        svc: RelayStatusService,
        desired_relay: bool,
        now: float,
    ) -> tuple[bool, float, float, bool]:
        relay_on = bool(desired_relay)
        self._telemetry.publish_local_pm_status_best_effort(svc, relay_on, now)
        return relay_on, 0.0, 0.0, False

    def _apply_relay_target_best_effort(self, svc: RelayStatusService, desired_relay: bool, now: float) -> bool | None:
        try:
            return self._relay_apply_result(self._targets.apply_enabled_target(svc, desired_relay, now))
        except RELAY_TARGET_APPLY_ERRORS as error:
            self._handle_relay_decision_failure(svc, error)
            return None

    @staticmethod
    def _relay_apply_result(value: object) -> bool:
        return require_bool(value, "_apply_enabled_target")

    def _relay_decision_noop(self, svc: RelayStatusService, desired_relay: bool, relay_on: bool) -> bool:
        if desired_relay == relay_on:
            return True
        return getattr(svc, "_relay_sync_expected_state", None) == bool(desired_relay)

    def _handle_relay_decision_failure(self, svc: RelayStatusService, error: Exception) -> None:
        source_key = self._health._enable_control_source_key(svc)
        source_label = self._health._enable_control_label(svc)
        transport_reason = modbus_transport_issue_reason(error)
        if source_key == "charger" and transport_reason is not None:
            self._transport.remember_issue(svc, transport_reason, "enable", error)
            self._transport.remember_retry(svc, transport_reason, "enable")
        svc.runtime.mark_failure(source_key)
        svc.runtime.warning_throttled(
            f"{source_key}-switch-failed",
            svc.auto_shelly_soft_fail_seconds,
            "%s switch request failed: %s",
            source_label,
            error,
            exc_info=error,
        )

    def derive_status_code(
        self,
        svc: RelayStatusService,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
        health_reason: str | None = None,
    ) -> int:
        hard_fault_status = self._hard_evse_fault_status_override(svc, health_reason)
        if hard_fault_status is not None:
            return hard_fault_status
        fault_status = self._charger_fault_status_override(svc, now)
        if fault_status is not None:
            return fault_status
        status_override = self._health._charger_status_override(svc, auto_mode_active, now)
        if status_override is not None:
            status_code, status_source = status_override
            svc._last_status_source = status_source
            return int(status_code)
        return self._fallback_status_code(svc, relay_on, power, auto_mode_active, now)

    def _charger_fault_status_override(self, svc: RelayStatusService, now: float | None = None) -> int | None:
        if self._health.charger_health_override(svc, now) != "charger-fault":
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

    def _hard_evse_fault_status_override(
        self,
        svc: RelayStatusService,
        health_reason: object | None = None,
    ) -> int | None:
        fault_reason = evse_fault_reason(getattr(svc, "_last_health_reason", None) if health_reason is None else health_reason)
        if fault_reason not in {"contactor-feedback-mismatch", "contactor-lockout-open", "contactor-lockout-welded"}:
            return None
        svc._last_status_source = self._evse_fault_status_source(fault_reason)
        return 0

    def _fallback_status_code(
        self,
        svc: RelayStatusService,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> int:
        enabled_state = self._health._effective_enabled_state(svc, relay_on, now)
        if enabled_state:
            return self._enabled_fallback_status_code(svc, power)
        return self._disabled_fallback_status_code(svc, auto_mode_active)

    @staticmethod
    def _enabled_fallback_status_code(svc: RelayStatusService, power: float) -> int:
        if power >= svc.charging_threshold_watts:
            svc._last_status_source = "charging"
            return 2
        svc._last_status_source = "enabled-idle"
        return int(svc.idle_status)

    @staticmethod
    def _disabled_fallback_status_code(svc: RelayStatusService, auto_mode_active: bool) -> int:
        svc._last_status_source = "auto-waiting" if auto_mode_active else "manual-off"
        return 4 if auto_mode_active else 6

    def _resolved_live_readbacks(
        self,
        svc: RelayStatusService,
        power: float,
        energy_forward: float,
        now: float,
    ) -> tuple[float, float, float]:
        readback = svc._readback_resolver.resolve(now).charger
        if readback is None:
            return float(power), 0.0, float(energy_forward)
        state = readback.state
        return (
            self._readback_or_default(state.power_w, power),
            self._readback_or_default(state.actual_current_amps, 0.0),
            self._readback_or_default(state.energy_kwh, energy_forward),
        )

    def _readback_or_default(self, value: object, default: float) -> float:
        normalized = self._non_negative_readback(value)
        return float(default) if normalized is None else normalized

    @staticmethod
    def _non_negative_readback(value: object) -> float | None:
        normalized = finite_float_or_none(value)
        return None if normalized is None else max(0.0, normalized)

    def _resolved_total_current(
        self,
        phase_data: dict[str, dict[str, float]],
        resolved_current: float,
    ) -> float:
        if resolved_current > 0.0:
            return float(resolved_current)
        return self._total_phase_current(phase_data)

    @staticmethod
    def _total_phase_current(phase_data: dict[str, dict[str, float]]) -> float:
        return sum(phase_data[phase_name]["current"] for phase_name in ("L1", "L2", "L3"))

    def publish_online_update(
        self,
        svc: RelayStatusService,
        pm_status: dict[str, object],
        status: int,
        energy_forward: float,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
    ) -> bool:
        resolved_power, resolved_current, resolved_energy_forward = self._resolved_live_readbacks(
            svc,
            power,
            energy_forward,
            now,
        )
        phase_data = self._telemetry._phase_data_for_pm_status(svc, pm_status, resolved_power, voltage)
        total_current = self._resolved_total_current(phase_data, resolved_current)

        measurements_changed = svc.state.publish_live_measurements(
            resolved_power,
            voltage,
            total_current,
            phase_data,
            now,
        )
        state_changed = self._virtual_state.update_virtual_state(status, resolved_energy_forward, relay_on)
        return bool(measurements_changed or state_changed)


__all__ = ["RelayStatusPublisher", "RelayStatusService", "VirtualStatePublisher"]
