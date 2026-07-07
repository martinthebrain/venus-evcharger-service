# SPDX-License-Identifier: GPL-3.0-or-later
"""Static DBus path defaults used during EV charger service registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service

PathSpec = tuple[Any, Callable[[Any, Any], str] | None]
PathMap = dict[str, PathSpec]

_METER_BACKEND_ROLE = "meter"
_SWITCH_BACKEND_ROLE = "switch"
_CHARGER_BACKEND_ROLE = "charger"
_METER_BACKEND_DEFAULT = "shelly_meter"
_SWITCH_BACKEND_DEFAULT = "shelly_contactor_switch"


def _diagnostic_flag(svc: Any, attribute_name: str) -> int:
    """Return one DBus-style integer flag from an optional service attribute."""
    if not hasattr(svc, attribute_name):
        return 0
    return int(bool(getattr(svc, attribute_name)))


def scheduled_diagnostic_defaults(snapshot: Any) -> PathMap:
    """Return scheduled-mode diagnostic paths with normalized disabled defaults."""
    if snapshot is None:
        return disabled_scheduled_diagnostic_defaults()
    return active_scheduled_diagnostic_defaults(snapshot)


def disabled_scheduled_diagnostic_defaults() -> PathMap:
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


def active_scheduled_diagnostic_defaults(snapshot: Any) -> PathMap:
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


def software_update_diagnostic_defaults(svc: Any) -> PathMap:
    """Return software-update diagnostic paths and their initial defaults."""
    return {
        "/Auto/SoftwareUpdateAvailable": (_diagnostic_flag(svc, "_software_update_available"), None),
        "/Auto/SoftwareUpdateState": (str(getattr(svc, "_software_update_state", "idle")), None),
        "/Auto/SoftwareUpdateStateCode": (0, None),
        "/Auto/SoftwareUpdateDetail": (str(getattr(svc, "_software_update_detail", "")), None),
        "/Auto/SoftwareUpdateCurrentVersion": (str(getattr(svc, "_software_update_current_version", "")), None),
        "/Auto/SoftwareUpdateAvailableVersion": (str(getattr(svc, "_software_update_available_version", "")), None),
        "/Auto/SoftwareUpdateNoUpdateActive": (
            _diagnostic_flag(svc, "_software_update_no_update_active"),
            None,
        ),
        "/Auto/SoftwareUpdateRun": (0, None),
        "/Auto/SoftwareUpdateLastCheckAge": (-1, None),
        "/Auto/SoftwareUpdateLastRunAge": (-1, None),
    }


def backend_diagnostic_defaults(svc: Any) -> PathMap:
    """Return backend-composition diagnostic paths and their defaults."""
    return {
        "/Auto/BackendMode": (backend_mode_for_service(svc), None),
        "/Auto/MeterBackend": (backend_type_for_service(svc, _METER_BACKEND_ROLE, _METER_BACKEND_DEFAULT), None),
        "/Auto/SwitchBackend": (
            backend_type_for_service(svc, _SWITCH_BACKEND_ROLE, _SWITCH_BACKEND_DEFAULT),
            None,
        ),
        "/Auto/ChargerBackend": (
            backend_type_for_service(svc, _CHARGER_BACKEND_ROLE),
            None,
        ),
        "/Auto/ChargerStatus": ("", None),
        "/Auto/ChargerFault": ("", None),
        "/Auto/ChargerFaultActive": (0, None),
        "/Auto/ChargerEstimateActive": (0, None),
        "/Auto/ChargerEstimateSource": ("", None),
        "/Auto/RuntimeOverridesActive": (_diagnostic_flag(svc, "_runtime_overrides_active"), None),
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


def decision_diagnostic_defaults(svc: Any) -> PathMap:
    """Return compact Auto decision diagnostic paths and their defaults."""
    reason = str(getattr(svc, "_last_health_reason", "init"))
    state = str(getattr(svc, "_last_auto_state", "idle"))
    raw_state_code = getattr(svc, "_last_auto_state_code", 0)
    state_code = int(raw_state_code)
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


def phase_diagnostic_defaults(svc: Any) -> PathMap:
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


def age_counter_diagnostic_defaults() -> PathMap:
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


def runtime_timing_diagnostic_defaults() -> PathMap:
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
