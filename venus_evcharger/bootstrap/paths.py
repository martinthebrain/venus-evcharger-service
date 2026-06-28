# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Venus EV charger service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop
"""

from __future__ import annotations

import logging
import platform
import time
from datetime import datetime
from collections.abc import Callable
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.bootstrap.errors import BOOTSTRAP_DBUS_REGISTRATION_ERRORS
from venus_evcharger.core.common import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
PathSpec = tuple[Any, Callable[[Any, Any], str] | None]
PathMap = dict[str, PathSpec]


def _scheduled_diagnostic_defaults(snapshot: Any) -> PathMap:
    """Return scheduled-mode diagnostic paths with normalized disabled defaults."""
    if snapshot is None:
        return _disabled_scheduled_diagnostic_defaults()
    return _active_scheduled_diagnostic_defaults(snapshot)


def _disabled_scheduled_diagnostic_defaults() -> PathMap:
    """Return scheduled diagnostics for instances where scheduled mode is inactive."""
    return {
        "/Auto/ScheduledState": ("disabled", None),
        "/Auto/ScheduledStateCode": (0, None),
        "/Auto/ScheduledReason": ("disabled", None),
        "/Auto/ScheduledReasonCode": (0, None),
        "/Auto/ScheduledNightBoostActive": (0, None),
        "/Auto/ScheduledTargetDayEnabled": (0, None),
        "/Auto/ScheduledTargetDay": ("", None),
        "/Auto/ScheduledTargetDate": ("", None),
        "/Auto/ScheduledFallbackStart": ("", None),
        "/Auto/ScheduledBoostUntil": ("", None),
    }


def _active_scheduled_diagnostic_defaults(snapshot: Any) -> PathMap:
    """Return scheduled diagnostics for one active scheduled-mode snapshot."""
    return {
        "/Auto/ScheduledState": (snapshot.state, None),
        "/Auto/ScheduledStateCode": (snapshot.state_code, None),
        "/Auto/ScheduledReason": (snapshot.reason, None),
        "/Auto/ScheduledReasonCode": (snapshot.reason_code, None),
        "/Auto/ScheduledNightBoostActive": (int(bool(snapshot.night_boost_active)), None),
        "/Auto/ScheduledTargetDayEnabled": (int(bool(snapshot.target_day_enabled)), None),
        "/Auto/ScheduledTargetDay": (snapshot.target_day_label, None),
        "/Auto/ScheduledTargetDate": (snapshot.target_date_text, None),
        "/Auto/ScheduledFallbackStart": (snapshot.fallback_start_text, None),
        "/Auto/ScheduledBoostUntil": (snapshot.boost_until_text, None),
    }


def _software_update_diagnostic_defaults(svc: Any) -> PathMap:
    """Return software-update diagnostic paths and their initial defaults."""
    return {
        "/Auto/SoftwareUpdateAvailable": (int(bool(getattr(svc, "_software_update_available", False))), None),
        "/Auto/SoftwareUpdateState": (str(getattr(svc, "_software_update_state", "idle")), None),
        "/Auto/SoftwareUpdateStateCode": (0, None),
        "/Auto/SoftwareUpdateDetail": (str(getattr(svc, "_software_update_detail", "")), None),
        "/Auto/SoftwareUpdateCurrentVersion": (str(getattr(svc, "_software_update_current_version", "")), None),
        "/Auto/SoftwareUpdateAvailableVersion": (str(getattr(svc, "_software_update_available_version", "")), None),
        "/Auto/SoftwareUpdateNoUpdateActive": (int(bool(getattr(svc, "_software_update_no_update_active", False))), None),
        "/Auto/SoftwareUpdateRun": (0, None),
        "/Auto/SoftwareUpdateLastCheckAge": (-1, None),
        "/Auto/SoftwareUpdateLastRunAge": (-1, None),
    }


def _backend_diagnostic_defaults(svc: Any) -> PathMap:
    """Return backend-composition diagnostic paths and their defaults."""
    return {
        "/Auto/BackendMode": (backend_mode_for_service(svc, "combined"), None),
        "/Auto/MeterBackend": (backend_type_for_service(svc, "meter", "shelly_meter"), None),
        "/Auto/SwitchBackend": (backend_type_for_service(svc, "switch", "shelly_contactor_switch"), None),
        "/Auto/ChargerBackend": (backend_type_for_service(svc, "charger", ""), None),
        "/Auto/ChargerStatus": ("", None),
        "/Auto/ChargerFault": ("", None),
        "/Auto/ChargerFaultActive": (0, None),
        "/Auto/ChargerEstimateActive": (0, None),
        "/Auto/ChargerEstimateSource": ("", None),
        "/Auto/RuntimeOverridesActive": (int(bool(getattr(svc, "_runtime_overrides_active", False))), None),
        "/Auto/RuntimeOverridesPath": (str(getattr(svc, "runtime_overrides_path", "")), None),
        "/Auto/ChargerTransportActive": (0, None),
        "/Auto/ChargerTransportReason": ("", None),
        "/Auto/ChargerTransportSource": ("", None),
        "/Auto/ChargerTransportDetail": ("", None),
        "/Auto/ChargerRetryActive": (0, None),
        "/Auto/ChargerRetryReason": ("", None),
        "/Auto/ChargerRetrySource": ("", None),
        "/Auto/ChargerCurrentTarget": (-1.0, None),
        "/Auto/LastChargerReadAge": (-1, None),
        "/Auto/LastChargerEstimateAge": (-1, None),
        "/Auto/LastChargerTransportAge": (-1, None),
        "/Auto/ChargerRetryRemaining": (-1, None),
    }


def _decision_diagnostic_defaults(svc: Any) -> PathMap:
    """Return compact Auto decision diagnostic paths and their defaults."""
    reason = str(getattr(svc, "_last_health_reason", "init"))
    state = str(getattr(svc, "_last_auto_state", "idle"))
    state_code = int(getattr(svc, "_last_auto_state_code", 0))
    return {
        "/Auto/DecisionReason": (reason, None),
        "/Auto/DecisionState": (state, None),
        "/Auto/DecisionStateCode": (state_code, None),
        "/Auto/DecisionRelayIntent": (-1, None),
        "/Auto/DecisionSurplusWatts": (-1.0, None),
        "/Auto/DecisionGridWatts": (-1.0, None),
        "/Auto/DecisionSocPercent": (-1.0, None),
        "/Auto/DecisionStartThresholdWatts": (-1.0, None),
        "/Auto/DecisionStopThresholdWatts": (-1.0, None),
        "/Auto/DecisionProfile": ("", None),
        "/Auto/DecisionThresholdMode": ("", None),
    }


def _phase_diagnostic_defaults(svc: Any) -> PathMap:
    """Return phase, switch-feedback, and contactor diagnostic path defaults."""
    supported = ",".join(getattr(svc, "supported_phase_selections", ("P1",)))
    return {
        "/Auto/PhaseCurrent": ("", None),
        "/Auto/PhaseObserved": ("", None),
        "/Auto/PhaseTarget": ("", None),
        "/Auto/PhaseReason": ("", None),
        "/Auto/PhaseMismatchActive": (0, None),
        "/Auto/PhaseLockoutActive": (0, None),
        "/Auto/PhaseLockoutTarget": ("", None),
        "/Auto/PhaseLockoutReason": ("", None),
        "/Auto/PhaseSupportedConfigured": (supported, None),
        "/Auto/PhaseSupportedEffective": (supported, None),
        "/Auto/PhaseDegradedActive": (0, None),
        "/Auto/SwitchFeedbackClosed": (-1, None),
        "/Auto/SwitchInterlockOk": (-1, None),
        "/Auto/SwitchFeedbackMismatch": (0, None),
        "/Auto/ContactorSuspectedOpen": (0, None),
        "/Auto/ContactorSuspectedWelded": (0, None),
        "/Auto/ContactorFaultCount": (0, None),
        "/Auto/ContactorLockoutActive": (0, None),
        "/Auto/ContactorLockoutReason": ("", None),
        "/Auto/ContactorLockoutSource": ("", None),
        "/Auto/ContactorLockoutReset": (0, None),
        "/Auto/PhaseLockoutReset": (0, None),
        "/Auto/PhaseThresholdWatts": (-1.0, None),
        "/Auto/PhaseCandidate": ("", None),
        "/Auto/PhaseCandidateAge": (-1, None),
        "/Auto/PhaseLockoutAge": (-1, None),
        "/Auto/ContactorLockoutAge": (-1, None),
        "/Auto/LastSwitchFeedbackAge": (-1, None),
    }


def _age_counter_diagnostic_defaults() -> PathMap:
    """Return age-like and aggregate diagnostic counters initialized to sentinel values."""
    return {
        "/Auto/ErrorCount": (0, None),
        "/Auto/DbusReadErrors": (0, None),
        "/Auto/ShellyReadErrors": (0, None),
        "/Auto/ShellyState": ("unknown", None),
        "/Auto/ShellyLastError": ("", None),
        "/Auto/ShellyRetryRemaining": (0, None),
        "/Auto/ShellyConsecutiveErrors": (0, None),
        "/Auto/ShellyLastOkAge": (-1, None),
        "/Auto/PendingRelayAge": (-1, None),
        "/Auto/ChargerWriteErrors": (0, None),
        "/Auto/PvReadErrors": (0, None),
        "/Auto/BatteryReadErrors": (0, None),
        "/Auto/GridReadErrors": (0, None),
        "/Auto/InputCacheHits": (0, None),
        "/Auto/LastShellyReadAge": (-1, None),
        "/Auto/LastPvReadAge": (-1, None),
        "/Auto/LastBatteryReadAge": (-1, None),
        "/Auto/LastGridReadAge": (-1, None),
        "/Auto/LastDbusReadAge": (-1, None),
        "/Auto/ChargerCurrentTargetAge": (-1, None),
        "/Auto/LastSuccessfulUpdateAge": (-1, None),
        "/Auto/Stale": (0, None),
        "/Auto/StaleSeconds": (0, None),
        "/Auto/RecoveryAttempts": (0, None),
        "/Auto/DbusIntrospectionState": ("unknown", None),
        "/Auto/DbusIntrospectionQueueDepth": (0, None),
        "/Auto/DbusIntrospectionServiceCount": (0, None),
        "/Auto/DbusIntrospectionUnusablePathCount": (0, None),
        "/Auto/DbusIntrospectionSnapshotAge": (-1, None),
    }


def _runtime_timing_diagnostic_defaults() -> PathMap:
    """Return async-runtime timing diagnostics initialized to neutral values."""
    return {
        "/Auto/UpdateWorkerDurationSeconds": (0.0, None),
        "/Auto/UpdateWorkerPending": (0, None),
        "/Auto/UpdateWorkerSkipped": (0, None),
        "/Auto/PublishFlushDurationSeconds": (0.0, None),
        "/Auto/PublishQueueLagSeconds": (0.0, None),
        "/Auto/PublishQueueDropped": (0, None),
        "/Auto/WriteCommandDurationSeconds": (0.0, None),
        "/Auto/WriteCommandQueueLagSeconds": (0.0, None),
        "/Auto/MainloopHeartbeatAge": (0.0, None),
    }


class _ServiceBootstrapPathMixin(_ComposableControllerMixin):
    @staticmethod
    def _connected_value(svc: Any) -> int:
        """Return one static initial Connected flag for DBus registration."""
        configured = getattr(svc, "topology_configured", getattr(svc, "host_configured", True))
        return 1 if bool(configured) else 0

    def register_paths(self) -> None:
        """Register all DBus paths exposed by the emulated EV charger."""
        svc = self.service
        self._register_management_paths()
        for path, (initial, formatter) in self._all_service_paths().items():
            logging.debug("Registering path: %s initial=%r formatter=%r", path, initial, formatter)
            try:
                svc._dbusservice.add_path(
                    path,
                    initial,
                    gettextcallback=formatter,
                    writeable=path in self.WRITABLE_PATHS,
                    onchangecallback=svc._handle_write,
                )
            except BOOTSTRAP_DBUS_REGISTRATION_ERRORS as error:
                logging.error("Failed to register path %s: %s", path, error, exc_info=error)
                raise

    def _register_management_paths(self) -> None:
        """Register immutable management and identity DBus paths."""
        svc = self.service
        svc._dbusservice.add_path("/Mgmt/ProcessName", self._script_path)
        svc._dbusservice.add_path(
            "/Mgmt/ProcessVersion",
            "Unknown version, and running on Python " + platform.python_version(),
        )
        svc._dbusservice.add_path("/Mgmt/Connection", svc.connection_name)
        svc._dbusservice.add_path("/DeviceInstance", svc.deviceinstance)
        svc._dbusservice.add_path("/ProductId", 0xFFFF)
        svc._dbusservice.add_path("/ProductName", svc.product_name)
        svc._dbusservice.add_path("/CustomName", svc.custom_name)
        svc._dbusservice.add_path("/FirmwareVersion", svc.firmware_version)
        svc._dbusservice.add_path("/HardwareVersion", svc.hardware_version)
        svc._dbusservice.add_path("/Serial", svc.serial)
        svc._dbusservice.add_path("/Connected", self._connected_value(svc))
        svc._dbusservice.add_path("/Position", svc.position)
        svc._dbusservice.add_path("/UpdateIndex", 0)

    def _measurement_paths(self) -> PathMap:
        """Return measurement and energy paths shown on the EV charger tile."""
        return {
            "/Ac/Power": (0.0, self._formatters["w"]),
            "/Ac/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L1/Power": (0.0, self._formatters["w"]),
            "/Ac/L2/Power": (0.0, self._formatters["w"]),
            "/Ac/L3/Power": (0.0, self._formatters["w"]),
            "/Ac/L1/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L2/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L3/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L1/Current": (0.0, self._formatters["a"]),
            "/Ac/L2/Current": (0.0, self._formatters["a"]),
            "/Ac/L3/Current": (0.0, self._formatters["a"]),
            "/Ac/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L1/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L2/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L3/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Session/Energy": (0.0, None),
            "/Session/Time": (0, None),
            "/Ac/Current": (0.0, self._formatters["a"]),
            "/Current": (0.0, self._formatters["a"]),
        }

    def _control_paths(self) -> PathMap:
        """Return writable and status-like EV charger control paths."""
        svc = self.service
        return {
            "/MinCurrent": (svc.min_current, self._formatters["a"]),
            "/MaxCurrent": (svc.max_current, self._formatters["a"]),
            "/SetCurrent": (svc.virtual_set_current, self._formatters["a"]),
            "/PhaseSelection": (getattr(svc, "requested_phase_selection", "P1"), None),
            "/PhaseSelectionActive": (getattr(svc, "active_phase_selection", "P1"), None),
            "/SupportedPhaseSelections": (
                ",".join(getattr(svc, "supported_phase_selections", ("P1",))),
                None,
            ),
            "/AutoStart": (svc.virtual_autostart, None),
            "/Auto/StartSurplusWatts": (getattr(svc, "auto_start_surplus_watts", 0.0), None),
            "/Auto/StopSurplusWatts": (getattr(svc, "auto_stop_surplus_watts", 0.0), None),
            "/Auto/MinSoc": (getattr(svc, "auto_min_soc", 0.0), None),
            "/Auto/ResumeSoc": (getattr(svc, "auto_resume_soc", 0.0), None),
            "/Auto/StartDelaySeconds": (getattr(svc, "auto_start_delay_seconds", 0.0), None),
            "/Auto/StopDelaySeconds": (getattr(svc, "auto_stop_delay_seconds", 0.0), None),
            "/Auto/ScheduledEnabledDays": (
                str(getattr(svc, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri")),
                None,
            ),
            "/Auto/ScheduledFallbackDelaySeconds": (
                getattr(svc, "auto_scheduled_night_start_delay_seconds", 0.0),
                None,
            ),
            "/Auto/ScheduledLatestEndTime": (str(getattr(svc, "auto_scheduled_latest_end_time", "06:30")), None),
            "/Auto/ScheduledNightCurrent": (getattr(svc, "auto_scheduled_night_current_amps", 0.0), None),
            "/Auto/DbusBackoffBaseSeconds": (getattr(svc, "auto_dbus_backoff_base_seconds", 0.0), None),
            "/Auto/DbusBackoffMaxSeconds": (getattr(svc, "auto_dbus_backoff_max_seconds", 0.0), None),
            "/Auto/GridRecoveryStartSeconds": (getattr(svc, "auto_grid_recovery_start_seconds", 0.0), None),
            "/Auto/StopSurplusDelaySeconds": (getattr(svc, "auto_stop_surplus_delay_seconds", 0.0), None),
            "/Auto/StopSurplusVolatilityLowWatts": (
                getattr(svc, "auto_stop_surplus_volatility_low_watts", 0.0),
                None,
            ),
            "/Auto/StopSurplusVolatilityHighWatts": (
                getattr(svc, "auto_stop_surplus_volatility_high_watts", 0.0),
                None,
            ),
            "/Auto/ReferenceChargePowerWatts": (getattr(svc, "auto_reference_charge_power_watts", 0.0), None),
            "/Auto/LearnChargePowerEnabled": (
                int(bool(getattr(svc, "auto_learn_charge_power_enabled", True))),
                None,
            ),
            "/Auto/LearnChargePowerMinWatts": (getattr(svc, "auto_learn_charge_power_min_watts", 0.0), None),
            "/Auto/LearnChargePowerAlpha": (getattr(svc, "auto_learn_charge_power_alpha", 0.0), None),
            "/Auto/LearnChargePowerStartDelaySeconds": (
                getattr(svc, "auto_learn_charge_power_start_delay_seconds", 0.0),
                None,
            ),
            "/Auto/LearnChargePowerWindowSeconds": (
                getattr(svc, "auto_learn_charge_power_window_seconds", 0.0),
                None,
            ),
            "/Auto/LearnChargePowerMaxAgeSeconds": (
                getattr(svc, "auto_learn_charge_power_max_age_seconds", 0.0),
                None,
            ),
            "/Auto/PhaseSwitching": (int(bool(getattr(svc, "auto_phase_switching_enabled", True))), None),
            "/Auto/PhasePreferLowestWhenIdle": (
                int(bool(getattr(svc, "auto_phase_prefer_lowest_when_idle", True))),
                None,
            ),
            "/Auto/PhaseUpshiftDelaySeconds": (getattr(svc, "auto_phase_upshift_delay_seconds", 0.0), None),
            "/Auto/PhaseDownshiftDelaySeconds": (getattr(svc, "auto_phase_downshift_delay_seconds", 0.0), None),
            "/Auto/PhaseUpshiftHeadroomWatts": (getattr(svc, "auto_phase_upshift_headroom_watts", 0.0), None),
            "/Auto/PhaseDownshiftMarginWatts": (getattr(svc, "auto_phase_downshift_margin_watts", 0.0), None),
            "/Auto/PhaseMismatchRetrySeconds": (getattr(svc, "auto_phase_mismatch_retry_seconds", 0.0), None),
            "/Auto/PhaseMismatchLockoutCount": (getattr(svc, "auto_phase_mismatch_lockout_count", 0), None),
            "/Auto/PhaseMismatchLockoutSeconds": (getattr(svc, "auto_phase_mismatch_lockout_seconds", 0.0), None),
            "/ChargingTime": (0, None),
            "/Mode": (svc.virtual_mode, None),
            "/StartStop": (svc.virtual_startstop, None),
            "/Enable": (svc.virtual_enable, None),
            "/Status": (0, self._formatters["status"]),
        }

    def _diagnostic_paths(self) -> PathMap:
        """Return Auto-diagnostic DBus paths published by the service."""
        svc = self.service
        scheduled_snapshot = self._scheduled_snapshot()
        return {
            "/Auto/Health": (svc._last_health_reason, None),
            "/Auto/HealthCode": (svc._last_health_code, None),
            "/Auto/State": (getattr(svc, "_last_auto_state", "idle"), None),
            "/Auto/StateCode": (getattr(svc, "_last_auto_state_code", 0), None),
            "/Auto/RecoveryActive": (0, None),
            "/Auto/StatusSource": (str(getattr(svc, "_last_status_source", "unknown")), None),
            "/Auto/FaultActive": (0, None),
            "/Auto/FaultReason": ("", None),
            **_scheduled_diagnostic_defaults(scheduled_snapshot),
            **_backend_diagnostic_defaults(svc),
            **_decision_diagnostic_defaults(svc),
            **_software_update_diagnostic_defaults(svc),
            **_phase_diagnostic_defaults(svc),
            **_age_counter_diagnostic_defaults(),
            **_runtime_timing_diagnostic_defaults(),
        }

    def _all_service_paths(self) -> PathMap:
        """Return the complete dynamic EV charger DBus path map."""
        return {
            **self._measurement_paths(),
            **self._control_paths(),
            **self._diagnostic_paths(),
        }

    def _scheduled_snapshot(self) -> Any | None:
        """Return one initial scheduled-mode diagnostic snapshot for path registration."""
        svc = self.service
        if not mode_uses_scheduled_logic(getattr(svc, "virtual_mode", 0)):
            return None
        return scheduled_mode_snapshot(
            datetime.fromtimestamp(time.time()),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "06:30"),
        )
