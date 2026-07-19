# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed registry for the public EVCS control surface.

This module contains data only and deliberately has no runtime dependency on
the DBus gateway, HTTP adapter, or write controller.  Those boundaries derive
their path and command views from this registry instead of maintaining parallel
lists that can drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from venus_evcharger.control.models import ControlCommandName
else:
    ControlCommandName = str

ControlValueKind = Literal[
    "binary",
    "current",
    "float",
    "integer",
    "mode",
    "phase_selection",
    "string",
]


@dataclass(frozen=True, slots=True)
class EvcsControlPathContract:
    """One path shared by the EVCS gateway and control boundaries."""

    path: str
    writable: bool = True
    rollback_snapshot: bool = True
    direct_command: ControlCommandName | None = None
    value_kind: ControlValueKind | None = None
    required_order: int | None = None


_DIRECT_CONTROL_PATHS = (
    EvcsControlPathContract("/Mode", direct_command="set_mode", value_kind="mode", required_order=0),
    EvcsControlPathContract("/AutoStart", direct_command="set_auto_start", value_kind="binary", required_order=4),
    EvcsControlPathContract("/StartStop", direct_command="set_start_stop", value_kind="binary", required_order=1),
    EvcsControlPathContract("/Enable", direct_command="set_enable", value_kind="binary", required_order=2),
    EvcsControlPathContract(
        "/PhaseSelection",
        direct_command="set_phase_selection",
        value_kind="phase_selection",
    ),
)

_PHASE_STATUS_PATHS = (
    "/PhaseSelectionActive",
    "/SupportedPhaseSelections",
    "/Auto/PhaseLockoutActive",
    "/Auto/PhaseLockoutTarget",
    "/Auto/PhaseLockoutReason",
    "/Auto/PhaseSupportedConfigured",
    "/Auto/PhaseSupportedEffective",
    "/Auto/PhaseDegradedActive",
)

_CONTACTOR_STATUS_PATHS = (
    "/Auto/ContactorFaultCount",
    "/Auto/ContactorLockoutActive",
    "/Auto/ContactorLockoutReason",
    "/Auto/ContactorLockoutSource",
    "/Auto/ContactorLockoutAge",
)

_CURRENT_SETTING_PATHS = (
    EvcsControlPathContract("/SetCurrent", value_kind="current", required_order=3),
    EvcsControlPathContract("/MinCurrent", value_kind="current"),
    EvcsControlPathContract("/MaxCurrent", value_kind="current"),
)

_PHASE_LOCKOUT_RESET_CONTRACT = EvcsControlPathContract(
    "/Auto/PhaseLockoutReset",
    direct_command="reset_phase_lockout",
    value_kind="binary",
)

_CONTACTOR_LOCKOUT_RESET_CONTRACT = EvcsControlPathContract(
    "/Auto/ContactorLockoutReset",
    direct_command="reset_contactor_lockout",
    value_kind="binary",
)

_AUTO_RUNTIME_PATH_SPECS: tuple[tuple[str, ControlValueKind], ...] = (
    ("/Auto/StartSurplusWatts", "float"),
    ("/Auto/StopSurplusWatts", "float"),
    ("/Auto/MinSoc", "float"),
    ("/Auto/ResumeSoc", "float"),
    ("/Auto/StartDelaySeconds", "float"),
    ("/Auto/StopDelaySeconds", "float"),
    ("/Auto/ScheduledEnabledDays", "string"),
    ("/Auto/ScheduledFallbackDelaySeconds", "float"),
    ("/Auto/ScheduledLatestEndTime", "string"),
    ("/Auto/ScheduledNightCurrent", "float"),
    ("/Auto/DbusBackoffBaseSeconds", "float"),
    ("/Auto/DbusBackoffMaxSeconds", "float"),
    ("/Auto/GridRecoveryStartSeconds", "float"),
    ("/Auto/StopSurplusDelaySeconds", "float"),
    ("/Auto/StopSurplusVolatilityLowWatts", "float"),
    ("/Auto/StopSurplusVolatilityHighWatts", "float"),
    ("/Auto/ReferenceChargePowerWatts", "float"),
    ("/Auto/LearnChargePowerEnabled", "binary"),
    ("/Auto/LearnChargePowerMinWatts", "float"),
    ("/Auto/LearnChargePowerAlpha", "float"),
    ("/Auto/LearnChargePowerStartDelaySeconds", "float"),
    ("/Auto/LearnChargePowerWindowSeconds", "float"),
    ("/Auto/LearnChargePowerMaxAgeSeconds", "float"),
    ("/Auto/PhaseSwitching", "binary"),
    ("/Auto/PhasePreferLowestWhenIdle", "binary"),
    ("/Auto/PhaseUpshiftDelaySeconds", "float"),
    ("/Auto/PhaseDownshiftDelaySeconds", "float"),
    ("/Auto/PhaseUpshiftHeadroomWatts", "float"),
    ("/Auto/PhaseDownshiftMarginWatts", "float"),
    ("/Auto/PhaseMismatchRetrySeconds", "float"),
    ("/Auto/PhaseMismatchLockoutCount", "integer"),
    ("/Auto/PhaseMismatchLockoutSeconds", "float"),
)

