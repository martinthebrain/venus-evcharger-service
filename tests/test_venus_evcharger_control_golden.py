# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import json
import sys
import unittest
from unittest.mock import MagicMock

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.core.contracts import CONTROL_API_ERROR_CODES
from venus_evcharger.service.control import ServiceControlFacade


class _GoldenControlService:
    pass


class TestVenusEvchargerControlGolden(unittest.TestCase):
    def test_capabilities_projection_matches_golden_snapshot(self) -> None:
        service = _GoldenControlService()
        service.config = configparser.ConfigParser()
        service.config.read_string(
            """
[DEFAULT]
Host=192.0.2.20

[Backends]
Mode=split
MeterType=template_meter
SwitchType=switch_group
ChargerType=goe_charger
"""
        )
        service.control_api_read_token = "read"
        service.control_api_control_token = "control"
        service.control_api_localhost_only = True
        service.control_api_bound_unix_socket_path = "/run/venus-evcharger-control.sock"
        service.supported_phase_selections = ("P1", "P1_P2", "P1_P2_P3")
        control = ServiceControlFacade(service)
        capabilities = control.capabilities_payload()

        projection = {
            "auth_scopes": capabilities["auth_scopes"],
            "command_scope_requirements": capabilities["command_scope_requirements"],
            "features": {
                key: capabilities["features"][key]
                for key in (
                    "command_audit_trail",
                    "event_kind_filters",
                    "event_retry_hints",
                    "event_stream",
                    "optimistic_concurrency",
                    "per_command_request_schemas",
                    "rate_limiting",
                )
            },
            "topology": capabilities["topology"],
            "supported_phase_selections": capabilities["supported_phase_selections"],
        }
        golden = {
            "auth_scopes": ["control_admin", "control_basic", "read", "update_admin"],
            "command_scope_requirements": {
                "reset_contactor_lockout": "control_admin",
                "reset_phase_lockout": "control_admin",
                "set_auto_runtime_setting": "control_admin",
                "set_auto_start": "control_basic",
                "set_current_setting": "control_basic",
                "set_enable": "control_basic",
                "set_mode": "control_basic",
                "set_phase_selection": "control_basic",
                "set_start_stop": "control_basic",
                "trigger_software_update": "update_admin",
            },
            "features": {
                "command_audit_trail": True,
                "event_kind_filters": True,
                "event_retry_hints": True,
                "event_stream": True,
                "optimistic_concurrency": True,
                "per_command_request_schemas": True,
                "rate_limiting": True,
            },
            "topology": {
                "backend_mode": "split",
                "meter_backend": "template_meter",
                "switch_backend": "switch_group",
                "charger_backend": "goe_charger",
            },
            "supported_phase_selections": ["P1", "P1_P2", "P1_P2_P3"],
        }

        self.assertEqual(json.dumps(projection, sort_keys=True), json.dumps(golden, sort_keys=True))

    def test_error_code_set_matches_golden_snapshot(self) -> None:
        golden = [
            "bad_request",
            "blocked_by_health",
            "blocked_by_mode",
            "command_rejected",
            "conflict",
            "cooldown_active",
            "forbidden_remote_client",
            "idempotency_conflict",
            "insufficient_scope",
            "invalid_content_length",
            "invalid_json",
            "invalid_payload",
            "not_found",
            "rate_limited",
            "unauthorized",
            "unsupported_command",
            "unsupported_for_topology",
            "update_in_progress",
            "validation_error",
        ]
        self.assertEqual(sorted(CONTROL_API_ERROR_CODES), golden)
