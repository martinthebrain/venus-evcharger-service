# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for endpoint-specific State API envelopes."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.core import contracts_state_shared
from venus_evcharger.core.contracts_state_endpoints import (
    _normalized_state_health_mapping,
    _normalized_state_update_mapping,
    normalized_state_api_config_effective_fields,
    normalized_state_api_dbus_diagnostics_fields,
    normalized_state_api_health_fields,
    normalized_state_api_topology_fields,
    normalized_state_api_update_fields,
)


class TestCoreContractsStateEndpointsContracts(unittest.TestCase):
    def test_dbus_diagnostics_empty_and_explicit_envelopes(self) -> None:
        self.assertEqual(
            normalized_state_api_dbus_diagnostics_fields(None),
            {"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}},
        )
        with patch.object(contracts_state_shared, "STATE_API_VERSIONS", frozenset({"v1", "v2"})):
            payload = normalized_state_api_dbus_diagnostics_fields(
                {"ok": 0, "api_version": "v2", "kind": "health", "state": {1: "value"}}
            )
        self.assertEqual(
            payload,
            {"ok": False, "api_version": "v2", "kind": "health", "state": {"1": "value"}},
        )

    def test_generic_endpoint_defaults_are_exact(self) -> None:
        self.assertEqual(normalized_state_api_topology_fields(None)["kind"], "topology")
        self.assertEqual(normalized_state_api_update_fields(None)["kind"], "update")
        self.assertEqual(normalized_state_api_config_effective_fields(None)["kind"], "config-effective")
        self.assertEqual(normalized_state_api_health_fields(None)["kind"], "health")

    def test_update_mapping_normalizes_every_typed_field(self) -> None:
        state = {
            "last_check_at": "1.5",
            "last_run_at": "2.5",
            "next_check_at": "3.5",
            "boot_auto_due_at": "4.5",
            "run_requested_at": "5.5",
            "available": 2,
            "no_update_active": 0,
            "detail": "unchanged",
        }
        normalized = _normalized_state_update_mapping(state)
        self.assertEqual(
            normalized,
            {
                "last_check_at": 1.5,
                "last_run_at": 2.5,
                "next_check_at": 3.5,
                "boot_auto_due_at": 4.5,
                "run_requested_at": 5.5,
                "available": True,
                "no_update_active": False,
                "detail": "unchanged",
            },
        )
        self.assertIs(normalized["available"], True)
        self.assertIs(normalized["no_update_active"], False)
        envelope = normalized_state_api_update_fields({"state": state})
        self.assertEqual(envelope["kind"], "update")
        self.assertEqual(envelope["state"], _normalized_state_update_mapping(state))

    def test_health_mapping_normalizes_every_typed_field(self) -> None:
        state = {
            "health_code": "3",
            "listen_port": "8765",
            "fault_active": 1,
            "runtime_overrides_active": 0,
            "control_api_enabled": 2,
            "control_api_running": -1,
            "control_api_localhost_only": 1,
            "update_stale": 0,
            "detail": "unchanged",
        }
        expected = {
            "health_code": 3,
            "listen_port": 8765,
            "fault_active": True,
            "runtime_overrides_active": False,
            "control_api_enabled": True,
            "control_api_running": False,
            "control_api_localhost_only": True,
            "update_stale": False,
            "detail": "unchanged",
        }
        normalized = _normalized_state_health_mapping(state)
        self.assertEqual(normalized, expected)
        for key, expected_flag in (
            ("fault_active", True),
            ("runtime_overrides_active", False),
            ("control_api_enabled", True),
            ("control_api_running", False),
            ("control_api_localhost_only", True),
            ("update_stale", False),
        ):
            self.assertIs(normalized[key], expected_flag)
        envelope = normalized_state_api_health_fields({"state": state})
        self.assertEqual(envelope["kind"], "health")
        self.assertEqual(envelope["state"], expected)


if __name__ == "__main__":
    unittest.main()
