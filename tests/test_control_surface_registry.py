# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-boundary contracts for the transport-neutral control registry."""

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
    CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_TARGET,
    CONTROL_BINARY_AUTO_RUNTIME_TARGETS,
    CONTROL_BINARY_COMMANDS,
    CONTROL_COMMAND_DEFAULT_TARGETS,
    CONTROL_COMMAND_NAMES,
    CONTROL_DIRECT_TARGET_COMMANDS,
    CONTROL_FLOAT_AUTO_RUNTIME_TARGETS,
    CONTROL_INTEGER_AUTO_RUNTIME_TARGETS,
    CONTROL_REQUIRED_TARGETS,
    CONTROL_STRING_AUTO_RUNTIME_TARGETS,
    CONTROL_TARGET_BY_NAME,
    CONTROL_TARGET_CONTRACTS,
    CONTROL_WRITABLE_TARGETS,
    CONTROL_WRITE_SNAPSHOT_TARGETS,
)

EXPECTED_SNAPSHOT_TARGETS = (
    "mode",
    "auto_start",
    "start_stop",
    "enable",
    "phase_selection",
    "phase_selection_active",
    "supported_phase_selections",
    "auto_phase_lockout_active",
    "auto_phase_lockout_target",
    "auto_phase_lockout_reason",
    "auto_phase_supported_configured",
    "auto_phase_supported_effective",
    "auto_phase_degraded_active",
    "auto_phase_lockout_reset",
    "auto_contactor_fault_count",
    "auto_contactor_lockout_active",
    "auto_contactor_lockout_reason",
    "auto_contactor_lockout_source",
    "auto_contactor_lockout_age",
    "auto_contactor_lockout_reset",
    "set_current",
    "min_current",
    "max_current",
    "auto_start_surplus_watts",
    "auto_stop_surplus_watts",
    "auto_min_soc",
    "auto_resume_soc",
    "auto_start_delay_seconds",
    "auto_stop_delay_seconds",
    "auto_scheduled_enabled_days",
    "auto_scheduled_fallback_delay_seconds",
    "auto_scheduled_latest_end_time",
    "auto_scheduled_night_current",
    "auto_dbus_backoff_base_seconds",
    "auto_dbus_backoff_max_seconds",
    "auto_grid_recovery_start_seconds",
    "auto_stop_surplus_delay_seconds",
    "auto_stop_surplus_volatility_low_watts",
    "auto_stop_surplus_volatility_high_watts",
    "auto_reference_charge_power_watts",
    "auto_learn_charge_power_enabled",
    "auto_learn_charge_power_min_watts",
    "auto_learn_charge_power_alpha",
    "auto_learn_charge_power_start_delay_seconds",
    "auto_learn_charge_power_window_seconds",
    "auto_learn_charge_power_max_age_seconds",
    "auto_phase_switching",
    "auto_phase_prefer_lowest_when_idle",
    "auto_phase_upshift_delay_seconds",
    "auto_phase_downshift_delay_seconds",
    "auto_phase_upshift_headroom_watts",
    "auto_phase_downshift_margin_watts",
    "auto_phase_mismatch_retry_seconds",
    "auto_phase_mismatch_lockout_count",
    "auto_phase_mismatch_lockout_seconds",
)

EXPECTED_DIRECT_COMMANDS = {
    "mode": "set_mode",
    "auto_start": "set_auto_start",
    "start_stop": "set_start_stop",
    "enable": "set_enable",
    "phase_selection": "set_phase_selection",
    "auto_phase_lockout_reset": "reset_phase_lockout",
    "auto_contactor_lockout_reset": "reset_contactor_lockout",
    "auto_software_update_run": "trigger_software_update",
}

