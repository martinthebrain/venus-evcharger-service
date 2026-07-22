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

from venus_evcharger.control.models import ControlCommandName, ControlRoute
from venus_evcharger.core.contracts_control_surface import (
    CONTROL_DIRECT_TARGET_COMMANDS,
    CONTROL_REQUIRED_TARGETS,
    CONTROL_TARGET_BY_NAME,
    CONTROL_WRITABLE_TARGETS,
    CONTROL_WRITE_SNAPSHOT_TARGETS,
)
from venus_evcharger.dbus_gateway_core import CacheFreshnessKind

VenusPathRole = Literal["identity", "measurement", "control", "status"]


@dataclass(frozen=True)
class VenusDbusPathContract:
    """One GUI/VRM-visible EV charger path contract."""

    path: str
    role: VenusPathRole
    writeable: bool = False


def _snake_case(value: str) -> str:
    underscored = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", underscored).strip("_").lower()


_VENUS_DIRECT_CONTROL_PATHS = (
    "/Mode",
    "/AutoStart",
    "/StartStop",
    "/Enable",
    "/PhaseSelection",
)
_VENUS_PHASE_STATUS_PATHS = (
    "/PhaseSelectionActive",
    "/SupportedPhaseSelections",
    "/Auto/PhaseLockoutActive",
    "/Auto/PhaseLockoutTarget",
    "/Auto/PhaseLockoutReason",
    "/Auto/PhaseSupportedConfigured",
    "/Auto/PhaseSupportedEffective",
    "/Auto/PhaseDegradedActive",
)
_VENUS_CONTACTOR_STATUS_PATHS = (
    "/Auto/ContactorFaultCount",
    "/Auto/ContactorLockoutActive",
    "/Auto/ContactorLockoutReason",
    "/Auto/ContactorLockoutSource",
    "/Auto/ContactorLockoutAge",
)
_VENUS_CURRENT_SETTING_PATHS = ("/SetCurrent", "/MinCurrent", "/MaxCurrent")
_VENUS_AUTO_RUNTIME_PATHS = (
    "/Auto/StartSurplusWatts",
    "/Auto/StopSurplusWatts",
    "/Auto/MinSoc",
    "/Auto/ResumeSoc",
    "/Auto/StartDelaySeconds",
    "/Auto/StopDelaySeconds",
    "/Auto/ScheduledEnabledDays",
    "/Auto/ScheduledFallbackDelaySeconds",
    "/Auto/ScheduledLatestEndTime",
    "/Auto/ScheduledNightCurrent",
    "/Auto/DbusBackoffBaseSeconds",
    "/Auto/DbusBackoffMaxSeconds",
    "/Auto/GridRecoveryStartSeconds",
    "/Auto/StopSurplusDelaySeconds",
    "/Auto/StopSurplusVolatilityLowWatts",
    "/Auto/StopSurplusVolatilityHighWatts",
    "/Auto/ReferenceChargePowerWatts",
    "/Auto/LearnChargePowerEnabled",
    "/Auto/LearnChargePowerMinWatts",
    "/Auto/LearnChargePowerAlpha",
    "/Auto/LearnChargePowerStartDelaySeconds",
    "/Auto/LearnChargePowerWindowSeconds",
    "/Auto/LearnChargePowerMaxAgeSeconds",
    "/Auto/PhaseSwitching",
    "/Auto/PhasePreferLowestWhenIdle",
    "/Auto/PhaseUpshiftDelaySeconds",
    "/Auto/PhaseDownshiftDelaySeconds",
    "/Auto/PhaseUpshiftHeadroomWatts",
    "/Auto/PhaseDownshiftMarginWatts",
    "/Auto/PhaseMismatchRetrySeconds",
    "/Auto/PhaseMismatchLockoutCount",
    "/Auto/PhaseMismatchLockoutSeconds",
)
_VENUS_PHASE_RESET_PATH = "/Auto/PhaseLockoutReset"
_VENUS_CONTACTOR_RESET_PATH = "/Auto/ContactorLockoutReset"
_VENUS_SOFTWARE_UPDATE_PATH = "/Auto/SoftwareUpdateRun"

VENUS_CONTROL_PATHS = (
    *_VENUS_DIRECT_CONTROL_PATHS,
    *_VENUS_PHASE_STATUS_PATHS,
    _VENUS_PHASE_RESET_PATH,
    *_VENUS_CONTACTOR_STATUS_PATHS,
    _VENUS_CONTACTOR_RESET_PATH,
    *_VENUS_CURRENT_SETTING_PATHS,
    *_VENUS_AUTO_RUNTIME_PATHS,
    _VENUS_SOFTWARE_UPDATE_PATH,
)
VENUS_CONTROL_PATH_TO_TARGET = {path: _snake_case(path) for path in VENUS_CONTROL_PATHS}
VENUS_CONTROL_TARGET_TO_PATH = {target: path for path, target in VENUS_CONTROL_PATH_TO_TARGET.items()}


