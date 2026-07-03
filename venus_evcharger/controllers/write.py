# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus write-handling helpers for the Venus EV charger service.

Victron GUI writes arrive here through writable DBus paths such as /Mode,
/Enable, /StartStop, and /AutoStart. The controller translates those user
actions into wallbox-specific state changes and Shelly relay commands.

This module is where "operator intent" enters the service. Because writes can
trigger real-world side effects, the code has to distinguish between two
phases:

- reversible in-memory state updates
- irreversible external actions such as queued relay or charger commands

That is why write snapshots and rollback helpers are so prominent here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from venus_evcharger.auto.policy import AutoPolicy, validate_auto_policy
from venus_evcharger.control import ControlApiV1Service, ControlCommand, ControlResult
from venus_evcharger.control.models import ControlCommandSource
from venus_evcharger.core.contracts import write_failure_is_reversible
from venus_evcharger.controllers.errors import CONTROL_COMMAND_ERRORS
from venus_evcharger.controllers.write_support import _DbusWriteSupport
from venus_evcharger.controllers.write_snapshot import (
    SNAPSHOT_ATTRS,
    SNAPSHOT_DBUS_PATHS,
    SNAPSHOT_DEQUE_ATTRS,
    SNAPSHOT_MAPPING_ATTRS,
    SNAPSHOT_VALUE_ATTRS,
)
from venus_evcharger.dbus_gateway import evcs_path_to_field


