# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-boundary contracts for the canonical EVCS control registry."""

from __future__ import annotations

import unittest
from typing import get_args

import venus_evcharger.core.contracts_control_surface as control_surface
from venus_evcharger.control.http_api_routing import ControlApiHttpRouter
from venus_evcharger.control.models import ControlCommandName
from venus_evcharger.control.reference import (
    CONTROL_API_COMMAND_REFERENCE_BY_NAME,
    CONTROL_API_COMMAND_SCOPE_REQUIREMENTS,
)
from venus_evcharger.control.service import ControlApiV1Service
from venus_evcharger.core import contracts_control
from venus_evcharger.core.contracts_control_surface import (
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_PATH,
    CONTROL_BINARY_AUTO_RUNTIME_PATHS,
    CONTROL_BINARY_COMMANDS,
    CONTROL_COMMAND_DEFAULT_PATHS,
    CONTROL_COMMAND_NAMES,
    CONTROL_DIRECT_PATH_COMMANDS,
    CONTROL_FLOAT_AUTO_RUNTIME_PATHS,
    CONTROL_INTEGER_AUTO_RUNTIME_PATHS,
    CONTROL_STRING_AUTO_RUNTIME_PATHS,
    EVCS_CONTROL_PATH_BY_PATH,
    EVCS_CONTROL_PATH_CONTRACTS,
    EVCS_REQUIRED_CONTROL_PATHS,
    EVCS_WRITABLE_PATHS,
    EVCS_WRITE_SNAPSHOT_PATHS,
)
from venus_evcharger.dbus_gateway_surface import (
    EVCS_CONFIG_AND_DIAGNOSTIC_PATHS,
    VENUS_EV_CHARGER_REQUIRED_CONTRACTS,
    VENUS_EV_CHARGER_WRITABLE_PATHS,
)

