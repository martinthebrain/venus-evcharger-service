# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime charger-state sync and retry helpers for Shelly I/O support."""

from __future__ import annotations

import math
from collections.abc import Callable

from venus_evcharger.backend.errors import BACKEND_IO_ERRORS
from venus_evcharger.backend.models import ChargerState, PhaseSelection, phase_selection_count
from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.backend.shelly_io_capabilities import ShellyCapabilities
from venus_evcharger.backend.shelly_io_ports import ShellyRuntimeHost
from venus_evcharger.backend.shelly_io_runtime_cache import ShellyRuntimeCache
from venus_evcharger.backend.shelly_io_types import (
    _ChargerStateBackendLike,
)
from venus_evcharger.core.common import (
    _charger_transport_retry_delay_seconds,
    _fresh_charger_retry_until,
)
from venus_evcharger.core.contracts import exception_detail, finite_float_or_none
from venus_evcharger.ports.readback import TimedChargerState


class ShellyChargerRuntime:
    """Mirror charger readback into runtime state and synthesize retry behavior."""

    def __init__(
        self,
        service: ShellyRuntimeHost,
        cache: ShellyRuntimeCache,
        capabilities: ShellyCapabilities,
        clock: Callable[[], float],
    ) -> None:
        self.service = service
        self.cache = cache
        self.capabilities = capabilities
        self._clock = clock

    def _sync_charger_runtime_state(self, state: ChargerState, now: float | None = None) -> None:
        svc = self.service
        state_at = svc.time_now() if now is None else float(now)
        auto_mode_active = self._auto_mode_active(getattr(svc, "virtual_mode", 0))
        self._store_runtime_charger_snapshot(state, state_at)
        self._sync_virtual_enabled_state(state, auto_mode_active)
        self._sync_virtual_current_target(state, state_at)
        self._sync_runtime_phase_selection_from_charger(state)

    def _store_runtime_charger_snapshot(self, state: ChargerState, state_at: float) -> None:
        svc = self.service
        svc._readback_store.replace_charger(TimedChargerState(state=state, captured_at=state_at))
        svc._last_charger_state_enabled = self._optional_bool(getattr(state, "enabled", None))
        svc._last_charger_state_current_amps = self._optional_float(getattr(state, "current_amps", None))
        svc._last_charger_state_phase_selection = getattr(state, "phase_selection", None)
        svc._last_charger_state_actual_current_amps = self._optional_float(getattr(state, "actual_current_amps", None))
        svc._last_charger_state_power_w = self._optional_float(getattr(state, "power_w", None))
        svc._last_charger_state_energy_kwh = self._optional_float(getattr(state, "energy_kwh", None))
        svc._last_charger_state_status = self.cache._cached_optional_text(getattr(state, "status_text", None))
        svc._last_charger_state_fault = self.cache._cached_optional_text(getattr(state, "fault_text", None))
        svc._last_charger_state_at = state_at

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        return None if value is None else bool(value)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        return finite_float_or_none(value)

    def _auto_mode_active(self, current_mode: object) -> bool:
        return self.service.auto.mode_uses_auto_logic(current_mode)

    def remember_charger_estimate(self, source: str, now: float | None = None) -> None:
        svc = self.service
        captured_at = self._clock() if now is None else float(now)
        svc._last_charger_estimate_source = str(source).strip() or None
        svc._last_charger_estimate_at = captured_at

    def clear_charger_estimate(self) -> None:
        svc = self.service
        svc._last_charger_estimate_source = None
        svc._last_charger_estimate_at = None

    def estimated_phase_voltage_v(self, selection: PhaseSelection) -> float:
        cached_voltage = self._cached_runtime_voltage()
        if cached_voltage is None:
            return 230.0
        return self._phase_voltage_for_selection(selection, cached_voltage)

    def _cached_runtime_voltage(self) -> float | None:
        cached_voltage = finite_float_or_none(getattr(self.service, "_last_voltage", None))
        if cached_voltage is None or cached_voltage <= 0.0:
            return None
        return float(cached_voltage)

    def _phase_voltage_for_selection(self, selection: PhaseSelection, cached_voltage: float) -> float:
        phase_voltage = float(cached_voltage)
        voltage_mode = getattr(self.service, "voltage_mode", None)
        uses_phase_voltage = voltage_mode is None or str(voltage_mode).strip().lower() == "phase"
        if selection != "P1" and not uses_phase_voltage:
            phase_voltage = phase_voltage / math.sqrt(3.0)
        return 230.0 if phase_voltage <= 0.0 else float(phase_voltage)

    @staticmethod
    def _charging_like_status(state: ChargerState) -> bool:
        raw_status = getattr(state, "status_text", None)
        if raw_status is None:
            return False
        status = str(raw_status).strip().lower()
        return status.startswith("charging")

    @classmethod
    def resolved_pm_charger_current(cls, state: ChargerState) -> float | None:
        actual_current = cls._actual_pm_charger_current(state)
        if actual_current is not None:
            return actual_current
        return cls._fallback_pm_charger_current(state)

    @staticmethod
    def _actual_pm_charger_current(state: ChargerState) -> float | None:
        return None if state.actual_current_amps is None else float(state.actual_current_amps)

    @classmethod
    def _fallback_pm_charger_current(cls, state: ChargerState) -> float | None:
        if state.current_amps is None:
            return None
        if state.status_text is not None:
            return float(state.current_amps) if cls._charging_like_status(state) else 0.0
        if state.enabled is False:
            return 0.0
        return float(state.current_amps)

    def estimated_charger_power_w(
        self,
        current_a: float | None,
        phase_selection: PhaseSelection,
    ) -> float | None:
        if current_a is None:
            return None
        return float(current_a) * self.estimated_phase_voltage_v(phase_selection) * float(
            phase_selection_count(phase_selection)
        )

    def sync_estimated_charger_energy_cache(self, energy_kwh: float, power_w: float, now: float) -> None:
        svc = self.service
        svc._charger_estimated_energy_kwh = max(0.0, float(energy_kwh))
        svc._charger_estimated_energy_at = float(now)
        svc._charger_estimated_power_w = max(0.0, float(power_w))

    def integrated_estimated_charger_energy_kwh(self, power_w: float, now: float) -> float:
        svc = self.service
        energy_kwh = finite_float_or_none(getattr(svc, "_charger_estimated_energy_kwh", None)) or 0.0
        last_at = finite_float_or_none(getattr(svc, "_charger_estimated_energy_at", None))
        last_power = finite_float_or_none(getattr(svc, "_charger_estimated_power_w", None))
        if last_at is not None and last_power is not None:
            elapsed_seconds = max(0.0, float(now) - last_at)
            energy_kwh += (max(0.0, float(last_power)) * (elapsed_seconds / 3600.0)) / 1000.0
        self.sync_estimated_charger_energy_cache(energy_kwh, power_w, now)
        return energy_kwh

    def _sync_virtual_enabled_state(self, state: ChargerState, auto_mode_active: bool) -> None:
        if state.enabled is None:
            return
        svc = self.service
        svc.virtual_enable = int(bool(state.enabled))
        if not auto_mode_active:
            svc.virtual_startstop = int(bool(state.enabled))

    def _sync_virtual_current_target(self, state: ChargerState, state_at: float) -> None:
        if state.current_amps is None:
            return
        svc = self.service
        svc.virtual_set_current = float(state.current_amps)
        svc._charger_target_current_amps = float(state.current_amps)
        svc._charger_target_current_applied_at = state_at

    def _sync_runtime_phase_selection_from_charger(self, state: ChargerState) -> None:
        if state.phase_selection is None or self.capabilities.phase_selection_switch_backend() is not None:
            return
        svc = self.service
        self.capabilities.remember_phase_selection_state(
            supported=self.capabilities.charger_supported_phase_selections(),
            requested=getattr(svc, "requested_phase_selection", state.phase_selection),
            active=state.phase_selection,
        )

    @staticmethod
    def _charger_transport_detail(error: BaseException) -> str:
        return exception_detail(error)

    def remember_charger_transport_issue(
        self,
        reason: str,
        source: str,
        error: BaseException,
        now: float | None = None,
    ) -> None:
        svc = self.service
        captured_at = self._clock() if now is None else float(now)
        svc._last_charger_transport_reason = str(reason).strip() or None
        svc._last_charger_transport_source = str(source).strip() or None
        svc._last_charger_transport_detail = self._charger_transport_detail(error)
        svc._last_charger_transport_at = captured_at

    def clear_charger_transport_issue(self) -> None:
        svc = self.service
        svc._last_charger_transport_reason = None
        svc._last_charger_transport_source = None
        svc._last_charger_transport_detail = None
        svc._last_charger_transport_at = None

    def remember_charger_retry(self, reason: str, source: str, now: float | None = None) -> None:
        svc = self.service
        captured_at = self._clock() if now is None else float(now)
        delay_seconds = _charger_transport_retry_delay_seconds(svc, reason)
        self._schedule_charger_retry_backoff(svc, captured_at, delay_seconds)
        svc._charger_retry_reason = str(reason).strip() or None
        svc._charger_retry_source = str(source).strip() or None
        svc._charger_retry_until = captured_at + delay_seconds

    @staticmethod
    def _schedule_charger_retry_backoff(svc: ShellyRuntimeHost, captured_at: float, delay_seconds: float) -> None:
        svc.runtime.delay_source_retry("charger", captured_at, delay_seconds)

    def clear_charger_retry(self) -> None:
        svc = self.service
        svc._charger_retry_reason = None
        svc._charger_retry_source = None
        svc._charger_retry_until = None
        if isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = 0.0

    def charger_retry_active(self, now: float | None = None) -> bool:
        svc = self.service
        current = self._clock() if now is None else float(now)
        return _fresh_charger_retry_until(svc, current) is not None

    def read_charger_state_best_effort(self, now: float | None = None) -> ChargerState | None:
        read_context = self._charger_read_context(now)
        if read_context is None:
            return None
        svc, backend, current = read_context
        try:
            state = backend.read_charger_state()
        except BACKEND_IO_ERRORS as error:
            self._handle_charger_state_read_error(svc, error, current)
            return None
        self._sync_charger_runtime_state(state, now=current)
        self.clear_charger_transport_issue()
        self.clear_charger_retry()
        svc.runtime.mark_recovery("charger", "Charger state reads recovered")
        return state

    def _charger_read_context(
        self,
        now: float | None,
    ) -> tuple[ShellyRuntimeHost, _ChargerStateBackendLike, float] | None:
        svc = self.service
        backend = self.capabilities.charger_state_backend()
        if backend is None:
            svc._readback_store.replace_charger(None)
            return None
        current = self._clock() if now is None else float(now)
        if self.charger_retry_active(current):
            return None
        return svc, backend, current

    def _handle_charger_state_read_error(self, svc: ShellyRuntimeHost, error: BaseException, current: float) -> None:
        transport_reason = modbus_transport_issue_reason(error)
        if transport_reason is not None:
            self.remember_charger_transport_issue(transport_reason, "read", error, current)
            self.remember_charger_retry(transport_reason, "read", current)
        svc.runtime.mark_failure("charger")
        svc.runtime.warning_throttled(
            "charger-state-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Charger state read failed: %s",
            error,
            exc_info=error,
        )


__all__ = ["ShellyChargerRuntime"]
