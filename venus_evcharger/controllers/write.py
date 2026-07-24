# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic control-command handling for the Venus EV charger service.

External adapters translate their transport-specific routes into stable
targets before commands arrive here. The controller translates those user
actions into wallbox-specific state changes and hardware commands.

This module is where "operator intent" enters the service. Because writes can
trigger real-world side effects, the code has to distinguish between two
phases:

- reversible in-memory state updates
- irreversible external actions such as queued relay or charger commands

That is why write snapshots and rollback helpers are so prominent here.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from venus_evcharger.auto.policy_settings import AUTO_POLICY_SETTING_BY_TARGET
from venus_evcharger.control import ControlApiV1Service, ControlCommand, ControlResult
from venus_evcharger.control.models import ControlCommandName, ControlCommandSource
from venus_evcharger.core.contracts import write_failure_is_reversible
from venus_evcharger.controllers.errors import CONTROL_COMMAND_ERRORS
from venus_evcharger.controllers.write_support import _ControlWriteSupport
from venus_evcharger.controllers.write_snapshot import (
    SNAPSHOT_ATTRS,
    SNAPSHOT_DEQUE_ATTRS,
    SNAPSHOT_MAPPING_ATTRS,
    SNAPSHOT_VALUE_ATTRS,
)


class ControlWriteController(_ControlWriteSupport):
    """Apply validated semantic control commands to the wallbox service.

    A write handler in this project is more than a simple setter. It may need
    to:

    - normalize GUI input into supported values
    - update several related runtime attributes together
    - publish derived semantic fields immediately
    - queue hardware actions
    - preserve a rollback snapshot until the write is known to be safe

    Keeping that orchestration inside one controller makes the behavior easier
    to test and easier to extend when new writable paths are added.
    """

    SNAPSHOT_ATTRS = SNAPSHOT_ATTRS
    SNAPSHOT_DEQUE_ATTRS = SNAPSHOT_DEQUE_ATTRS
    SNAPSHOT_VALUE_ATTRS = SNAPSHOT_VALUE_ATTRS
    SNAPSHOT_MAPPING_ATTRS = SNAPSHOT_MAPPING_ATTRS
    CURRENT_SETTING_TARGETS = frozenset({"set_current", "max_current", "min_current"})
    CURRENT_SETTING_FIELDS = {
        "set_current": "set_current",
        "max_current": "max_current",
        "min_current": "min_current",
    }
    AUTO_RUNTIME_SETTING_SPECS: dict[str, tuple[str, Callable[[Any], Any]]] = {
        "auto_start_delay_seconds": ("auto_start_delay_seconds", float),
        "auto_stop_delay_seconds": ("auto_stop_delay_seconds", float),
        "auto_scheduled_enabled_days": ("auto_scheduled_enabled_days", str),
        "auto_scheduled_fallback_delay_seconds": ("auto_scheduled_night_start_delay_seconds", float),
        "auto_scheduled_latest_end_time": ("auto_scheduled_latest_end_time", str),
        "auto_scheduled_night_current": ("auto_scheduled_night_current_amps", float),
        "auto_dbus_backoff_base_seconds": ("auto_dbus_backoff_base_seconds", float),
        "auto_dbus_backoff_max_seconds": ("auto_dbus_backoff_max_seconds", float),
    }
    AUTO_RUNTIME_SETTING_TARGETS = set(AUTO_RUNTIME_SETTING_SPECS) | set(AUTO_POLICY_SETTING_BY_TARGET)
    AUTO_RUNTIME_SETTING_FIELDS = {
        target: target
        for target in AUTO_RUNTIME_SETTING_TARGETS
    }

    def __init__(self, port: Any) -> None:
        self.port = port
        self._control_api = ControlApiV1Service(
            current_setting_targets=self.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=self.AUTO_RUNTIME_SETTING_TARGETS,
        )
        self._command_lock = threading.RLock()
        self._external_side_effect_started = False

    @staticmethod
    def _write_failure_detail(error: Exception) -> str:
        """Return a compact error detail for Control API results."""
        return str(error) or error.__class__.__name__

    def build_control_command(
        self,
        name: ControlCommandName,
        target: str,
        value: Any,
        *,
        source: ControlCommandSource = "internal",
    ) -> ControlCommand:
        """Build one canonical control command from a semantic target."""
        return self._control_api.command_for_target(name, target, value, source=source)

    def build_control_command_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        """Build one canonical control command from one structured API payload."""
        return self._control_api.command_from_payload(payload, source=source)

    def _handle_mode_write(self, requested_mode: int) -> None:
        port = self.port
        previous_mode = int(port.virtual_mode)
        current_time = port.time_now()
        normalized_mode = port.normalize_mode(requested_mode)
        auto_mode_active = port.mode_uses_auto_logic(normalized_mode)
        self._log_normalized_mode(requested_mode, normalized_mode)
        if auto_mode_active:
            self._handle_mode_transition_to_auto(previous_mode, current_time)
        else:
            self._handle_mode_transition_to_manual(previous_mode, current_time)
        port.virtual_mode = normalized_mode
        self._reset_auto_decision_state(port)
        self._snapshot_for_mode(port, current_time, auto_mode_active)
        self._publish_mode_paths(port, current_time, auto_mode_active)
        logging.info(
            "Control target mode requested=%s previous=%s applied=%s %s",
            requested_mode,
            previous_mode,
            port.virtual_mode,
            port.state_summary(),
        )

    def _handle_autostart_write(self, value: Any) -> None:
        port = self.port
        port.virtual_autostart = int(value)
        port.publish_field("auto_start", port.virtual_autostart, port.time_now(), force=True)
        logging.info(
            "Control target auto_start=%s %s",
            port.virtual_autostart,
            port.state_summary(),
        )

    def _handle_startstop_write(self, wanted_on: bool) -> None:
        port = self.port
        current_time = port.time_now()
        if port.mode_uses_auto_logic(port.virtual_mode):
            # In Auto, StartStop acts like an allow/deny request for the auto
            # controller. It must not bypass SOC/night/surplus checks by
            # forcing the relay on directly.
            if not wanted_on:
                self._apply_auto_disable(port, current_time)
            else:
                port.virtual_enable = 1
            self._publish_startstop_enable(port, current_time, auto_mode_active=True)
        else:
            # In Manual, StartStop remains direct relay control.
            self._apply_manual_startstop_request(port, wanted_on, current_time)
            self._publish_startstop_enable(port, current_time, auto_mode_active=False)
        logging.info(
            "Control target start_stop=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_enable_write(self, wanted_on: bool) -> None:
        port = self.port
        current_time = port.time_now()
        if port.mode_uses_auto_logic(port.virtual_mode):
            if not wanted_on:
                self._apply_auto_disable(port, current_time)
            else:
                port.virtual_enable = 1
        else:
            self._apply_manual_enable_request(port, wanted_on, current_time)
        self._publish_startstop_enable(
            port,
            current_time,
            auto_mode_active=port.mode_uses_auto_logic(port.virtual_mode),
        )
        logging.info(
            "Control target enable=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_current_setting_write(self, target: str, value: Any) -> None:
        port = self.port
        current_time = port.time_now()
        if target == "set_current":
            requested_current = float(value)
            if port.charger_current_available():
                port.charger_set_current(requested_current)
                self._mark_external_side_effect_started()
            port.virtual_set_current = requested_current
            target_value = port.virtual_set_current
        elif target == "max_current":
            port.max_current = float(value)
            target_value = port.max_current
        else:
            port.min_current = float(value)
            target_value = port.min_current
        port.publish_field(self.CURRENT_SETTING_FIELDS[target], target_value, current_time, force=True)

    @classmethod
    def _apply_auto_runtime_setting(cls, port: Any, target: str, value: Any) -> Any:
        """Apply one Auto runtime setting using its declarative normalization spec."""
        policy_setting = AUTO_POLICY_SETTING_BY_TARGET.get(target)
        if policy_setting is not None:
            target_value = policy_setting.update(port.auto_policy, value)
            port.validate_runtime_config()
            return target_value
        attr_name, normalizer = cls.AUTO_RUNTIME_SETTING_SPECS[target]
        setattr(port, attr_name, normalizer(value))
        port.validate_runtime_config()
        return getattr(port, attr_name)

    def _handle_auto_runtime_setting_write(self, target: str, value: Any) -> None:
        """Apply one writable Auto tuning value that may persist in runtime overrides."""
        port = self.port
        current_time = port.time_now()
        target_value = self._apply_auto_runtime_setting(port, target, value)
        port.publish_field(self.AUTO_RUNTIME_SETTING_FIELDS[target], target_value, current_time, force=True)

    def _handle_phase_selection_write(self, value: Any) -> None:
        """Apply one phase selection when the current backend can do so safely."""
        port = self.port
        current_time = port.time_now()
        requested_selection = port.normalize_phase_selection(value)
        if requested_selection not in port.supported_phase_selections:
            raise ValueError(
                f"Unsupported phase selection '{value}' "
                f"(supported: {','.join(port.supported_phase_selections)})"
            )
        if port.phase_selection_requires_pause() and port.relay_may_be_on_for_cutover():
            self._queue_phase_switch_state(
                port._service,
                requested_selection,
                current_time,
                resume_relay=True,
            )
            self._queue_relay_command(port, False, current_time)
            self._publish_local_pm_status_best_effort(port, False, current_time)
            self._publish_phase_selection_paths(port, current_time)
            logging.info(
                "Control target phase_selection requested=%s staged=%s %s",
                value,
                requested_selection,
                port.state_summary(),
            )
            return
        applied_selection = port.apply_phase_selection(requested_selection)
        port.requested_phase_selection = applied_selection
        port.active_phase_selection = applied_selection
        self._publish_phase_selection_paths(port, current_time)
        logging.info(
            "Control target phase_selection requested=%s applied=%s %s",
            value,
            applied_selection,
            port.state_summary(),
        )

    def _handle_phase_lockout_reset_write(self, value: Any) -> None:
        """Clear phase lockout and mismatch tracking on explicit operator request."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_field("auto_phase_lockout_reset", 0, current_time, force=True)
            return
        self._clear_phase_lockout_state(port._service)
        self._publish_phase_selection_paths(port, current_time)
        self._publish_phase_lockout_paths(port, current_time)
        logging.info("Control target auto_phase_lockout_reset=1 cleared phase lockout state %s", port.state_summary())

    def _handle_contactor_lockout_reset_write(self, value: Any) -> None:
        """Clear latched contactor-fault state on explicit operator request."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_field("auto_contactor_lockout_reset", 0, current_time, force=True)
            return
        self._clear_contactor_lockout_state(port._service)
        self._publish_contactor_lockout_paths(port, current_time)
        logging.info(
            "Control target auto_contactor_lockout_reset=1 cleared contactor lockout state %s",
            port.state_summary(),
        )

    def _handle_software_update_run_write(self, value: Any) -> None:
        """Queue one software-update run request for the periodic runtime loop."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_field("auto_software_update_run", 0, current_time, force=True)
            return
        port._software_update_run_requested_at = current_time
        port.publish_field("auto_software_update_run", 0, current_time, force=True)
        logging.info("Control target auto_software_update_run=1 queued a software update request %s", port.state_summary())

    def _handle_mode_value_write(self, value: Any) -> None:
        """Normalize and dispatch one mode target value."""
        self._handle_mode_write(int(value))

    def _handle_startstop_value_write(self, value: Any) -> None:
        """Normalize and dispatch one start-stop target value."""
        self._handle_startstop_write(bool(int(value)))

    def _handle_enable_value_write(self, value: Any) -> None:
        """Normalize and dispatch one enable target value."""
        self._handle_enable_write(bool(int(value)))

    def handle_control_command(self, command: ControlCommand) -> ControlResult:
        """Handle one canonical Control API command using existing write semantics."""
        with self._command_lock:
            return self._handle_control_command_locked(command)

    def _handle_control_command_locked(self, command: ControlCommand) -> ControlResult:
        """Apply one command while excluding concurrent state transactions."""
        port = self.port
        snapshot = self._snapshot_write_state(port._service)
        self._external_side_effect_started = False
        persistence_completed = False
        try:
            port.begin_publication_transaction()
            self._control_api.execute(self, command)
            port.save_runtime_state()
            persistence_completed = True
            port.save_runtime_overrides()
            port.commit_publication_transaction()
            return ControlResult.applied_result(
                command,
                external_side_effect_started=self._external_side_effect_started,
            )
        except CONTROL_COMMAND_ERRORS as error:
            detail = self._write_failure_detail(error)
            irreversible = self._external_side_effect_started or persistence_completed
            if write_failure_is_reversible(irreversible):
                port.discard_publication_transaction()
                self._restore_write_state(port._service, snapshot)
                logging.warning(
                    "Control command %s target=%s value=%s failed: %s",
                    command.name,
                    command.target,
                    command.value,
                    error,
                    exc_info=error,
                )
                return ControlResult.rejected_result(command, detail=detail)
            self._commit_in_flight_publications(port)
            logging.warning(
                "Control command %s target=%s value=%s failed after external side effects started; "
                "keeping in-flight state: %s",
                command.name,
                command.target,
                command.value,
                error,
                exc_info=error,
            )
            return ControlResult.accepted_in_flight_result(
                command,
                detail=detail,
                external_side_effect_started=irreversible,
            )
        finally:
            port.discard_publication_transaction()
            self._external_side_effect_started = False

    @staticmethod
    def _commit_in_flight_publications(port: Any) -> None:
        """Best-effort publish state after an irreversible partial command."""
        try:
            port.commit_publication_transaction()
        except CONTROL_COMMAND_ERRORS as publication_error:
            logging.warning(
                "Control state publication failed after an irreversible command: %s",
                publication_error,
                exc_info=publication_error,
            )