EXPECTED_SNAPSHOT_PATHS = (
    "/Mode",
    "/AutoStart",
    "/StartStop",
    "/Enable",
    "/PhaseSelection",
    "/PhaseSelectionActive",
    "/SupportedPhaseSelections",
    "/Auto/PhaseLockoutActive",
    "/Auto/PhaseLockoutTarget",
    "/Auto/PhaseLockoutReason",
    "/Auto/PhaseSupportedConfigured",
    "/Auto/PhaseSupportedEffective",
    "/Auto/PhaseDegradedActive",
    "/Auto/PhaseLockoutReset",
    "/Auto/ContactorFaultCount",
    "/Auto/ContactorLockoutActive",
    "/Auto/ContactorLockoutReason",
    "/Auto/ContactorLockoutSource",
    "/Auto/ContactorLockoutAge",
    "/Auto/ContactorLockoutReset",
    "/SetCurrent",
    "/MinCurrent",
    "/MaxCurrent",
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

EXPECTED_DIRECT_COMMANDS = {
    "/Mode": "set_mode",
    "/AutoStart": "set_auto_start",
    "/StartStop": "set_start_stop",
    "/Enable": "set_enable",
    "/PhaseSelection": "set_phase_selection",
    "/Auto/PhaseLockoutReset": "reset_phase_lockout",
    "/Auto/ContactorLockoutReset": "reset_contactor_lockout",
    "/Auto/SoftwareUpdateRun": "trigger_software_update",
}

EXPECTED_READ_ONLY_SNAPSHOT_PATHS = frozenset(
    {
        "/PhaseSelectionActive",
        "/SupportedPhaseSelections",
        "/Auto/PhaseLockoutActive",
        "/Auto/PhaseLockoutTarget",
        "/Auto/PhaseLockoutReason",
        "/Auto/PhaseSupportedConfigured",
        "/Auto/PhaseSupportedEffective",
        "/Auto/PhaseDegradedActive",
        "/Auto/ContactorFaultCount",
        "/Auto/ContactorLockoutActive",
        "/Auto/ContactorLockoutReason",
        "/Auto/ContactorLockoutSource",
        "/Auto/ContactorLockoutAge",
    }
)

EXPECTED_STATE_ENDPOINTS = frozenset(
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


class ControlSurfaceRegistryTests(unittest.TestCase):
    def test_registry_rejects_duplicate_paths(self) -> None:
        duplicate = control_surface.EvcsControlPathContract("/Mode")
        with self.assertRaisesRegex(ValueError, "contains duplicate paths"):
            control_surface._path_index((duplicate, duplicate))

    def test_registry_has_unique_paths_and_preserves_snapshot_order(self) -> None:
        registered_paths = tuple(contract.path for contract in EVCS_CONTROL_PATH_CONTRACTS)
        self.assertEqual(len(registered_paths), len(set(registered_paths)))
        self.assertEqual(EVCS_WRITE_SNAPSHOT_PATHS, EXPECTED_SNAPSHOT_PATHS)
        self.assertEqual(EVCS_CONTROL_PATH_BY_PATH.keys(), set(registered_paths))
        self.assertEqual(len(EVCS_CONFIG_AND_DIAGNOSTIC_PATHS), 173)
        self.assertEqual(len(EVCS_CONFIG_AND_DIAGNOSTIC_PATHS), len(set(EVCS_CONFIG_AND_DIAGNOSTIC_PATHS)))
        self.assertLessEqual(set(registered_paths), set(EVCS_CONFIG_AND_DIAGNOSTIC_PATHS))

    def test_writable_and_required_gateway_views_are_derived_from_registry(self) -> None:
        expected_writable = (
            frozenset(EXPECTED_SNAPSHOT_PATHS) - EXPECTED_READ_ONLY_SNAPSHOT_PATHS
        ) | {"/Auto/SoftwareUpdateRun"}
        self.assertEqual(
            frozenset(contract.path for contract in EVCS_CONTROL_PATH_CONTRACTS if contract.writable),
            expected_writable,
        )
        self.assertEqual(EVCS_WRITABLE_PATHS, expected_writable)
        self.assertIs(VENUS_EV_CHARGER_WRITABLE_PATHS, EVCS_WRITABLE_PATHS)
        self.assertEqual(EVCS_REQUIRED_CONTROL_PATHS, ("/Mode", "/StartStop", "/Enable", "/SetCurrent", "/AutoStart"))
        required_control_paths = tuple(
            contract.path
            for contract in VENUS_EV_CHARGER_REQUIRED_CONTRACTS
            if contract.role == "control"
        )
        self.assertEqual(required_control_paths, EVCS_REQUIRED_CONTROL_PATHS)

    def test_command_names_paths_and_binary_semantics_share_one_contract(self) -> None:
        self.assertEqual(CONTROL_COMMAND_NAMES, frozenset(get_args(ControlCommandName)))
        self.assertEqual(CONTROL_DIRECT_PATH_COMMANDS, EXPECTED_DIRECT_COMMANDS)
        self.assertEqual(
            CONTROL_COMMAND_DEFAULT_PATHS,
            {command_name: path for path, command_name in EXPECTED_DIRECT_COMMANDS.items()},
        )
        self.assertEqual(ControlApiV1Service._COMMAND_NAMES, CONTROL_COMMAND_NAMES)
        self.assertEqual(ControlApiV1Service._DIRECT_PATH_COMMANDS, CONTROL_DIRECT_PATH_COMMANDS)
        self.assertEqual(ControlApiV1Service._COMMAND_DEFAULT_PATHS, CONTROL_COMMAND_DEFAULT_PATHS)
        self.assertEqual(ControlApiV1Service._BINARY_COMMANDS, CONTROL_BINARY_COMMANDS)
        self.assertEqual(frozenset(ControlApiV1Service._HANDLER_SPECS), CONTROL_COMMAND_NAMES)
        self.assertEqual(frozenset(CONTROL_API_COMMAND_REFERENCE_BY_NAME), CONTROL_COMMAND_NAMES)
        self.assertEqual(frozenset(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS), CONTROL_COMMAND_NAMES)

    def test_auto_runtime_groups_match_registry_and_write_controller(self) -> None:
        grouped_paths = (
            CONTROL_FLOAT_AUTO_RUNTIME_PATHS
            | CONTROL_STRING_AUTO_RUNTIME_PATHS
            | CONTROL_BINARY_AUTO_RUNTIME_PATHS
            | CONTROL_INTEGER_AUTO_RUNTIME_PATHS
        )
        self.assertEqual(grouped_paths, frozenset(CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_PATH))
        self.assertEqual(ControlApiV1Service._FLOAT_AUTO_RUNTIME_PATHS, CONTROL_FLOAT_AUTO_RUNTIME_PATHS)
        self.assertEqual(ControlApiV1Service._STRING_AUTO_RUNTIME_PATHS, CONTROL_STRING_AUTO_RUNTIME_PATHS)
        self.assertEqual(ControlApiV1Service._BINARY_AUTO_RUNTIME_PATHS, CONTROL_BINARY_AUTO_RUNTIME_PATHS)
        self.assertEqual(ControlApiV1Service._INTEGER_AUTO_RUNTIME_PATHS, CONTROL_INTEGER_AUTO_RUNTIME_PATHS)

    def test_http_and_payload_contracts_use_the_canonical_api_endpoints_and_commands(self) -> None:
        self.assertEqual(CONTROL_API_STATE_ENDPOINTS, EXPECTED_STATE_ENDPOINTS)
        self.assertIs(ControlApiHttpRouter.STATE_GET_ENDPOINTS, CONTROL_API_STATE_ENDPOINTS)
        self.assertIs(contracts_control.CONTROL_API_STATE_ENDPOINTS, CONTROL_API_STATE_ENDPOINTS)
        self.assertIs(contracts_control.CONTROL_COMMAND_NAMES, CONTROL_COMMAND_NAMES)


if __name__ == "__main__":
    unittest.main()
