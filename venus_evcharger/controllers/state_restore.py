# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-state restore helpers for the state controller."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from venus_evcharger.backend.models import PhaseSelection
from venus_evcharger.core.contracts import non_negative_float_or_none, non_negative_int
from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.controllers.errors import RUNTIME_PERSISTENCE_WRITE_ERRORS
from .state_restore_support import (
    _StateRuntimeRestoreVictronEss,
    _victron_ess_balance_energy_ids,
    _victron_ess_balance_runtime_string,
)


class _StateRuntimeRestore(_StateRuntimeRestoreVictronEss):
    if TYPE_CHECKING:  # pragma: no cover
        _normalize_mode: Callable[[object], int]

        def state_summary(self) -> str: ...

    @staticmethod
    def _victron_ess_balance_activation_mode(payload: dict[str, object], svc: Any) -> str | None:
        activation_mode = str(
            payload.get(
                "activation_mode",
                getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", "always"),
            )
            or "always"
        ).strip().lower()
        if activation_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return activation_mode
        return None

    def _restore_basic_runtime_state(self, svc: Any, state: dict[str, object]) -> None:
        svc.virtual_mode = self._normalize_mode(state.get("mode", svc.virtual_mode))
        svc.virtual_autostart = self.coerce_runtime_int(state.get("autostart"), svc.virtual_autostart)
        svc.virtual_enable = self.coerce_runtime_int(state.get("enable"), svc.virtual_enable)
        svc.virtual_startstop = self.coerce_runtime_int(state.get("startstop"), svc.virtual_startstop)
        svc.manual_override_until = self.coerce_runtime_float(state.get("manual_override_until"), svc.manual_override_until)
        svc._auto_mode_cutover_pending = bool(
            self.coerce_runtime_int(state.get("auto_mode_cutover_pending"), svc._auto_mode_cutover_pending)
        )
        svc._ignore_min_offtime_once = False

    def _restore_learned_charge_power_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        svc.learned_charge_power_watts = non_negative_float_or_none(
            state.get("learned_charge_power_watts", getattr(svc, "learned_charge_power_watts", None))
        )
        svc.learned_charge_power_updated_at = self._coerce_optional_runtime_past_time(
            state.get("learned_charge_power_updated_at", getattr(svc, "learned_charge_power_updated_at", None)),
            current_time,
        )
        svc.learned_charge_power_state = self._normalize_learned_charge_power_state(
            state.get("learned_charge_power_state", getattr(svc, "learned_charge_power_state", "unknown"))
        )
        svc.learned_charge_power_learning_since = self._coerce_optional_runtime_past_time(
            state.get("learned_charge_power_learning_since", getattr(svc, "learned_charge_power_learning_since", None)),
            current_time,
        )
        svc.learned_charge_power_sample_count = non_negative_int(
            state.get("learned_charge_power_sample_count", getattr(svc, "learned_charge_power_sample_count", 0)),
            0,
        )
        svc.learned_charge_power_phase = self._normalize_learned_charge_power_phase(
            state.get("learned_charge_power_phase", getattr(svc, "learned_charge_power_phase", None))
        )
        svc.learned_charge_power_voltage = non_negative_float_or_none(
            state.get("learned_charge_power_voltage", getattr(svc, "learned_charge_power_voltage", None))
        )
        svc.learned_charge_power_signature_mismatch_sessions = non_negative_int(
            state.get(
                "learned_charge_power_signature_mismatch_sessions",
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0),
            ),
            0,
        )
        svc.learned_charge_power_signature_checked_session_started_at = self._coerce_optional_runtime_past_time(
            state.get(
                "learned_charge_power_signature_checked_session_started_at",
                getattr(svc, "learned_charge_power_signature_checked_session_started_at", None),
            ),
            current_time,
        )

    def _restore_phase_switch_runtime_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        supported_phase_selections = self._normalize_runtime_supported_phase_selections(
            state.get("supported_phase_selections", getattr(svc, "supported_phase_selections", ("P1",)))
        )
        svc.supported_phase_selections = supported_phase_selections
        default_phase_selection = supported_phase_selections[0]
        svc.requested_phase_selection = self._normalize_runtime_phase_selection(
            state.get("requested_phase_selection", getattr(svc, "requested_phase_selection", default_phase_selection)),
            default_phase_selection,
        )
        svc.active_phase_selection = self._normalize_runtime_phase_selection(
            state.get("active_phase_selection", getattr(svc, "active_phase_selection", svc.requested_phase_selection)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_pending_selection = self._normalized_optional_runtime_phase_selection(
            state.get("phase_switch_pending_selection", getattr(svc, "_phase_switch_pending_selection", None)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_state = self._normalize_phase_switch_state(
            state.get("phase_switch_state", getattr(svc, "_phase_switch_state", None))
        )
        svc._phase_switch_requested_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_requested_at", getattr(svc, "_phase_switch_requested_at", None)),
            current_time,
        )
        svc._phase_switch_stable_until = self._coerce_optional_runtime_float(
            state.get("phase_switch_stable_until", getattr(svc, "_phase_switch_stable_until", None))
        )
        svc._phase_switch_resume_relay = bool(
            self.coerce_runtime_int(
                state.get("phase_switch_resume_relay", getattr(svc, "_phase_switch_resume_relay", False)),
                1 if bool(getattr(svc, "_phase_switch_resume_relay", False)) else 0,
            )
        )
        svc._phase_switch_mismatch_counts = self._normalized_phase_switch_mismatch_counts(
            state.get("phase_switch_mismatch_counts", getattr(svc, "_phase_switch_mismatch_counts", {})),
            svc.requested_phase_selection,
        )
        svc._phase_switch_last_mismatch_selection = self._normalized_optional_runtime_phase_selection(
            state.get("phase_switch_last_mismatch_selection", getattr(svc, "_phase_switch_last_mismatch_selection", None)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_last_mismatch_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_last_mismatch_at", getattr(svc, "_phase_switch_last_mismatch_at", None)),
            current_time,
        )
        svc._phase_switch_lockout_selection = self._normalized_optional_runtime_phase_selection(
            state.get("phase_switch_lockout_selection", getattr(svc, "_phase_switch_lockout_selection", None)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_lockout_reason = str(
            state.get("phase_switch_lockout_reason", getattr(svc, "_phase_switch_lockout_reason", "")) or ""
        )
        svc._phase_switch_lockout_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_lockout_at", getattr(svc, "_phase_switch_lockout_at", None)),
            current_time,
        )
        svc._phase_switch_lockout_until = self._coerce_optional_runtime_float(
            state.get("phase_switch_lockout_until", getattr(svc, "_phase_switch_lockout_until", None))
        )
        if svc._phase_switch_state is None or svc._phase_switch_pending_selection is None:
            svc._phase_switch_pending_selection = None
            svc._phase_switch_state = None
            svc._phase_switch_requested_at = None
            svc._phase_switch_stable_until = None
            svc._phase_switch_resume_relay = False

    def _normalized_phase_switch_mismatch_counts(
        self,
        raw_counts: object,
        default_selection: PhaseSelection,
    ) -> dict[str, int]:
        normalized_counts: dict[str, int] = {}
        if not isinstance(raw_counts, dict):
            return normalized_counts
        for raw_selection, raw_count in raw_counts.items():
            normalized_selection = self._normalize_runtime_phase_selection(raw_selection, default_selection)
            normalized_counts[normalized_selection] = non_negative_int(raw_count, 0)
        return normalized_counts

    def _restore_relay_runtime_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        svc.relay_last_changed_at = self._coerce_optional_runtime_past_time(
            state.get("relay_last_changed_at", svc.relay_last_changed_at),
            current_time,
        )
        svc.relay_last_off_at = self._coerce_optional_runtime_past_time(
            state.get("relay_last_off_at", svc.relay_last_off_at),
            current_time,
        )

    def _restore_contactor_runtime_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        svc._contactor_fault_counts = self._normalized_contactor_fault_counts(
            state.get("contactor_fault_counts", getattr(svc, "_contactor_fault_counts", {}))
        )
        svc._contactor_fault_active_reason = self._normalized_contactor_fault_reason(
            state.get("contactor_fault_active_reason", getattr(svc, "_contactor_fault_active_reason", ""))
        )
        svc._contactor_fault_active_since = self._coerce_optional_runtime_past_time(
            state.get("contactor_fault_active_since", getattr(svc, "_contactor_fault_active_since", None)),
            current_time,
        )
        svc._contactor_lockout_reason = (
            self._normalized_contactor_fault_reason(
                state.get("contactor_lockout_reason", getattr(svc, "_contactor_lockout_reason", ""))
            )
            or ""
        )
        svc._contactor_lockout_source = str(
            state.get("contactor_lockout_source", getattr(svc, "_contactor_lockout_source", "")) or ""
        )
        svc._contactor_lockout_at = self._coerce_optional_runtime_past_time(
            state.get("contactor_lockout_at", getattr(svc, "_contactor_lockout_at", None)),
            current_time,
        )

    @staticmethod
    def _normalized_contactor_fault_counts(raw_counts: object) -> dict[str, int]:
        allowed_reasons = {"contactor-suspected-open", "contactor-suspected-welded"}
        normalized_counts: dict[str, int] = {}
        if not isinstance(raw_counts, dict):
            return normalized_counts
        for raw_reason, raw_count in raw_counts.items():
            reason = str(raw_reason).strip()
            if reason in allowed_reasons:
                normalized_counts[reason] = non_negative_int(raw_count, 0)
        return normalized_counts

    @staticmethod
    def _normalized_contactor_fault_reason(value: object) -> str | None:
        reason = str(value or "").strip()
        if reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
            return None
        return reason

    def load_runtime_state(self) -> None:
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return
        state = self._read_runtime_state_payload(path)
        if state is None:
            return
        current_time = self._runtime_load_time(svc)
        self._restore_basic_runtime_state(svc, state)
        self._restore_learned_charge_power_state(svc, state, current_time)
        self._restore_phase_switch_runtime_state(svc, state, current_time)
        self._restore_contactor_runtime_state(svc, state, current_time)
        self._restore_relay_runtime_state(svc, state, current_time)
        self._restore_victron_ess_balance_runtime_state(svc, state)
        svc._runtime_state_serialized = self._serialized_runtime_state()
        logging.info("Restored runtime state from %s: %s", path, self.state_summary())

    def save_runtime_state(self) -> None:
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return
        payload = self._serialized_runtime_state()
        if payload == getattr(svc, "_runtime_state_serialized", None):
            return
        try:
            write_text_atomically(path, payload)
            svc._runtime_state_serialized = payload
            logging.debug("Saved runtime state to %s: %s", path, self.state_summary())
        except RUNTIME_PERSISTENCE_WRITE_ERRORS as error:
            logging.warning("Unable to write runtime state to %s: %s", path, error)