def _validate_control_target_coverage(
    gateway_targets: Mapping[str, object],
    domain_targets: Mapping[str, object],
) -> None:
    if set(gateway_targets) != set(domain_targets):
        raise ValueError("Venus control paths and domain control targets are out of sync.")


_validate_control_target_coverage(VENUS_CONTROL_TARGET_TO_PATH, CONTROL_TARGET_BY_NAME)


def _command_for_target(target: str) -> ControlCommandName | None:
    direct = CONTROL_DIRECT_TARGET_COMMANDS.get(target)
    if direct is not None:
        return direct
    contract = CONTROL_TARGET_BY_NAME[target]
    if contract.value_kind == "current":
        return "set_current_setting"
    if target.startswith("auto_") and contract.writable:
        return "set_auto_runtime_setting"
    return None


VENUS_CONTROL_ROUTES = {
    path: ControlRoute(command, target)
    for path, target in VENUS_CONTROL_PATH_TO_TARGET.items()
    if (command := _command_for_target(target)) is not None
}
VENUS_EV_CHARGER_WRITABLE_PATHS = frozenset(
    VENUS_CONTROL_TARGET_TO_PATH[target] for target in CONTROL_WRITABLE_TARGETS
)
VENUS_EV_CHARGER_REQUIRED_CONTROL_PATHS = tuple(
    VENUS_CONTROL_TARGET_TO_PATH[target] for target in CONTROL_REQUIRED_TARGETS
)
VENUS_EV_CHARGER_WRITE_SNAPSHOT_PATHS = tuple(
    VENUS_CONTROL_TARGET_TO_PATH[target] for target in CONTROL_WRITE_SNAPSHOT_TARGETS
)


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

VENUS_EV_CHARGER_STATIC_PATHS = frozenset(VENUS_EV_CHARGER_IDENTITY_PATHS) - {
    "/Connected",
    "/UpdateIndex",
}

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

VENUS_EV_CHARGER_REQUIRED_CONTRACTS = tuple(
    VenusDbusPathContract(path, "identity") for path in VENUS_EV_CHARGER_IDENTITY_PATHS
) + tuple(VenusDbusPathContract(path, "measurement") for path in VENUS_EV_CHARGER_MEASUREMENT_PATHS) + tuple(
    VenusDbusPathContract(path, "status", path in VENUS_EV_CHARGER_WRITABLE_PATHS)
    for path in VENUS_EV_CHARGER_STATUS_PATHS
) + tuple(
    VenusDbusPathContract(path, "control", True)
    for path in VENUS_EV_CHARGER_REQUIRED_CONTROL_PATHS
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


EVCS_PRIMARY_CONTROL_PATHS = (
    *_VENUS_DIRECT_CONTROL_PATHS,
    *_VENUS_PHASE_STATUS_PATHS[:2],
    *_VENUS_CURRENT_SETTING_PATHS,
    *_VENUS_AUTO_RUNTIME_PATHS,
)
EVCS_PHASE_CONTROL_PATHS = (
    *_VENUS_PHASE_STATUS_PATHS[2:5],
    _VENUS_PHASE_RESET_PATH,
    *_VENUS_PHASE_STATUS_PATHS[5:],
)
EVCS_CONTACTOR_AGE_PATHS = (_VENUS_CONTACTOR_STATUS_PATHS[-1],)
EVCS_CONTACTOR_CONTROL_PATHS = (
    *_VENUS_CONTACTOR_STATUS_PATHS[:-1],
    _VENUS_CONTACTOR_RESET_PATH,
)
EVCS_SOFTWARE_UPDATE_CONTROL_PATHS = (_VENUS_SOFTWARE_UPDATE_PATH,)


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
    "/Auto/GatewayDiagnosticsAge",
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
    "/Auto/GatewayDiscoveryState",
    "/Auto/GatewayDiscoveryPendingWork",
    "/Auto/GatewayDiscoveredSourceCount",
    "/Auto/GatewayUnusableSourceCount",
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


def venus_control_route(path: str) -> ControlRoute | None:
    """Resolve a writable Venus path to its transport-neutral control route."""
    return VENUS_CONTROL_ROUTES.get(str(path))


def venus_path_for_control_target(target: str) -> str:
    """Return the adapter-owned Venus path for one semantic control target."""
    return VENUS_CONTROL_TARGET_TO_PATH.get(str(target), "")


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


def evcs_path_freshness_kind(path: str) -> CacheFreshnessKind:
    """Classify one adapter-owned path for cache freshness diagnostics."""
    normalized = str(path)
    if normalized in VENUS_EV_CHARGER_STATIC_PATHS:
        return "static"
    if normalized.startswith("/Auto/"):
        return "diagnostic"
    return "local_owned"