class DbusWriteController(_DbusWriteSupport):
    """Encapsulate writable DBus path handling for the Venus EV charger service.

    A write handler in this project is more than a simple setter. It may need
    to:

    - normalize GUI input into supported values
    - update several related runtime attributes together
    - publish derived DBus paths immediately
    - queue hardware actions
    - preserve a rollback snapshot until the write is known to be safe

    Keeping that orchestration inside one controller makes the behavior easier
    to test and easier to extend when new writable paths are added.
    """

    SNAPSHOT_DBUS_PATHS = SNAPSHOT_DBUS_PATHS
    SNAPSHOT_ATTRS = SNAPSHOT_ATTRS
    SNAPSHOT_DEQUE_ATTRS = SNAPSHOT_DEQUE_ATTRS
    SNAPSHOT_VALUE_ATTRS = SNAPSHOT_VALUE_ATTRS
    SNAPSHOT_MAPPING_ATTRS = SNAPSHOT_MAPPING_ATTRS
    CURRENT_SETTING_PATHS = ("/SetCurrent", "/MaxCurrent", "/MinCurrent")
    CURRENT_SETTING_FIELDS = {
        "/SetCurrent": "set_current",
        "/MaxCurrent": "max_current",
        "/MinCurrent": "min_current",
    }
    AUTO_RUNTIME_SETTING_SPECS: dict[str, tuple[str, Callable[[Any], Any], str]] = {
        "/Auto/StartSurplusWatts": ("auto_start_surplus_watts", float, "policy"),
        "/Auto/StopSurplusWatts": ("auto_stop_surplus_watts", float, "policy"),
        "/Auto/MinSoc": ("auto_min_soc", float, "policy"),
        "/Auto/ResumeSoc": ("auto_resume_soc", float, "policy"),
        "/Auto/StartDelaySeconds": ("auto_start_delay_seconds", float, "runtime"),
        "/Auto/StopDelaySeconds": ("auto_stop_delay_seconds", float, "runtime"),
        "/Auto/ScheduledEnabledDays": ("auto_scheduled_enabled_days", str, "runtime"),
        "/Auto/ScheduledFallbackDelaySeconds": ("auto_scheduled_night_start_delay_seconds", float, "runtime"),
        "/Auto/ScheduledLatestEndTime": ("auto_scheduled_latest_end_time", str, "runtime"),
        "/Auto/ScheduledNightCurrent": ("auto_scheduled_night_current_amps", float, "runtime"),
        "/Auto/DbusBackoffBaseSeconds": ("auto_dbus_backoff_base_seconds", float, "runtime"),
        "/Auto/DbusBackoffMaxSeconds": ("auto_dbus_backoff_max_seconds", float, "runtime"),
        "/Auto/GridRecoveryStartSeconds": ("auto_grid_recovery_start_seconds", float, "policy"),
        "/Auto/StopSurplusDelaySeconds": ("auto_stop_surplus_delay_seconds", float, "policy"),
        "/Auto/StopSurplusVolatilityLowWatts": ("auto_stop_surplus_volatility_low_watts", float, "policy"),
        "/Auto/StopSurplusVolatilityHighWatts": ("auto_stop_surplus_volatility_high_watts", float, "policy"),
        "/Auto/ReferenceChargePowerWatts": ("auto_reference_charge_power_watts", float, "policy"),
        "/Auto/LearnChargePowerEnabled": ("auto_learn_charge_power_enabled", int, "policy"),
        "/Auto/LearnChargePowerMinWatts": ("auto_learn_charge_power_min_watts", float, "policy"),
        "/Auto/LearnChargePowerAlpha": ("auto_learn_charge_power_alpha", float, "policy"),
        "/Auto/LearnChargePowerStartDelaySeconds": ("auto_learn_charge_power_start_delay_seconds", float, "policy"),
        "/Auto/LearnChargePowerWindowSeconds": ("auto_learn_charge_power_window_seconds", float, "policy"),
        "/Auto/LearnChargePowerMaxAgeSeconds": ("auto_learn_charge_power_max_age_seconds", float, "policy"),
        "/Auto/PhaseSwitching": ("auto_phase_switching_enabled", int, "policy"),
        "/Auto/PhasePreferLowestWhenIdle": ("auto_phase_prefer_lowest_when_idle", int, "policy"),
        "/Auto/PhaseUpshiftDelaySeconds": ("auto_phase_upshift_delay_seconds", float, "policy"),
        "/Auto/PhaseDownshiftDelaySeconds": ("auto_phase_downshift_delay_seconds", float, "policy"),
        "/Auto/PhaseUpshiftHeadroomWatts": ("auto_phase_upshift_headroom_watts", float, "policy"),
        "/Auto/PhaseDownshiftMarginWatts": ("auto_phase_downshift_margin_watts", float, "policy"),
        "/Auto/PhaseMismatchRetrySeconds": ("auto_phase_mismatch_retry_seconds", float, "policy"),
        "/Auto/PhaseMismatchLockoutCount": ("auto_phase_mismatch_lockout_count", int, "policy"),
        "/Auto/PhaseMismatchLockoutSeconds": ("auto_phase_mismatch_lockout_seconds", float, "policy"),
    }
    AUTO_RUNTIME_SETTING_PATHS = set(AUTO_RUNTIME_SETTING_SPECS)
    AUTO_RUNTIME_SETTING_FIELDS = {
        path: evcs_path_to_field(path)
        for path in AUTO_RUNTIME_SETTING_SPECS
    }

    def __init__(self, port: Any) -> None:
        self.port = port
        self._control_api = ControlApiV1Service(
            current_setting_paths=self.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=self.AUTO_RUNTIME_SETTING_PATHS,
        )
        self._external_side_effect_started = False

    @staticmethod
    def _write_failure_detail(error: Exception) -> str:
        """Return a compact error detail for Control API results."""
        return str(error) or error.__class__.__name__

    def build_control_command(
        self,
        path: str,
        value: Any,
        *,
        source: ControlCommandSource = "dbus",
    ) -> ControlCommand:
        """Build one canonical control command from one transport-level write."""
        return self._control_api.command_for_write(path, value, source=source)

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
        port.virtual_mode = normalized_mode
        self._reset_auto_decision_state(port)
        self._snapshot_for_mode(port, current_time, auto_mode_active)
        self._publish_mode_paths(port, current_time, auto_mode_active)
        logging.info(
            "DBus write /Mode requested=%s previous=%s applied=%s %s",
            requested_mode,
            previous_mode,
            port.virtual_mode,
            port.state_summary(),
        )

    def _handle_autostart_write(self, value: Any) -> None:
        port = self.port
        port.virtual_autostart = int(value)
        port.publish_dbus_field("auto_start", port.virtual_autostart, port.time_now(), force=True)
        logging.info(
            "DBus write /AutoStart=%s %s",
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
            "DBus write /StartStop=%s auto_mode=%s %s",
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
            "DBus write /Enable=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_current_setting_write(self, path: str, value: Any) -> None:
        port = self.port
        current_time = port.time_now()
        if path == "/SetCurrent":
            requested_current = float(value)
            if port.charger_current_available():
                port.charger_set_current(requested_current)
                self._mark_external_side_effect_started()
            port.virtual_set_current = requested_current
            target_value = port.virtual_set_current
        elif path == "/MaxCurrent":
            port.max_current = float(value)
            target_value = port.max_current
        else:
            port.min_current = float(value)
            target_value = port.min_current
        port.publish_dbus_field(self.CURRENT_SETTING_FIELDS[path], target_value, current_time, force=True)

    @staticmethod
    def _sync_auto_policy_runtime(port: Any) -> None:
        """Rebuild and validate the structured Auto policy after runtime tuning writes."""
        validate_auto_policy(AutoPolicy.from_service(port._service), port._service)
        port.validate_runtime_config()

    @classmethod
    def _apply_auto_runtime_setting(cls, port: Any, path: str, value: Any) -> Any:
        """Apply one Auto runtime setting using its declarative normalization spec."""
        attr_name, normalizer, validation = cls.AUTO_RUNTIME_SETTING_SPECS[path]
        setattr(port, attr_name, normalizer(value))
        if validation == "policy":
            cls._sync_auto_policy_runtime(port)
        else:
            port.validate_runtime_config()
        return getattr(port, attr_name)

    def _handle_auto_runtime_setting_write(self, path: str, value: Any) -> None:
        """Apply one writable Auto tuning value that may persist in runtime overrides."""
        port = self.port
        current_time = port.time_now()
        target_value = self._apply_auto_runtime_setting(port, path, value)
        port.publish_dbus_field(self.AUTO_RUNTIME_SETTING_FIELDS[path], target_value, current_time, force=True)

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
                "DBus write /PhaseSelection requested=%s staged=%s %s",
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
            "DBus write /PhaseSelection requested=%s applied=%s %s",
            value,
            applied_selection,
            port.state_summary(),
        )

    def _handle_phase_lockout_reset_write(self, value: Any) -> None:
        """Clear phase lockout and mismatch tracking on explicit operator request."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_dbus_field("auto_phase_lockout_reset", 0, current_time, force=True)
            return
        self._clear_phase_lockout_state(port._service)
        self._publish_phase_selection_paths(port, current_time)
        self._publish_phase_lockout_paths(port, current_time)
        logging.info("DBus write /Auto/PhaseLockoutReset=1 cleared phase lockout state %s", port.state_summary())

    def _handle_contactor_lockout_reset_write(self, value: Any) -> None:
        """Clear latched contactor-fault state on explicit operator request."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_dbus_field("auto_contactor_lockout_reset", 0, current_time, force=True)
            return
        self._clear_contactor_lockout_state(port._service)
        self._publish_contactor_lockout_paths(port, current_time)
        logging.info(
            "DBus write /Auto/ContactorLockoutReset=1 cleared contactor lockout state %s",
            port.state_summary(),
        )

    def _handle_software_update_run_write(self, value: Any) -> None:
        """Queue one software-update run request for the periodic runtime loop."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_dbus_field("auto_software_update_run", 0, current_time, force=True)
            return
        port._software_update_run_requested_at = current_time
        port.publish_dbus_field("auto_software_update_run", 0, current_time, force=True)
        logging.info("DBus write /Auto/SoftwareUpdateRun=1 queued a software update request %s", port.state_summary())

    @staticmethod
    def _handle_unknown_write(_path: str, _value: Any) -> None:
        """Keep legacy unknown writes as a no-op compatibility path."""

    def _handle_mode_value_write(self, value: Any) -> None:
        """Normalize and dispatch one /Mode write value."""
        self._handle_mode_write(int(value))

    def _handle_startstop_value_write(self, value: Any) -> None:
        """Normalize and dispatch one /StartStop write value."""
        self._handle_startstop_write(bool(int(value)))

    def _handle_enable_value_write(self, value: Any) -> None:
        """Normalize and dispatch one /Enable write value."""
        self._handle_enable_write(bool(int(value)))

    def handle_control_command(self, command: ControlCommand) -> ControlResult:
        """Handle one canonical Control API command using existing write semantics."""
        port = self.port
        snapshot = self._snapshot_write_state(port._service)
        self._external_side_effect_started = False
        try:
            self._control_api.execute(self, command)
            port.save_runtime_state()
            port.save_runtime_overrides()
            return ControlResult.applied_result(
                command,
                external_side_effect_started=self._external_side_effect_started,
            )
        except CONTROL_COMMAND_ERRORS as error:
            detail = self._write_failure_detail(error)
            if write_failure_is_reversible(self._external_side_effect_started):
                self._restore_write_state(port._service, snapshot)
                logging.warning(
                    "Control command %s path=%s value=%s failed: %s",
                    command.name,
                    command.path,
                    command.value,
                    error,
                    exc_info=error,
                )
                return ControlResult.rejected_result(command, detail=detail)
            logging.warning(
                "Control command %s path=%s value=%s failed after external side effects started; "
                "keeping in-flight state: %s",
                command.name,
                command.path,
                command.value,
                error,
                exc_info=error,
            )
            return ControlResult.accepted_in_flight_result(
                command,
                detail=detail,
                external_side_effect_started=self._external_side_effect_started,
            )
        finally:
            self._external_side_effect_started = False

    def handle_write(self, path: str, value: Any) -> bool:
        """Handle writable DBus path updates from Venus OS."""
        command = self.build_control_command(path, value, source="dbus")
        result = self.handle_control_command(command)
        return result.accepted
