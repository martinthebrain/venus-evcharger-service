# SPDX-License-Identifier: GPL-3.0-or-later
"""Venus EVCS surface contract owned by the DBus gateway boundary.

The dedicated gateway owns all transport-level DBus access and the concrete
Venus path surface exposed to the GUI/VRM. Core code should treat these values
as gateway contract data, not as backend policy.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.core.contracts_control_surface import (
    EVCS_CONTACTOR_AGE_PATHS,
    EVCS_CONTACTOR_CONTROL_PATHS,
    EVCS_PHASE_CONTROL_PATHS,
    EVCS_PRIMARY_CONTROL_PATHS,
    EVCS_REQUIRED_CONTROL_PATHS,
    EVCS_SOFTWARE_UPDATE_CONTROL_PATHS,
    EVCS_WRITABLE_PATHS,
)

VenusPathRole = Literal["identity", "measurement", "control", "status"]


@dataclass(frozen=True)
class VenusDbusPathContract:
    """One GUI/VRM-visible EV charger path contract."""

    path: str
    role: VenusPathRole
    writeable: bool = False


VENUS_EV_CHARGER_IDENTITY_PATHS = (
    "/Mgmt/ProcessName",
    "/Mgmt/ProcessVersion",
    "/Mgmt/Connection",
    "/DeviceInstance",
    "/ProductId",
    "/ProductName",
    "/CustomName",
    "/FirmwareVersion",
    "/HardwareVersion",
    "/Serial",
    "/Connected",
    "/UpdateIndex",
    "/Position",
)

VENUS_EV_CHARGER_MEASUREMENT_PATHS = (
    "/Ac/Power",
    "/Ac/Current",
    "/Ac/Voltage",
    "/Ac/Energy/Forward",
    "/Session/Energy",
    "/Session/Time",
)

VENUS_EV_CHARGER_STATUS_PATHS = (
    "/Status",
    "/Auto/Health",
    "/Auto/State",
    "/Auto/StatusSource",
)

VENUS_EV_CHARGER_WRITABLE_PATHS = EVCS_WRITABLE_PATHS

VENUS_EV_CHARGER_REQUIRED_CONTRACTS = tuple(
    VenusDbusPathContract(path, "identity") for path in VENUS_EV_CHARGER_IDENTITY_PATHS
) + tuple(VenusDbusPathContract(path, "measurement") for path in VENUS_EV_CHARGER_MEASUREMENT_PATHS) + tuple(
    VenusDbusPathContract(path, "status", path in VENUS_EV_CHARGER_WRITABLE_PATHS)
    for path in VENUS_EV_CHARGER_STATUS_PATHS
) + tuple(
    VenusDbusPathContract(path, "control", True)
    for path in EVCS_REQUIRED_CONTROL_PATHS
)

EVCS_LIVE_MEASUREMENT_FIELDS = {
    "ac_power_w": "/Ac/Power",
    "ac_voltage_v": "/Ac/Voltage",
    "ac_current_a": "/Ac/Current",
    "charge_current_a": "/Current",
    "l1_power_w": "/Ac/L1/Power",
    "l1_current_a": "/Ac/L1/Current",
    "l1_voltage_v": "/Ac/L1/Voltage",
    "l2_power_w": "/Ac/L2/Power",
    "l2_current_a": "/Ac/L2/Current",
    "l2_voltage_v": "/Ac/L2/Voltage",
    "l3_power_w": "/Ac/L3/Power",
    "l3_current_a": "/Ac/L3/Current",
    "l3_voltage_v": "/Ac/L3/Voltage",
}

EVCS_ENERGY_TIME_FIELDS = {
    "energy_forward_kwh": "/Ac/Energy/Forward",
    "l1_energy_forward_kwh": "/Ac/L1/Energy/Forward",
    "l2_energy_forward_kwh": "/Ac/L2/Energy/Forward",
    "l3_energy_forward_kwh": "/Ac/L3/Energy/Forward",
    "charging_time_s": "/ChargingTime",
    "session_energy_kwh": "/Session/Energy",
    "session_time_s": "/Session/Time",
}

EVCS_FIELD_TO_PATH = {
    **EVCS_LIVE_MEASUREMENT_FIELDS,
    **EVCS_ENERGY_TIME_FIELDS,
}


def _field_name_from_venus_path(path: str) -> str:
    return _snake_case(str(path))


def _snake_case(value: str) -> str:
    underscored = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", underscored).strip("_").lower()


EVCS_CONFIG_AND_DIAGNOSTIC_PATHS = (
    "/UpdateIndex",
    "/Connected",
    "/Status",
    *EVCS_PRIMARY_CONTROL_PATHS,
    "/Auto/Health",
    "/Auto/HealthCode",
    "/Auto/State",
    "/Auto/StateCode",
    "/Auto/RecoveryActive",
    "/Auto/StatusSource",
    "/Auto/FaultActive",
    "/Auto/FaultReason",
    "/Auto/Stale",
    "/Auto/RecoveryAttempts",
    "/Auto/LastShellyReadAge",
    "/Auto/ShellyLastOkAge",
    "/Auto/PendingRelayAge",
    "/Auto/LastPvReadAge",
    "/Auto/LastBatteryReadAge",
    "/Auto/LastGridReadAge",
    "/Auto/LastDbusReadAge",
    "/Auto/ChargerCurrentTargetAge",
    "/Auto/PhaseCandidateAge",
    "/Auto/PhaseLockoutAge",
    *EVCS_CONTACTOR_AGE_PATHS,
    "/Auto/LastSwitchFeedbackAge",
    "/Auto/LastChargerReadAge",
    "/Auto/LastChargerEstimateAge",
    "/Auto/LastChargerTransportAge",
    "/Auto/ChargerRetryRemaining",
    "/Auto/LastSuccessfulUpdateAge",
    "/Auto/SoftwareUpdateLastCheckAge",
    "/Auto/SoftwareUpdateLastRunAge",
    "/Auto/StaleSeconds",
    "/Auto/DbusIntrospectionSnapshotAge",
    "/Auto/PhaseCurrent",
    "/Auto/PhaseObserved",
    "/Auto/PhaseTarget",
    "/Auto/PhaseReason",
    "/Auto/PhaseMismatchActive",
    *EVCS_PHASE_CONTROL_PATHS,
    "/Auto/PhaseThresholdWatts",
    "/Auto/PhaseCandidate",
    "/Auto/SwitchFeedbackClosed",
    "/Auto/SwitchInterlockOk",
    "/Auto/SwitchFeedbackMismatch",
    "/Auto/ContactorSuspectedOpen",
    "/Auto/ContactorSuspectedWelded",
    *EVCS_CONTACTOR_CONTROL_PATHS,
    "/Auto/UpdateWorkerDurationSeconds",
    "/Auto/UpdateWorkerPending",
    "/Auto/UpdateWorkerSkipped",
    "/Auto/PublishFlushDurationSeconds",
    "/Auto/PublishQueueLagSeconds",
    "/Auto/PublishQueueDropped",
    "/Auto/WriteCommandDurationSeconds",
    "/Auto/WriteCommandQueueLagSeconds",
    "/Auto/MainloopHeartbeatAge",
    "/Auto/DecisionReason",
    "/Auto/DecisionState",
    "/Auto/DecisionStateCode",
    "/Auto/DecisionRelayIntent",
    "/Auto/DecisionSurplusWatts",
    "/Auto/DecisionGridWatts",
    "/Auto/DecisionSocPercent",
    "/Auto/DecisionStartThresholdWatts",
    "/Auto/DecisionStopThresholdWatts",
    "/Auto/DecisionProfile",
    "/Auto/DecisionThresholdMode",
    "/Auto/ScheduledState",
    "/Auto/ScheduledStateCode",
    "/Auto/ScheduledReason",
    "/Auto/ScheduledReasonCode",
    "/Auto/ScheduledNightBoostActive",
    "/Auto/ScheduledTargetDayEnabled",
    "/Auto/ScheduledTargetDay",
    "/Auto/ScheduledTargetDate",
    "/Auto/ScheduledFallbackStart",
    "/Auto/ScheduledBoostUntil",
    "/Auto/SoftwareUpdateAvailable",
    "/Auto/SoftwareUpdateState",
    "/Auto/SoftwareUpdateStateCode",
    "/Auto/SoftwareUpdateDetail",
    "/Auto/SoftwareUpdateCurrentVersion",
    "/Auto/SoftwareUpdateAvailableVersion",
    "/Auto/SoftwareUpdateNoUpdateActive",
    *EVCS_SOFTWARE_UPDATE_CONTROL_PATHS,
    "/Auto/BackendMode",
    "/Auto/MeterBackend",
    "/Auto/SwitchBackend",
    "/Auto/ChargerBackend",
    "/Auto/RuntimeOverridesActive",
    "/Auto/RuntimeOverridesPath",
    "/Auto/ChargerStatus",
    "/Auto/ChargerFault",
    "/Auto/ChargerFaultActive",
    "/Auto/ChargerEstimateActive",
    "/Auto/ChargerEstimateSource",
    "/Auto/ChargerTransportActive",
    "/Auto/ChargerTransportReason",
    "/Auto/ChargerTransportSource",
    "/Auto/ChargerTransportDetail",
    "/Auto/ChargerRetryActive",
    "/Auto/ChargerRetryReason",
    "/Auto/ChargerRetrySource",
    "/Auto/ChargerCurrentTarget",
    "/Auto/ErrorCount",
    "/Auto/DbusReadErrors",
    "/Auto/ShellyReadErrors",
    "/Auto/ChargerWriteErrors",
    "/Auto/PvReadErrors",
    "/Auto/BatteryReadErrors",
    "/Auto/GridReadErrors",
    "/Auto/InputCacheHits",
    "/Auto/ShellyState",
    "/Auto/ShellyLastError",
    "/Auto/ShellyRetryRemaining",
    "/Auto/ShellyConsecutiveErrors",
    "/Auto/DbusIntrospectionState",
    "/Auto/DbusIntrospectionQueueDepth",
    "/Auto/DbusIntrospectionServiceCount",
    "/Auto/DbusIntrospectionUnusablePathCount",
)

EVCS_FIELD_TO_PATH.update({
    _field_name_from_venus_path(path): path
    for path in EVCS_CONFIG_AND_DIAGNOSTIC_PATHS
    if _field_name_from_venus_path(path) not in EVCS_FIELD_TO_PATH
})

EVCS_PATH_TO_FIELD = {path: field for field, path in EVCS_FIELD_TO_PATH.items()}


def evcs_path_to_field(path: str) -> str:
    """Translate one concrete Venus DBus path to its semantic EVCS publish field."""
    return EVCS_PATH_TO_FIELD.get(str(path), "")


def evcs_fields_to_paths(fields: Mapping[str, object]) -> dict[str, object]:
    """Translate semantic EVCS publish fields to concrete Venus DBus paths."""
    paths: dict[str, object] = {}
    for field, value in fields.items():
        path = EVCS_FIELD_TO_PATH.get(str(field))
        if path:
            paths[path] = value
    return paths


def missing_required_venus_paths(registered_paths: set[str] | frozenset[str]) -> tuple[str, ...]:
    """Return required EVCS paths that were not registered."""
    return tuple(
        contract.path
        for contract in VENUS_EV_CHARGER_REQUIRED_CONTRACTS
        if contract.path not in registered_paths
    )


def mismatched_venus_writeability(path: str, writeable: bool) -> bool:
    """Return whether a registered path violates the EVCS writeability contract."""
    expected = path in VENUS_EV_CHARGER_WRITABLE_PATHS
    return bool(writeable) != expected


def venus_path_writeable(path: str) -> bool:
    """Return whether the Venus EVCS surface expects the path to be writeable."""
    return path in VENUS_EV_CHARGER_WRITABLE_PATHS