_SOFTWARE_UPDATE_CONTRACT = EvcsControlPathContract(
    "/Auto/SoftwareUpdateRun",
    rollback_snapshot=False,
    direct_command="trigger_software_update",
    value_kind="binary",
)


EVCS_CONTROL_PATH_CONTRACTS = (
    *_DIRECT_CONTROL_PATHS,
    *(EvcsControlPathContract(path, writable=False) for path in _PHASE_STATUS_PATHS),
    _PHASE_LOCKOUT_RESET_CONTRACT,
    *(EvcsControlPathContract(path, writable=False) for path in _CONTACTOR_STATUS_PATHS),
    _CONTACTOR_LOCKOUT_RESET_CONTRACT,
    *_CURRENT_SETTING_PATHS,
    *(EvcsControlPathContract(path, value_kind=value_kind) for path, value_kind in _AUTO_RUNTIME_PATH_SPECS),
    _SOFTWARE_UPDATE_CONTRACT,
)


def _path_index(
    contracts: tuple[EvcsControlPathContract, ...],
) -> Mapping[str, EvcsControlPathContract]:
    indexed = {contract.path: contract for contract in contracts}
    if len(indexed) != len(contracts):
        raise ValueError("EVCS control path registry contains duplicate paths.")
    return MappingProxyType(indexed)


EVCS_CONTROL_PATH_BY_PATH = _path_index(EVCS_CONTROL_PATH_CONTRACTS)
EVCS_WRITABLE_PATHS = frozenset(
    contract.path for contract in EVCS_CONTROL_PATH_CONTRACTS if contract.writable
)
EVCS_WRITE_SNAPSHOT_PATHS = tuple(
    contract.path for contract in EVCS_CONTROL_PATH_CONTRACTS if contract.rollback_snapshot
)
EVCS_REQUIRED_CONTROL_PATHS = tuple(
    contract.path
    for contract in sorted(
        (item for item in EVCS_CONTROL_PATH_CONTRACTS if item.required_order is not None),
        key=lambda item: item.required_order if item.required_order is not None else -1,
    )
)

# Ordered views preserve the established field-map order without repeating
# transport path literals outside this registry.
EVCS_PRIMARY_CONTROL_PATHS = (
    *(contract.path for contract in _DIRECT_CONTROL_PATHS),
    *_PHASE_STATUS_PATHS[:2],
    *(contract.path for contract in _CURRENT_SETTING_PATHS),
    *(path for path, _value_kind in _AUTO_RUNTIME_PATH_SPECS),
)
EVCS_PHASE_CONTROL_PATHS = (
    *_PHASE_STATUS_PATHS[2:5],
    _PHASE_LOCKOUT_RESET_CONTRACT.path,
    *_PHASE_STATUS_PATHS[5:],
)
EVCS_CONTACTOR_AGE_PATHS = (_CONTACTOR_STATUS_PATHS[-1],)
EVCS_CONTACTOR_CONTROL_PATHS = (
    *_CONTACTOR_STATUS_PATHS[:-1],
    _CONTACTOR_LOCKOUT_RESET_CONTRACT.path,
)
EVCS_SOFTWARE_UPDATE_CONTROL_PATHS = (_SOFTWARE_UPDATE_CONTRACT.path,)

