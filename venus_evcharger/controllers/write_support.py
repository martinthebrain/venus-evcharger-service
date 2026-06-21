# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers shared by the DBus write controller."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from venus_evcharger.backend.models import effective_supported_phase_selections
from venus_evcharger.controllers.write_snapshot import capture_write_state, restore_write_state


class _DbusWriteSupportMixin:
    SNAPSHOT_ATTRS: ClassVar[tuple[str, ...]]
    SNAPSHOT_DBUS_PATHS: ClassVar[tuple[str, ...]]
    SNAPSHOT_DEQUE_ATTRS: ClassVar[tuple[str, ...]]
    SNAPSHOT_MAPPING_ATTRS: ClassVar[tuple[str, ...]]
    SNAPSHOT_VALUE_ATTRS: ClassVar[tuple[str, ...]]
    port: Any

    @classmethod
    def _snapshot_write_state(cls, svc: Any) -> dict[str, Any]:
        return capture_write_state(
            svc,
            attrs=cls.SNAPSHOT_ATTRS,
            deque_attrs=cls.SNAPSHOT_DEQUE_ATTRS,
            value_attrs=cls.SNAPSHOT_VALUE_ATTRS,
            mapping_attrs=cls.SNAPSHOT_MAPPING_ATTRS,
            dbus_paths=cls.SNAPSHOT_DBUS_PATHS,
        )

    @staticmethod
    def _restore_write_state(svc: Any, snapshot: dict[str, Any]) -> None:
        restore_write_state(svc, snapshot)

    def _queue_relay_command(self, svc: Any, relay_on: bool, current_time: float) -> None:
        svc.queue_relay_command(relay_on, current_time)
        self._external_side_effect_started = True

    def _mark_external_side_effect_started(self) -> None:
        self._external_side_effect_started = True

    @staticmethod
    def _publish_local_pm_status_best_effort(svc: Any, relay_on: bool, current_time: float) -> None:
        try:
            svc.publish_local_pm_status(relay_on, current_time)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning(
                "Local relay placeholder publish failed after queuing relay=%s: %s",
                int(bool(relay_on)),
                error,
                exc_info=error,
            )

    @staticmethod
    def _log_normalized_mode(requested_mode: int, applied_mode: int) -> None:
        if applied_mode == requested_mode:
            return
        logging.info(
            "Unsupported mode %s requested on /Mode, normalizing to %s",
            requested_mode,
            applied_mode,
        )

    @staticmethod
    def _reset_auto_decision_state(svc: Any) -> None:
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc.clear_auto_samples()

    @staticmethod
    def _activate_auto_without_cutover(svc: Any) -> None:
        svc.auto_mode_cutover_pending = False
        svc.ignore_min_offtime_once = False

    def _queue_auto_cutover(self, svc: Any, current_time: float) -> None:
        self._queue_relay_command(svc, False, current_time)
        svc.virtual_enable = 1
        svc.virtual_startstop = 0
        svc.auto_mode_cutover_pending = True
        svc.ignore_min_offtime_once = False
        self._publish_local_pm_status_best_effort(svc, False, current_time)

    def _handle_mode_transition_to_auto(self, previous_mode: int, current_time: float) -> None:
        port = self.port
        if port.mode_uses_auto_logic(previous_mode):
            return
        if port.relay_may_be_on_for_cutover():
            self._queue_auto_cutover(port, current_time)
            port.manual_override_until = 0.0
            return
        self._activate_auto_without_cutover(port)
        port.manual_override_until = 0.0

    @staticmethod
    def _snapshot_for_mode(svc: Any, current_time: float, auto_mode_active: bool) -> None:
        snapshot = svc.get_worker_snapshot()
        svc.update_worker_snapshot(
            captured_at=current_time,
            auto_mode_active=auto_mode_active,
            pv_power=None if not auto_mode_active else snapshot.get("pv_power"),
            battery_soc=None if not auto_mode_active else snapshot.get("battery_soc"),
            grid_power=None if not auto_mode_active else snapshot.get("grid_power"),
        )

    @staticmethod
    def _auto_startstop_value(svc: Any) -> int:
        return int(svc.virtual_enable or svc.virtual_startstop)

    @classmethod
    def _startstop_value_for_mode(cls, svc: Any, auto_mode_active: bool) -> int:
        return cls._auto_startstop_value(svc) if auto_mode_active else int(svc.virtual_startstop)

    @classmethod
    def _publish_startstop_enable(
        cls,
        svc: Any,
        current_time: float,
        auto_mode_active: bool | None = None,
    ) -> None:
        resolved_auto_mode_active = svc.mode_uses_auto_logic(svc.virtual_mode) if auto_mode_active is None else auto_mode_active
        svc.publish_dbus_path(
            "/StartStop",
            cls._startstop_value_for_mode(svc, resolved_auto_mode_active),
            current_time,
            force=True,
        )
        svc.publish_dbus_path("/Enable", int(svc.virtual_enable), current_time, force=True)

    @staticmethod
    def _publish_mode_paths(svc: Any, current_time: float, auto_mode_active: bool) -> None:
        svc.publish_dbus_path("/Mode", svc.virtual_mode, current_time, force=True)
        _DbusWriteSupportMixin._publish_startstop_enable(svc, current_time, auto_mode_active)

    @staticmethod
    def _supported_phase_selection_text(svc: Any, current_time: float) -> str:
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=current_time,
        )
        return ",".join(effective_supported)

    @staticmethod
    def _queue_phase_switch_state(
        svc: Any,
        requested_selection: str,
        current_time: float,
        *,
        resume_relay: bool,
    ) -> None:
        svc.requested_phase_selection = requested_selection
        svc._phase_switch_pending_selection = requested_selection
        svc._phase_switch_state = "waiting-relay-off"
        svc._phase_switch_requested_at = current_time
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = bool(resume_relay)

    @classmethod
    def _publish_phase_selection_paths(cls, svc: Any, current_time: float) -> None:
        svc.publish_dbus_path("/PhaseSelection", svc.requested_phase_selection, current_time, force=True)
        svc.publish_dbus_path("/PhaseSelectionActive", svc.active_phase_selection, current_time, force=True)
        svc.publish_dbus_path(
            "/SupportedPhaseSelections",
            cls._supported_phase_selection_text(svc, current_time),
            current_time,
            force=True,
        )

    @staticmethod
    def _clear_phase_lockout_state(svc: Any) -> None:
        svc._phase_switch_mismatch_active = False
        svc._phase_switch_mismatch_counts = {}
        svc._phase_switch_last_mismatch_selection = None
        svc._phase_switch_last_mismatch_at = None
        svc._phase_switch_lockout_selection = None
        svc._phase_switch_lockout_reason = ""
        svc._phase_switch_lockout_at = None
        svc._phase_switch_lockout_until = None

    @classmethod
    def _publish_phase_lockout_paths(cls, svc: Any, current_time: float) -> None:
        configured_supported = ",".join(tuple(getattr(svc, "supported_phase_selections", ("P1",))))
        effective_supported = cls._supported_phase_selection_text(svc, current_time)
        svc.publish_dbus_path("/Auto/PhaseLockoutActive", 0, current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseLockoutTarget", "", current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseLockoutReason", "", current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseSupportedConfigured", configured_supported, current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseSupportedEffective", effective_supported, current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseDegradedActive", int(configured_supported != effective_supported), current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseLockoutAge", -1, current_time, force=True)
        svc.publish_dbus_path("/Auto/PhaseLockoutReset", 0, current_time, force=True)

    @staticmethod
    def _clear_contactor_lockout_state(svc: Any) -> None:
        svc._contactor_fault_counts = {}
        svc._contactor_fault_active_reason = None
        svc._contactor_fault_active_since = None
        svc._contactor_lockout_reason = ""
        svc._contactor_lockout_source = ""
        svc._contactor_lockout_at = None
        svc._contactor_suspected_open_since = None
        svc._contactor_suspected_welded_since = None

    @staticmethod
    def _publish_contactor_lockout_paths(svc: Any, current_time: float) -> None:
        svc.publish_dbus_path("/Auto/ContactorFaultCount", 0, current_time, force=True)
        svc.publish_dbus_path("/Auto/ContactorLockoutActive", 0, current_time, force=True)
        svc.publish_dbus_path("/Auto/ContactorLockoutReason", "", current_time, force=True)
        svc.publish_dbus_path("/Auto/ContactorLockoutSource", "", current_time, force=True)
        svc.publish_dbus_path("/Auto/ContactorLockoutAge", -1, current_time, force=True)
        svc.publish_dbus_path("/Auto/ContactorLockoutReset", 0, current_time, force=True)

    def _apply_auto_disable(self, svc: Any, current_time: float) -> None:
        self._queue_relay_command(svc, False, current_time)
        svc.virtual_enable = 0
        svc.virtual_startstop = 0
        self._publish_local_pm_status_best_effort(svc, False, current_time)

    def _apply_manual_startstop_request(self, svc: Any, wanted_on: bool, current_time: float) -> None:
        self._apply_manual_enable_like_request(svc, wanted_on, current_time)

    def _apply_manual_enable_like_request(self, svc: Any, wanted_on: bool, current_time: float) -> None:
        if svc.charger_enable_available():
            svc.charger_set_enabled(wanted_on)
            self._mark_external_side_effect_started()
            svc.virtual_startstop = 1 if wanted_on else 0
            svc.virtual_enable = svc.virtual_startstop
            svc.manual_override_until = current_time + svc.auto_manual_override_seconds
            return
        self._queue_relay_command(svc, wanted_on, current_time)
        svc.virtual_startstop = 1 if wanted_on else 0
        svc.virtual_enable = svc.virtual_startstop
        svc.manual_override_until = current_time + svc.auto_manual_override_seconds
        self._publish_local_pm_status_best_effort(svc, wanted_on, current_time)

    def _apply_manual_enable_request(self, svc: Any, wanted_on: bool, current_time: float) -> None:
        self._apply_manual_enable_like_request(svc, wanted_on, current_time)