EXPECTED_READ_ONLY_TARGETS = frozenset(
    {
        "phase_selection_active",
        "supported_phase_selections",
        "auto_phase_lockout_active",
        "auto_phase_lockout_target",
        "auto_phase_lockout_reason",
        "auto_phase_supported_configured",
        "auto_phase_supported_effective",
        "auto_phase_degraded_active",
        "auto_contactor_fault_count",
        "auto_contactor_lockout_active",
        "auto_contactor_lockout_reason",
        "auto_contactor_lockout_source",
        "auto_contactor_lockout_age",
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
    def test_registry_rejects_duplicate_targets(self) -> None:
        duplicate = control_surface.ControlTargetContract("mode")
        with self.assertRaisesRegex(ValueError, "contains duplicate targets"):
            control_surface._target_index((duplicate, duplicate))

    def test_registry_has_unique_targets_and_preserves_snapshot_order(self) -> None:
        registered_targets = tuple(contract.target for contract in CONTROL_TARGET_CONTRACTS)
        self.assertEqual(len(registered_targets), len(set(registered_targets)))
        self.assertEqual(CONTROL_WRITE_SNAPSHOT_TARGETS, EXPECTED_SNAPSHOT_TARGETS)
        self.assertEqual(CONTROL_TARGET_BY_NAME.keys(), set(registered_targets))

    def test_writable_and_required_views_are_derived_from_registry(self) -> None:
        expected_writable = (
            frozenset(EXPECTED_SNAPSHOT_TARGETS) - EXPECTED_READ_ONLY_TARGETS
        ) | {"auto_software_update_run"}
        self.assertEqual(
            frozenset(contract.target for contract in CONTROL_TARGET_CONTRACTS if contract.writable),
            expected_writable,
        )
        self.assertEqual(CONTROL_WRITABLE_TARGETS, expected_writable)
        self.assertEqual(CONTROL_REQUIRED_TARGETS, ("mode", "start_stop", "enable", "set_current", "auto_start"))

    def test_command_names_targets_and_binary_semantics_share_one_contract(self) -> None:
        self.assertEqual(CONTROL_COMMAND_NAMES, frozenset(get_args(ControlCommandName)))
        self.assertEqual(CONTROL_DIRECT_TARGET_COMMANDS, EXPECTED_DIRECT_COMMANDS)
        self.assertEqual(
            CONTROL_COMMAND_DEFAULT_TARGETS,
            {command_name: target for target, command_name in EXPECTED_DIRECT_COMMANDS.items()},
        )
        self.assertEqual(ControlApiV1Service._COMMAND_NAMES, CONTROL_COMMAND_NAMES)
        self.assertEqual(ControlApiV1Service._COMMAND_DEFAULT_TARGETS, CONTROL_COMMAND_DEFAULT_TARGETS)
        self.assertEqual(ControlApiV1Service._BINARY_COMMANDS, CONTROL_BINARY_COMMANDS)
        self.assertEqual(frozenset(ControlApiV1Service._HANDLER_SPECS), CONTROL_COMMAND_NAMES)
        self.assertEqual(frozenset(CONTROL_API_COMMAND_REFERENCE_BY_NAME), CONTROL_COMMAND_NAMES)
        self.assertEqual(frozenset(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS), CONTROL_COMMAND_NAMES)

    def test_auto_runtime_groups_match_registry_and_service(self) -> None:
        grouped_targets = (
            CONTROL_FLOAT_AUTO_RUNTIME_TARGETS
            | CONTROL_STRING_AUTO_RUNTIME_TARGETS
            | CONTROL_BINARY_AUTO_RUNTIME_TARGETS
            | CONTROL_INTEGER_AUTO_RUNTIME_TARGETS
        )
        self.assertEqual(grouped_targets, frozenset(CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_TARGET))
        self.assertEqual(ControlApiV1Service._FLOAT_AUTO_RUNTIME_TARGETS, CONTROL_FLOAT_AUTO_RUNTIME_TARGETS)
        self.assertEqual(ControlApiV1Service._STRING_AUTO_RUNTIME_TARGETS, CONTROL_STRING_AUTO_RUNTIME_TARGETS)
        self.assertEqual(ControlApiV1Service._BINARY_AUTO_RUNTIME_TARGETS, CONTROL_BINARY_AUTO_RUNTIME_TARGETS)
        self.assertEqual(ControlApiV1Service._INTEGER_AUTO_RUNTIME_TARGETS, CONTROL_INTEGER_AUTO_RUNTIME_TARGETS)

    def test_http_contracts_use_canonical_endpoints_and_commands(self) -> None:
        self.assertEqual(CONTROL_API_STATE_ENDPOINTS, EXPECTED_STATE_ENDPOINTS)
        self.assertIs(ControlApiHttpRouter.STATE_GET_ENDPOINTS, CONTROL_API_STATE_ENDPOINTS)
        self.assertIs(contracts_control.CONTROL_API_STATE_ENDPOINTS, CONTROL_API_STATE_ENDPOINTS)
        self.assertIs(contracts_control.CONTROL_COMMAND_NAMES, CONTROL_COMMAND_NAMES)


if __name__ == "__main__":
    unittest.main()