CONTROL_DIRECT_PATH_COMMANDS: Mapping[str, ControlCommandName] = MappingProxyType(
    {
        contract.path: contract.direct_command
        for contract in EVCS_CONTROL_PATH_CONTRACTS
        if contract.direct_command is not None
    }
)
CONTROL_COMMAND_DEFAULT_PATHS: Mapping[ControlCommandName, str] = MappingProxyType(
    {command_name: path for path, command_name in CONTROL_DIRECT_PATH_COMMANDS.items()}
)

_NON_DIRECT_COMMAND_NAMES: tuple[ControlCommandName, ...] = (
    "set_auto_runtime_setting",
    "set_current_setting",
)
CONTROL_COMMAND_NAMES: frozenset[ControlCommandName] = frozenset(
    (*_NON_DIRECT_COMMAND_NAMES, *CONTROL_DIRECT_PATH_COMMANDS.values())
)
CONTROL_BINARY_COMMANDS: frozenset[ControlCommandName] = frozenset(
    contract.direct_command
    for contract in EVCS_CONTROL_PATH_CONTRACTS
    if contract.direct_command is not None and contract.value_kind == "binary"
)


def _auto_runtime_paths(value_kind: ControlValueKind) -> frozenset[str]:
    return frozenset(
        contract.path
        for contract in EVCS_CONTROL_PATH_CONTRACTS
        if contract.direct_command is None
        and contract.path.startswith("/Auto/")
        and contract.value_kind == value_kind
    )


CONTROL_FLOAT_AUTO_RUNTIME_PATHS = _auto_runtime_paths("float")
CONTROL_STRING_AUTO_RUNTIME_PATHS = _auto_runtime_paths("string")
CONTROL_BINARY_AUTO_RUNTIME_PATHS = _auto_runtime_paths("binary")
CONTROL_INTEGER_AUTO_RUNTIME_PATHS = _auto_runtime_paths("integer")
_AUTO_RUNTIME_PATH_GROUPS: tuple[tuple[ControlValueKind, frozenset[str]], ...] = (
    ("float", CONTROL_FLOAT_AUTO_RUNTIME_PATHS),
    ("string", CONTROL_STRING_AUTO_RUNTIME_PATHS),
    ("binary", CONTROL_BINARY_AUTO_RUNTIME_PATHS),
    ("integer", CONTROL_INTEGER_AUTO_RUNTIME_PATHS),
)
CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_PATH: Mapping[str, ControlValueKind] = MappingProxyType(
    {
        path: value_kind
        for value_kind, paths in _AUTO_RUNTIME_PATH_GROUPS
        for path in paths
    }
)

CONTROL_API_STATE_ENDPOINTS = frozenset(
    {
        "/v1/state/automation",
        "/v1/state/build",
        "/v1/state/config-effective",
        "/v1/state/contracts",
        "/v1/state/dbus-diagnostics",
        "/v1/state/health",
        "/v1/state/healthz",
        "/v1/state/operational",
        "/v1/state/runtime",
        "/v1/state/summary",
        "/v1/state/topology",
        "/v1/state/update",
        "/v1/state/version",
        "/v1/state/victron-bias-recommendation",
    }
)


__all__ = [
    "CONTROL_API_STATE_ENDPOINTS",
    "CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_PATH",
    "CONTROL_BINARY_AUTO_RUNTIME_PATHS",
    "CONTROL_BINARY_COMMANDS",
    "CONTROL_COMMAND_DEFAULT_PATHS",
    "CONTROL_COMMAND_NAMES",
    "CONTROL_DIRECT_PATH_COMMANDS",
    "CONTROL_FLOAT_AUTO_RUNTIME_PATHS",
    "CONTROL_INTEGER_AUTO_RUNTIME_PATHS",
    "CONTROL_STRING_AUTO_RUNTIME_PATHS",
    "EVCS_CONTROL_PATH_BY_PATH",
    "EVCS_CONTROL_PATH_CONTRACTS",
    "EVCS_CONTACTOR_AGE_PATHS",
    "EVCS_CONTACTOR_CONTROL_PATHS",
    "EVCS_PHASE_CONTROL_PATHS",
    "EVCS_PRIMARY_CONTROL_PATHS",
    "EVCS_REQUIRED_CONTROL_PATHS",
    "EVCS_SOFTWARE_UPDATE_CONTROL_PATHS",
    "EVCS_WRITABLE_PATHS",
    "EVCS_WRITE_SNAPSHOT_PATHS",
    "ControlValueKind",
    "EvcsControlPathContract",
]
