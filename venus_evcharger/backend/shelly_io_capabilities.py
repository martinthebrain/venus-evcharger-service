# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend-capability and direct-switch helpers for Shelly I/O support."""

from __future__ import annotations

from collections.abc import Callable

from venus_evcharger.backend.config import backend_mode_for_service
from venus_evcharger.backend.errors import BACKEND_OPTIONAL_CAPABILITY_ERRORS
from venus_evcharger.backend.models import PhaseSelection, SwitchState
from venus_evcharger.backend.shelly_io_ports import ShellyCapabilityHost
from venus_evcharger.backend.shelly_io_types import (
    _ChargerStateBackendLike,
    _EnableBackendLike,
    _MeterBackendLike,
    _PhaseSelectionBackendLike,
    is_charger_state_backend,
    is_enable_backend,
    is_meter_backend,
    is_phase_selection_backend,
    is_switch_capabilities_backend,
    normalize_phase_value,
    normalize_supported_phase_tuple,
)
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.ports.readback import TimedSwitchState


class ShellyCapabilities:
    """Expose split-backend discovery and direct-switch safety helpers."""

    def __init__(self, service: ShellyCapabilityHost, clock: Callable[[], float]) -> None:
        self.service = service
        self._clock = clock

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        return None if value is None else bool(value)

    def uses_split_backends(self) -> bool:
        return backend_mode_for_service(self.service) == "split"

    def _service_supported_phase_selections(self) -> tuple[PhaseSelection, ...]:
        return normalize_supported_phase_tuple(getattr(self.service, "supported_phase_selections", None))

    @staticmethod
    def _settings_supported_phase_selections(backend: object) -> tuple[PhaseSelection, ...] | None:
        settings = getattr(backend, "settings", None)
        if settings is None or not hasattr(settings, "supported_phase_selections"):
            return None
        return normalize_supported_phase_tuple(getattr(settings, "supported_phase_selections"))

    def split_meter_backend(self) -> _MeterBackendLike | None:
        if not self.uses_split_backends():
            return None
        backend = getattr(self.service, "_meter_backend", None)
        return backend if is_meter_backend(backend) else None

    def split_switch_backend(self) -> _EnableBackendLike | None:
        if not self.uses_split_backends():
            return None
        backend = getattr(self.service, "_switch_backend", None)
        return backend if is_enable_backend(backend) else None

    def split_enable_backend(self) -> _EnableBackendLike | None:
        backend = self.split_switch_backend()
        if backend is not None:
            return backend
        if not self.uses_split_backends():
            return None
        charger_backend = getattr(self.service, "_charger_backend", None)
        return charger_backend if is_enable_backend(charger_backend) else None

    def split_enable_source_key(self) -> str:
        backend = self.split_enable_backend()
        if backend is not None and backend is getattr(self.service, "_charger_backend", None):
            return "charger"
        return "shelly"

    def split_enable_source_label(self) -> str:
        return "charger backend" if self.split_enable_source_key() == "charger" else "Shelly relay"

    def phase_selection_switch_backend(self) -> _PhaseSelectionBackendLike | None:
        backend = getattr(self.service, "_switch_backend", None)
        return backend if is_phase_selection_backend(backend) else None

    def _phase_selection_charger_backend(self) -> _PhaseSelectionBackendLike | None:
        backend = getattr(self.service, "_charger_backend", None)
        return backend if is_phase_selection_backend(backend) else None

    def _charger_supports_phase_selection(self, selection: PhaseSelection) -> bool:
        backend = self._phase_selection_charger_backend()
        if backend is None:
            return False
        supported = self._settings_supported_phase_selections(backend)
        if supported is None:
            return True
        return selection in supported

    def charger_state_backend(self) -> _ChargerStateBackendLike | None:
        backend = getattr(self.service, "_charger_backend", None)
        return backend if is_charger_state_backend(backend) else None

    def charger_supported_phase_selections(self) -> tuple[PhaseSelection, ...]:
        backend = getattr(self.service, "_charger_backend", None)
        supported = self._settings_supported_phase_selections(backend)
        return supported if supported is not None else self._service_supported_phase_selections()

    def _phase_switch_capabilities(self) -> object | None:
        backend = getattr(self.service, "_switch_backend", None)
        if not is_switch_capabilities_backend(backend):
            return None
        try:
            return backend.capabilities()
        except BACKEND_OPTIONAL_CAPABILITY_ERRORS:
            return None

    def _switching_mode(self) -> str:
        capabilities = self._phase_switch_capabilities()
        mode = str(getattr(capabilities, "switching_mode", None)).strip().lower()
        return "contactor" if mode == "contactor" else "direct"

    def _max_direct_switch_power_w(self) -> float | None:
        if self._switching_mode() == "contactor":
            return None
        capabilities = self._phase_switch_capabilities()
        limit = finite_float_or_none(getattr(capabilities, "max_direct_switch_power_w", None))
        return None if limit is None or limit <= 0.0 else float(limit)

    def _current_confirmed_switch_load_power_w(self) -> float | None:
        svc = self.service
        if not bool(svc._last_pm_status_confirmed):
            return None
        pm_status = svc._last_pm_status
        if not isinstance(pm_status, dict):
            return None
        power_w = finite_float_or_none(pm_status.get("apower"))
        return None if power_w is None else abs(float(power_w))

    def _direct_switch_warning_context(self, relay_on: bool) -> tuple[float, float] | None:
        if bool(relay_on):
            return None
        limit_w = self._max_direct_switch_power_w()
        if limit_w is None:
            return None
        power_w = self._current_confirmed_switch_load_power_w()
        if power_w is None or power_w <= limit_w:
            return None
        return power_w, limit_w

    def _direct_switch_warning_interval(self) -> float:
        seconds = finite_float_or_none(getattr(self.service, "auto_shelly_soft_fail_seconds", None))
        if seconds is None or seconds == 0.0:
            seconds = 30.0
        return max(1.0, float(seconds))

    def warn_if_direct_switching_under_load(self, relay_on: bool) -> None:
        warning_context = self._direct_switch_warning_context(relay_on)
        if warning_context is None:
            return
        power_w, limit_w = warning_context
        svc = self.service
        svc.runtime.warning_throttled(
            "direct-switch-under-load",
            self._direct_switch_warning_interval(),
            "Direct Shelly relay OFF requested at %.1fW above configured direct switch limit %.1fW; consider switching_mode=contactor",
            power_w,
            limit_w,
        )

    def remember_phase_selection_state(
        self,
        *,
        active: object | None = None,
        requested: object | None = None,
        supported: object | None = None,
    ) -> None:
        svc = self.service
        supported_default = self._service_supported_phase_selections()
        normalized_supported = (
            normalize_supported_phase_tuple(supported, supported_default)
            if supported is not None
            else supported_default
        )
        svc.supported_phase_selections = normalized_supported
        default_phase_selection = normalized_supported[0]
        requested_value = self._phase_state_value(
            explicit=requested,
            attribute="requested_phase_selection",
        )
        normalized_requested = normalize_phase_value(requested_value, default_phase_selection)
        svc.requested_phase_selection = normalized_requested
        active_value = self._phase_state_value(
            explicit=active,
            attribute="active_phase_selection",
        )
        svc.active_phase_selection = normalize_phase_value(active_value, normalized_requested)

    def _phase_state_value(self, *, explicit: object | None, attribute: str) -> object | None:
        if explicit is not None:
            return explicit
        if hasattr(self.service, attribute):
            value: object = getattr(self.service, attribute)
            return value
        return None

    def store_runtime_switch_snapshot(self, switch_state: SwitchState | None, now: float | None = None) -> None:
        svc = self.service
        feedback_closed, interlock_ok = self._switch_snapshot_values(switch_state)
        snapshot_at = self._clock() if now is None else float(now)
        svc._readback_store.replace_switch(
            None if switch_state is None else TimedSwitchState(state=switch_state, captured_at=snapshot_at)
        )
        svc._last_switch_feedback_closed = feedback_closed
        svc._last_switch_interlock_ok = interlock_ok
        svc._last_switch_feedback_at = self._switch_snapshot_timestamp(feedback_closed, interlock_ok, now)

    def _switch_snapshot_values(self, switch_state: SwitchState | None) -> tuple[bool | None, bool | None]:
        feedback_value = None if switch_state is None else getattr(switch_state, "feedback_closed", None)
        interlock_value = None if switch_state is None else getattr(switch_state, "interlock_ok", None)
        return self._optional_bool(feedback_value), self._optional_bool(interlock_value)

    def _switch_snapshot_timestamp(
        self,
        feedback_closed: bool | None,
        interlock_ok: bool | None,
        now: float | None,
    ) -> float | None:
        if feedback_closed is None and interlock_ok is None:
            return None
        return self._clock() if now is None else float(now)

    def split_switch_supported_phase_selections(self) -> tuple[PhaseSelection, ...]:
        capabilities = self._phase_switch_capabilities()
        if capabilities is None:
            if getattr(self.service, "_switch_backend", None) is None:
                return tuple(self.charger_supported_phase_selections())
            return self._service_supported_phase_selections()
        if hasattr(capabilities, "supported_phase_selections"):
            return normalize_supported_phase_tuple(getattr(capabilities, "supported_phase_selections"))
        return self._service_supported_phase_selections()

    def phase_selection_requires_pause(self) -> bool:
        capabilities = self._phase_switch_capabilities()
        return bool(getattr(capabilities, "requires_charge_pause_for_phase_change", None))

    def set_phase_selection(self, selection: object) -> PhaseSelection:
        supported_phase_selections = self.split_switch_supported_phase_selections()
        default_phase_selection = supported_phase_selections[0]
        normalized_selection = normalize_phase_value(selection, default_phase_selection)
        if normalized_selection not in supported_phase_selections:
            raise ValueError(
                f"Unsupported phase selection '{selection}' for configured backend "
                f"(supported: {','.join(supported_phase_selections)})"
            )

        switch_backend = self.phase_selection_switch_backend()
        if switch_backend is not None:
            switch_backend.set_phase_selection(normalized_selection)

        charger_backend = self._phase_selection_charger_backend()
        if charger_backend is not None and self._charger_supports_phase_selection(normalized_selection):
            charger_backend.set_phase_selection(normalized_selection)

        self.remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=normalized_selection,
            active=normalized_selection,
        )
        return normalized_selection


__all__ = ["ShellyCapabilities"]
