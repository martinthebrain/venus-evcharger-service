# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete health, capability, error, and event contracts for Control API v1."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.core import contracts_control
from venus_evcharger.core.contracts_control import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
    _normalized_allowed_endpoints,
    _normalized_auth_scopes,
    _normalized_available_modes,
    _normalized_bool_mapping,
    _normalized_command_names,
    _normalized_command_scope_requirements,
    _normalized_command_sources,
    _normalized_control_auth_flags,
    _normalized_control_versioning,
    _normalized_phase_selections,
    _normalized_text_mapping,
    normalized_control_api_capabilities_fields,
    normalized_control_api_command_response_fields,
    normalized_control_api_error_fields,
    normalized_control_api_event_fields,
    normalized_control_api_event_kind,
    normalized_control_api_health_fields,
)


class TestCoreContractsControlApiContracts(unittest.TestCase):
    def test_health_empty_and_full_shapes(self) -> None:
        self.assertEqual(
            normalized_control_api_health_fields(None),
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "listen_host": "",
                "listen_port": 0,
                "auth_required": False,
                "read_auth_required": False,
                "control_auth_required": False,
                "localhost_only": True,
                "unix_socket_path": "",
            },
        )

    def test_health_forwards_supported_version_and_transport(self) -> None:
        with (
            patch.object(contracts_control, "CONTROL_API_VERSIONS", frozenset({"v1", "v2"})),
            patch.object(contracts_control, "CONTROL_API_TRANSPORTS", frozenset({"http", "mqtt"})),
        ):
            payload = normalized_control_api_health_fields({"api_version": "v2", "transport": "mqtt"})
        self.assertEqual(payload["api_version"], "v2")
        self.assertEqual(payload["transport"], "mqtt")
        self.assertEqual(
            normalized_control_api_health_fields(
                {
                    "ok": 0,
                    "api_version": "invalid",
                    "transport": "mqtt",
                    "listen_host": " host ",
                    "listen_port": "8765",
                    "auth_required": 1,
                    "read_auth_required": 0,
                    "control_auth_required": 1,
                    "localhost_only": 0,
                    "unix_socket_path": " /socket ",
                }
            ),
            {
                "ok": False,
                "api_version": "v1",
                "transport": "http",
                "listen_host": "host",
                "listen_port": 8765,
                "auth_required": True,
                "read_auth_required": False,
                "control_auth_required": True,
                "localhost_only": False,
                "unix_socket_path": "/socket",
            },
        )

    def test_auth_flag_inheritance_contract(self) -> None:
        self.assertEqual(_normalized_control_auth_flags({}), (False, False))
        self.assertEqual(_normalized_control_auth_flags({"auth_required": 1}), (True, True))
        self.assertEqual(
            _normalized_control_auth_flags({"auth_required": 1, "read_auth_required": 0}),
            (False, False),
        )
        self.assertEqual(
            _normalized_control_auth_flags({"read_auth_required": 1, "control_auth_required": 0}),
            (True, False),
        )

    def test_mapping_and_list_normalizers(self) -> None:
        self.assertEqual(_normalized_bool_mapping(None), {})
        self.assertEqual(_normalized_bool_mapping({1: 2, "off": 0}), {"1": True, "off": False})
        self.assertEqual(_normalized_text_mapping(None), {})
        self.assertEqual(_normalized_text_mapping({1: " value ", "empty": ""}), {"1": "value", "empty": "na"})
        self.assertEqual(_normalized_phase_selections(None), ["P1"])
        self.assertEqual(_normalized_phase_selections([" P3 ", "P1", ""]), ["P1", "P3"])
        self.assertEqual(_normalized_command_names(None), sorted(CONTROL_COMMAND_NAMES))
        self.assertEqual(_normalized_command_names(["set_mode", "invalid"]), ["set_mode"])
        self.assertEqual(_normalized_command_sources(None), sorted(CONTROL_COMMAND_SOURCES))
        self.assertEqual(_normalized_command_sources(["MQTT", "invalid"]), ["http", "mqtt"])
        self.assertEqual(_normalized_auth_scopes(None), ["control_admin", "control_basic", "read", "update_admin"])
        self.assertEqual(_normalized_available_modes(None), [0, 1, 2])
        self.assertEqual(_normalized_available_modes([2, -1, "1"]), [0, 1, 2])

    def test_endpoint_and_scope_requirement_contracts(self) -> None:
        self.assertEqual(_normalized_allowed_endpoints(None, CONTROL_API_EXPERIMENTAL_ENDPOINTS), ["/v1/events"])
        self.assertEqual(
            _normalized_allowed_endpoints(["/v1/events", "/bad"], CONTROL_API_EXPERIMENTAL_ENDPOINTS),
            ["/v1/events"],
        )
        self.assertEqual(_normalized_allowed_endpoints(["/bad"], CONTROL_API_EXPERIMENTAL_ENDPOINTS), ["/v1/events"])
        self.assertEqual(_normalized_command_scope_requirements(None), {})
        self.assertEqual(
            _normalized_command_scope_requirements({"set_mode": "control_admin", "invalid": "invalid"}),
            {"set_mode": "control_admin"},
        )

    def test_versioning_contract(self) -> None:
        fallback = _normalized_control_versioning({})
        self.assertEqual(fallback["stable_endpoints"], sorted(CONTROL_API_STABLE_ENDPOINTS))
        self.assertEqual(fallback["experimental_endpoints"], ["/v1/events"])
        self.assertEqual(
            fallback["breaking_change_policy"],
            "Stable v1 endpoints require a version bump for breaking changes; experimental endpoints may evolve within v1.",
        )
        self.assertEqual(
            _normalized_control_versioning(
                {
                    "versioning": {
                        "stable_endpoints": ["/v1/capabilities"],
                        "experimental_endpoints": ["/v1/events"],
                        "breaking_change_policy": " policy ",
                    }
                }
            ),
            {
                "stable_endpoints": ["/v1/capabilities"],
                "experimental_endpoints": ["/v1/events"],
                "breaking_change_policy": "policy",
            },
        )
        with patch.object(
            contracts_control,
            "CONTROL_API_EXPERIMENTAL_ENDPOINTS",
            frozenset({"/v1/events", "/v1/preview"}),
        ):
            self.assertEqual(
                _normalized_control_versioning(
                    {"versioning": {"experimental_endpoints": ["/v1/preview"]}}
                )["experimental_endpoints"],
                ["/v1/preview"],
            )

    def test_empty_capabilities_shape(self) -> None:
        payload = normalized_control_api_capabilities_fields(None)
        self.assertEqual(
            set(payload),
            {
                "ok", "api_version", "transport", "auth_required", "read_auth_required",
                "control_auth_required", "localhost_only", "unix_socket_path", "auth_header",
                "auth_scopes", "command_names", "command_scope_requirements", "command_sources",
                "state_endpoints", "endpoints", "available_modes", "supported_phase_selections",
                "features", "topology", "versioning",
            },
        )
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["auth_required"], False)
        self.assertEqual(payload["read_auth_required"], False)
        self.assertEqual(payload["control_auth_required"], False)
        self.assertEqual(payload["localhost_only"], True)
        self.assertEqual(payload["unix_socket_path"], "")
        self.assertEqual(payload["auth_header"], "Authorization: Bearer <token>")
        self.assertEqual(payload["auth_scopes"], ["control_admin", "control_basic", "read", "update_admin"])
        self.assertEqual(payload["command_names"], sorted(CONTROL_COMMAND_NAMES))
        self.assertEqual(payload["command_scope_requirements"], {})
        self.assertEqual(payload["command_sources"], sorted(CONTROL_COMMAND_SOURCES))
        self.assertEqual(payload["state_endpoints"], sorted(CONTROL_API_STATE_ENDPOINTS))
        self.assertEqual(payload["endpoints"], sorted(CONTROL_API_ENDPOINTS))
        self.assertEqual(payload["available_modes"], [0, 1, 2])
        self.assertEqual(payload["supported_phase_selections"], ["P1"])
        self.assertEqual(payload["features"], {})
        self.assertEqual(payload["topology"], {})

    def test_capabilities_normalize_all_non_default_fields(self) -> None:
        with (
            patch.object(contracts_control, "CONTROL_API_VERSIONS", frozenset({"v1", "v2"})),
            patch.object(contracts_control, "CONTROL_API_TRANSPORTS", frozenset({"http", "mqtt"})),
        ):
            payload = normalized_control_api_capabilities_fields(
                {
                    "ok": 0,
                    "api_version": "v2",
                    "transport": "mqtt",
                    "read_auth_required": 1,
                    "control_auth_required": 0,
                    "localhost_only": 0,
                    "unix_socket_path": " /run/control.sock ",
                    "auth_header": " X-Token ",
                    "auth_scopes": ["control_admin"],
                    "command_names": ["set_mode"],
                    "command_scope_requirements": {"set_mode": "control_admin"},
                    "command_sources": ["control-surface"],
                    "state_endpoints": ["/v1/state/health"],
                    "endpoints": ["/v1/capabilities"],
                    "available_modes": [7],
                    "supported_phase_selections": ["P3"],
                    "features": {"events": 1},
                    "topology": {"meter": " primary "},
                }
            )
        self.assertEqual(
            payload,
            {
                "ok": False,
                "api_version": "v2",
                "transport": "mqtt",
                "auth_required": True,
                "read_auth_required": True,
                "control_auth_required": False,
                "localhost_only": False,
                "unix_socket_path": "/run/control.sock",
                "auth_header": "X-Token",
                "auth_scopes": ["control_admin"],
                "command_names": ["set_mode"],
                "command_scope_requirements": {"set_mode": "control_admin"},
                "command_sources": ["control-surface"],
                "state_endpoints": ["/v1/state/health"],
                "endpoints": ["/v1/capabilities"],
                "available_modes": [7],
                "supported_phase_selections": ["P3"],
                "features": {"events": True},
                "topology": {"meter": "primary"},
                "versioning": _normalized_control_versioning({}),
            },
        )

    def test_command_response_normalizes_present_command(self) -> None:
        payload = normalized_control_api_command_response_fields(
            {"ok": 1, "command": {"name": "set_mode", "source": "control-surface"}}
        )
        self.assertIs(payload["ok"], True)
        self.assertEqual(
            payload["command"],
            {
                "name": "set_mode",
                "target": "",
                "value": None,
                "source": "control-surface",
                "detail": "",
                "command_id": "",
                "idempotency_key": "",
            },
        )

    def test_event_forwards_supported_api_version(self) -> None:
        with patch.object(contracts_control, "CONTROL_API_VERSIONS", frozenset({"v1", "v2"})):
            payload = normalized_control_api_event_fields({"api_version": "v2"})
        self.assertEqual(payload["api_version"], "v2")

    def test_error_response_and_event_shapes(self) -> None:
        self.assertEqual(
            normalized_control_api_error_fields(None),
            {"code": "bad_request", "message": "", "retryable": False, "details": {}},
        )
        self.assertEqual(
            normalized_control_api_error_fields(
                {"code": "CONFLICT", "detail": " detail ", "retryable": 1, "details": {1: "one"}}
            ),
            {"code": "conflict", "message": "detail", "retryable": True, "details": {"1": "one"}},
        )
        self.assertEqual(
            normalized_control_api_command_response_fields(None),
            {"ok": False, "detail": "", "replayed": False, "command": None, "result": None, "error": None},
        )
        self.assertEqual(normalized_control_api_event_kind(" HEARTBEAT "), "heartbeat")
        self.assertEqual(normalized_control_api_event_kind(None), "state")
        self.assertEqual(
            normalized_control_api_event_fields(None),
            {"seq": 0, "api_version": "v1", "kind": "state", "timestamp": 0.0, "resume_token": "0", "payload": {}},
        )
        self.assertEqual(
            normalized_control_api_event_fields(
                {"seq": "7", "api_version": "bad", "kind": "command", "timestamp": "12.5", "resume_token": " token ", "payload": {1: "one"}}
            ),
            {"seq": 7, "api_version": "v1", "kind": "command", "timestamp": 12.5, "resume_token": "token", "payload": {"1": "one"}},
        )


if __name__ == "__main__":
    unittest.main()
