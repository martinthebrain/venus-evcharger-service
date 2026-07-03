# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend-capability and direct-switch helpers for Shelly I/O support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from venus_evcharger.backend.config import backend_mode_for_service
from venus_evcharger.backend.errors import BACKEND_OPTIONAL_CAPABILITY_ERRORS
from venus_evcharger.backend.models import PhaseSelection
from venus_evcharger.backend.shelly_io_requests import ShellyIoRequests
from venus_evcharger.backend.shelly_io_types import (
    ShellyIoHost,
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


class ShellyIoCapabilities(ShellyIoRequests):
    """Expose split-backend discovery and direct-switch safety helpers."""

    if TYPE_CHECKING:
        service: ShellyIoHost

        @staticmethod
        def _optional_bool(value: object) -> bool | None: ...

        def _runtime_now(self) -> float: ...

    def _uses_split_backends(self) -> bool:
        return backend_mode_for_service(self.service, "combined") == "split"

    def _split_meter_backend(self) -> _MeterBackendLike | None:
        if not self._uses_split_backends():
            return None
        backend = getattr(self.service, "_meter_backend", None)
        return backend if is_meter_backend(backend) else None

    def _split_switch_backend(self) -> _EnableBackendLike | None:
        if not self._uses_split_backends():
            return None
        backend = getattr(self.service, "_switch_backend", None)
        return backend if is_enable_backend(backend) else None

    def _split_enable_backend(self) -> _EnableBackendLike | None:
        backend = self._split_switch_backend()
        if backend is not None:
            return backend
        if not self._uses_split_backends():
            return None
        charger_backend = getattr(self.service, "_charger_backend", None)
        return charger_backend if is_enable_backend(charger_backend) else None

    def _split_enable_source_key(self) -> str:
        backend = self._split_enable_backend()
        if backend is not None and backend is getattr(self.service, "_charger_backend", None):
            return "charger"
        return "shelly"

    def _split_enable_source_label(self) -> str:
        return "charger backend" if self._split_enable_source_key() == "charger" else "Shelly relay"

    def _phase_selection_switch_backend(self) -> _PhaseSelectionBackendLike | None:
        backend = getattr(self.service, "_switch_backend", None)
        return backend if is_phase_selection_backend(backend) else None

    def _phase_selection_charger_backend(self) -> _PhaseSelectionBackendLike | None:
        backend = getattr(self.service, "_charger_backend", None)
        return backend if is_phase_selection_backend(backend) else None

    def _charger_supports_phase_selection(self, selection: PhaseSelection) -> bool:
        backend = self._phase_selection_charger_backend()
        if backend is None:
            return False
        settings = getattr(backend, "settings", None)
        if settings is None or not hasattr(settings, "supported_phase_selections"):
            return True
        supported = normalize_supported_phase_tuple(
            getattr(settings, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        return selection in supported

    def _charger_state_backend(self) -> _ChargerStateBackendLike | None:
        backend = getattr(self.service, "_charger_backend", None)
        return backend if is_charger_state_backend(backend) else None

    def _charger_supported_phase_selections(self) -> tuple[PhaseSelection, ...]:
        backend = getattr(self.service, "_charger_backend", None)
        settings = getattr(backend, "settings", None)
        return normalize_supported_phase_tuple(
            getattr(
                settings,
                "supported_phase_selections",
                getattr(self.service, "supported_phase_selections", ("P1",)),
            ),
            ("P1",),
        )

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
        mode = str(getattr(capabilities, "switching_mode", "direct")).strip().lower()
        return "contactor" if mode == "contactor" else "direct"

    def _max_direct_switch_power_w(self) -> float | None:
        if self._switching_mode() == "contactor":
            return None
        capabilities = self._phase_switch_capabilities()
        limit = finite_float_or_none(getattr(capabilities, "max_direct_switch_power_w", None))
        return None if limit is None or limit <= 0.0 else float(limit)

    def _current_confirmed_switch_load_power_w(self) -> float | None:
        svc = self.service
        if not bool(getattr(svc, "_last_pm_status_confirmed", False)):
            return None
        pm_status = getattr(svc, "_last_pm_status", None)
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
        return max(
            1.0,
            float(getattr(self.service, "auto_shelly_soft_fail_seconds", 30.0) or 30.0),
        )

    def _warn_if_direct_switching_under_load(self, relay_on: bool) -> None:
        warning_context = self._direct_switch_warning_context(relay_on)
        if warning_context is None:
            return
        power_w, limit_w = warning_context
        svc = self.service
        warning = getattr(svc, "_warning_throttled", None)
        if not callable(warning):
            return
        warning(
            "direct-switch-under-load",
            self._direct_switch_warning_interval(),
            "Direct Shelly relay OFF requested at %.1fW above configured direct switch limit %.1fW; consider switching_mode=contactor",
            power_w,
            limit_w,
        )

    def _remember_phase_selection_state(
        self,
        *,
        active: object | None = None,
        requested: object | None = None,
        supported: object | None = None,
    ) -> None:
        svc = self.service
        supported_default = tuple(getattr(svc, "supported_phase_selections", ("P1",)))
        normalized_supported = normalize_supported_phase_tuple(
            supported if supported is not None else supported_default,
            supported_default or ("P1",),
        )
        svc.supported_phase_selections = normalized_supported
        default_phase_selection = normalized_supported[0]
        normalized_requested = normalize_phase_value(
            requested if requested is not None else getattr(svc, "requested_phase_selection", default_phase_selection),
            default_phase_selection,
        )
        svc.requested_phase_selection = normalized_requested
        svc.active_phase_selection = normalize_phase_value(
            active if active is not None else getattr(svc, "active_phase_selection", normalized_requested),
            normalized_requested,
        )

    def _store_runtime_switch_snapshot(self, switch_state: object | None, now: float | None = None) -> None:
        svc = self.service
        feedback_closed, interlock_ok = self._switch_snapshot_values(switch_state)
        svc._last_switch_feedback_closed = feedback_closed
        svc._last_switch_interlock_ok = interlock_ok
        svc._last_switch_feedback_at = self._switch_snapshot_timestamp(feedback_closed, interlock_ok, now)

    def _switch_snapshot_values(self, switch_state: object | None) -> tuple[bool | None, bool | None]:
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
        return self._runtime_now() if now is None else float(now)

    def _split_switch_supported_phase_selections(self) -> tuple[PhaseSelection, ...]:
        capabilities = self._phase_switch_capabilities()
        if capabilities is None:
            if getattr(self.service, "_switch_backend", None) is None:
                return tuple(self._charger_supported_phase_selections())
            return normalize_supported_phase_tuple(getattr(self.service, "supported_phase_selections", ("P1",)), ("P1",))
        normalized = normalize_supported_phase_tuple(
            getattr(
                capabilities,
                "supported_phase_selections",
                getattr(self.service, "supported_phase_selections", ("P1",)),
            ),
            ("P1",),
        )
        return normalized

    def phase_selection_requires_pause(self) -> bool:
        capabilities = self._phase_switch_capabilities()
        return bool(getattr(capabilities, "requires_charge_pause_for_phase_change", False))

    def set_phase_selection(self, selection: object) -> PhaseSelection:
        supported_phase_selections = self._split_switch_supported_phase_selections()
        default_phase_selection = supported_phase_selections[0]
        normalized_selection = normalize_phase_value(selection, default_phase_selection)
        if normalized_selection not in supported_phase_selections:
            raise ValueError(
                f"Unsupported phase selection '{selection}' for configured backend "
                f"(supported: {','.join(supported_phase_selections)})"
            )

        switch_backend = self._phase_selection_switch_backend()
        if switch_backend is not None:
            switch_backend.set_phase_selection(normalized_selection)

        charger_backend = self._phase_selection_charger_backend()
        if charger_backend is not None and self._charger_supports_phase_selection(normalized_selection):
            charger_backend.set_phase_selection(normalized_selection)

        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=normalized_selection,
            active=normalized_selection,
        )
        return normalized_selection
