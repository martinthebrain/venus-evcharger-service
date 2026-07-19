# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-state restore helpers for the state controller."""

from __future__ import annotations

from venus_evcharger.backend.models import PhaseSelection
from venus_evcharger.core.contracts import non_negative_float_or_none, non_negative_int
from venus_evcharger.controllers.state_contracts import ModeNormalizer, StateAttributes, is_object_dict
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_restore_victron_ess import VictronEssRuntimeRestorer


class RuntimeStateRestorer:
    """Apply one validated runtime-state payload to the live service."""

    def __init__(
        self,
        service: object,
        normalize_mode: ModeNormalizer,
        normalizer: RuntimeStateNormalizer,
        victron_ess: VictronEssRuntimeRestorer,
    ) -> None:
        self.service = service
        self.normalize_mode = normalize_mode
        self.normalizer = normalizer
        self.victron_ess = victron_ess

    def _restore_basic_runtime_state(self, svc: object, state: dict[str, object]) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "virtual_mode",
            self.normalize_mode(state.get("mode", attributes.get("virtual_mode"))),
        )
        attributes.set(
            "virtual_autostart",
            self.normalizer.coerce_runtime_int(
                state.get("autostart"),
                self.normalizer.coerce_runtime_int(attributes.get("virtual_autostart")),
            ),
        )
        attributes.set(
            "virtual_enable",
            self.normalizer.coerce_runtime_int(
                state.get("enable"),
                self.normalizer.coerce_runtime_int(attributes.get("virtual_enable")),
            ),
        )
        attributes.set(
            "virtual_startstop",
            self.normalizer.coerce_runtime_int(
                state.get("startstop"),
                self.normalizer.coerce_runtime_int(attributes.get("virtual_startstop")),
            ),
        )
        attributes.set(
            "manual_override_until",
            self.normalizer.coerce_runtime_float(
                state.get("manual_override_until"),
                self.normalizer.coerce_runtime_float(attributes.get("manual_override_until")),
            ),
        )
        attributes.set(
            "_auto_mode_cutover_pending",
            bool(
                self.normalizer.coerce_runtime_int(
                    state.get("auto_mode_cutover_pending"),
                    int(bool(attributes.get("_auto_mode_cutover_pending"))),
                )
            ),
        )
        attributes.set("_ignore_min_offtime_once", False)

    def _restore_learned_charge_power_state(
        self,
        svc: object,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "learned_charge_power_watts",
            non_negative_float_or_none(
                state.get(
                    "learned_charge_power_watts",
                    attributes.get("learned_charge_power_watts", None),
                )
            ),
        )
        attributes.set(
            "learned_charge_power_updated_at",
            self.normalizer.optional_past_time(
                state.get(
                    "learned_charge_power_updated_at",
                    attributes.get("learned_charge_power_updated_at", None),
                ),
                current_time,
            ),
        )
        attributes.set(
            "learned_charge_power_state",
            self.normalizer.learned_charge_power_state(
                state.get(
                    "learned_charge_power_state",
                    attributes.get("learned_charge_power_state", None),
                )
            ),
        )
        attributes.set(
            "learned_charge_power_learning_since",
            self.normalizer.optional_past_time(
                state.get(
                    "learned_charge_power_learning_since",
                    attributes.get("learned_charge_power_learning_since", None),
                ),
                current_time,
            ),
        )
        attributes.set(
            "learned_charge_power_sample_count",
            non_negative_int(
                state.get(
                    "learned_charge_power_sample_count",
                    attributes.get("learned_charge_power_sample_count", None),
                )
            ),
        )
        attributes.set(
            "learned_charge_power_phase",
            self.normalizer.learned_charge_power_phase(
                state.get(
                    "learned_charge_power_phase",
                    attributes.get("learned_charge_power_phase", None),
                )
            ),
        )
        attributes.set(
            "learned_charge_power_voltage",
            non_negative_float_or_none(
                state.get(
                    "learned_charge_power_voltage",
                    attributes.get("learned_charge_power_voltage", None),
                )
            ),
        )
        attributes.set(
            "learned_charge_power_signature_mismatch_sessions",
            non_negative_int(
                state.get(
                    "learned_charge_power_signature_mismatch_sessions",
                    attributes.get("learned_charge_power_signature_mismatch_sessions", None),
                )
            ),
        )
        attributes.set(
            "learned_charge_power_signature_checked_session_started_at",
            self.normalizer.optional_past_time(
                state.get(
                    "learned_charge_power_signature_checked_session_started_at",
                    attributes.get(
                        "learned_charge_power_signature_checked_session_started_at",
                        None,
                    ),
                ),
                current_time,
            ),
        )

    def _restore_phase_switch_runtime_state(
        self,
        svc: object,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        attributes = StateAttributes(svc)
        supported_phase_selections = self.normalizer.supported_phase_selections(
            state.get(
                "supported_phase_selections",
                attributes.get("supported_phase_selections", None),
            )
        )
        attributes.set("supported_phase_selections", supported_phase_selections)
        default_phase_selection = supported_phase_selections[0]
        requested_phase_selection = self.normalizer.phase_selection(
            state.get(
                "requested_phase_selection",
                attributes.get("requested_phase_selection", None),
            ),
            default_phase_selection,
        )
        attributes.set("requested_phase_selection", requested_phase_selection)
        attributes.set(
            "active_phase_selection",
            self.normalizer.phase_selection(
                state.get(
                    "active_phase_selection",
                    attributes.get("active_phase_selection", None),
                ),
                requested_phase_selection,
            ),
        )
        pending_selection = self.normalizer.optional_phase_selection(
            state.get(
                "phase_switch_pending_selection",
                attributes.get("_phase_switch_pending_selection", None),
            ),
            requested_phase_selection,
        )
        phase_switch_state = self.normalizer.phase_switch_state(
            state.get(
                "phase_switch_state",
                attributes.get("_phase_switch_state", None),
            )
        )
        attributes.set("_phase_switch_pending_selection", pending_selection)
        attributes.set("_phase_switch_state", phase_switch_state)
        attributes.set(
            "_phase_switch_requested_at",
            self.normalizer.optional_past_time(
                state.get(
                    "phase_switch_requested_at",
                    attributes.get("_phase_switch_requested_at", None),
                ),
                current_time,
            ),
        )
        attributes.set(
            "_phase_switch_stable_until",
            self.normalizer.optional_float(
                state.get(
                    "phase_switch_stable_until",
                    attributes.get("_phase_switch_stable_until", None),
                )
            ),
        )
        previous_resume_relay = bool(attributes.get("_phase_switch_resume_relay", None))
        raw_resume_relay = state.get("phase_switch_resume_relay")
        attributes.set(
            "_phase_switch_resume_relay",
            bool(self.normalizer.coerce_runtime_int(raw_resume_relay, int(previous_resume_relay))),
        )
        attributes.set(
            "_phase_switch_mismatch_counts",
            self._normalized_phase_switch_mismatch_counts(
                state.get(
                    "phase_switch_mismatch_counts",
                    attributes.get("_phase_switch_mismatch_counts", None),
                ),
                requested_phase_selection,
            ),
        )
        attributes.set(
            "_phase_switch_last_mismatch_selection",
            self.normalizer.optional_phase_selection(
                state.get(
                    "phase_switch_last_mismatch_selection",
                    attributes.get("_phase_switch_last_mismatch_selection", None),
                ),
                requested_phase_selection,
            ),
        )
        attributes.set(
            "_phase_switch_last_mismatch_at",
            self.normalizer.optional_past_time(
                state.get(
                    "phase_switch_last_mismatch_at",
                    attributes.get("_phase_switch_last_mismatch_at", None),
                ),
                current_time,
            ),
        )
        attributes.set(
            "_phase_switch_lockout_selection",
            self.normalizer.optional_phase_selection(
                state.get(
                    "phase_switch_lockout_selection",
                    attributes.get("_phase_switch_lockout_selection", None),
                ),
                requested_phase_selection,
            ),
        )
        attributes.set(
            "_phase_switch_lockout_reason",
            str(
                state.get(
                    "phase_switch_lockout_reason",
                    attributes.get("_phase_switch_lockout_reason", None),
                )
                or ""
            ),
        )
        attributes.set(
            "_phase_switch_lockout_at",
            self.normalizer.optional_past_time(
                state.get(
                    "phase_switch_lockout_at",
                    attributes.get("_phase_switch_lockout_at", None),
                ),
                current_time,
            ),
        )
        attributes.set(
            "_phase_switch_lockout_until",
            self.normalizer.optional_float(
                state.get(
                    "phase_switch_lockout_until",
                    attributes.get("_phase_switch_lockout_until", None),
                )
            ),
        )
        if phase_switch_state is None or pending_selection is None:
            for attr_name in (
                "_phase_switch_pending_selection",
                "_phase_switch_state",
                "_phase_switch_requested_at",
                "_phase_switch_stable_until",
            ):
                attributes.set(attr_name, None)
            attributes.set("_phase_switch_resume_relay", False)

    def _normalized_phase_switch_mismatch_counts(
        self,
        raw_counts: object,
        default_selection: PhaseSelection,
    ) -> dict[str, int]:
        normalized_counts: dict[str, int] = {}
        if not is_object_dict(raw_counts):
            return normalized_counts
        for raw_selection, raw_count in raw_counts.items():
            normalized_selection = self.normalizer.phase_selection(raw_selection, default_selection)
            normalized_counts[normalized_selection] = non_negative_int(raw_count)
        return normalized_counts

    def _restore_relay_runtime_state(
        self,
        svc: object,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "relay_last_changed_at",
            self.normalizer.optional_past_time(
                state.get("relay_last_changed_at", attributes.get("relay_last_changed_at")),
                current_time,
            ),
        )
        attributes.set(
            "relay_last_off_at",
            self.normalizer.optional_past_time(
                state.get("relay_last_off_at", attributes.get("relay_last_off_at")),
                current_time,
            ),
        )

    def _restore_contactor_runtime_state(
        self,
        svc: object,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "_contactor_fault_counts",
            self._normalized_contactor_fault_counts(
                state.get(
                    "contactor_fault_counts",
                    attributes.get("_contactor_fault_counts", None),
                )
            ),
        )
        attributes.set(
            "_contactor_fault_active_reason",
            self._normalized_contactor_fault_reason(
                state.get(
                    "contactor_fault_active_reason",
                    attributes.get("_contactor_fault_active_reason", None),
                )
            ),
        )
        attributes.set(
            "_contactor_fault_active_since",
            self.normalizer.optional_past_time(
                state.get(
                    "contactor_fault_active_since",
                    attributes.get("_contactor_fault_active_since", None),
                ),
                current_time,
            ),
        )
        attributes.set(
            "_contactor_lockout_reason",
            self._normalized_contactor_fault_reason(
                state.get(
                    "contactor_lockout_reason",
                    attributes.get("_contactor_lockout_reason", None),
                )
            )
            or "",
        )
        attributes.set(
            "_contactor_lockout_source",
            str(
                state.get(
                    "contactor_lockout_source",
                    attributes.get("_contactor_lockout_source", None),
                )
                or ""
            ),
        )
        attributes.set(
            "_contactor_lockout_at",
            self.normalizer.optional_past_time(
                state.get(
                    "contactor_lockout_at",
                    attributes.get("_contactor_lockout_at", None),
                ),
                current_time,
            ),
        )

    @staticmethod
    def _normalized_contactor_fault_counts(raw_counts: object) -> dict[str, int]:
        allowed_reasons = {"contactor-suspected-open", "contactor-suspected-welded"}
        normalized_counts: dict[str, int] = {}
        if not is_object_dict(raw_counts):
            return normalized_counts
        for raw_reason, raw_count in raw_counts.items():
            reason = str(raw_reason).strip()
            if reason in allowed_reasons:
                normalized_counts[reason] = non_negative_int(raw_count)
        return normalized_counts

    @staticmethod
    def _normalized_contactor_fault_reason(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        reason = value.strip()
        if reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
            return None
        return reason

    def restore(self, state: dict[str, object], current_time: float) -> None:
        svc = self.service
        self._restore_basic_runtime_state(svc, state)
        self._restore_learned_charge_power_state(svc, state, current_time)
        self._restore_phase_switch_runtime_state(svc, state, current_time)
        self._restore_contactor_runtime_state(svc, state, current_time)
        self._restore_relay_runtime_state(svc, state, current_time)
        self.victron_ess.restore_runtime_state(svc, state)
