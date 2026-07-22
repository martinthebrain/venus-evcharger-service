# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.core.contracts import (
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_ENDPOINTS,
    CONTROL_API_ERROR_CODES,
    CONTROL_API_EVENT_KINDS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_API_TRANSPORTS,
    CONTROL_API_VERSIONS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
    CONTROL_COMMAND_STATUSES,
    normalized_control_api_capabilities_fields,
    normalized_control_api_command_response_fields,
    normalized_control_api_error_code,
    normalized_control_api_error_fields,
    normalized_control_api_event_fields,
    normalized_control_api_event_kind,
    normalized_control_api_health_fields,
    normalized_control_api_transport,
    normalized_control_api_version,
    normalized_control_command_fields,
    normalized_control_command_name,
    normalized_control_command_source,
    normalized_control_command_status,
    normalized_control_result_fields,
)


class TestVenusEvchargerControlContracts(unittest.TestCase):
    def test_control_contract_constants_and_basic_normalizers_are_stable(self) -> None:
        self.assertEqual(CONTROL_API_VERSIONS, frozenset({"v1"}))
        self.assertEqual(CONTROL_API_TRANSPORTS, frozenset({"http"}))
        self.assertIn("/v1/capabilities", CONTROL_API_ENDPOINTS)
        self.assertIn("/v1/events", CONTROL_API_ENDPOINTS)
        self.assertIn("/v1/openapi.json", CONTROL_API_ENDPOINTS)
        self.assertIn("/v1/state/automation", CONTROL_API_STATE_ENDPOINTS)
        self.assertIn("/v1/state/healthz", CONTROL_API_STATE_ENDPOINTS)
        self.assertIn("/v1/state/version", CONTROL_API_STATE_ENDPOINTS)
        self.assertIn("/v1/state/summary", CONTROL_API_STATE_ENDPOINTS)
        self.assertIn("/v1/state/topology", CONTROL_API_STATE_ENDPOINTS)
        self.assertIn("/v1/state/victron-bias-recommendation", CONTROL_API_STATE_ENDPOINTS)
        self.assertEqual(CONTROL_API_EXPERIMENTAL_ENDPOINTS, frozenset({"/v1/events"}))
        self.assertIn("/v1/capabilities", CONTROL_API_STABLE_ENDPOINTS)
        self.assertIn("unauthorized", CONTROL_API_ERROR_CODES)
        self.assertIn("insufficient_scope", CONTROL_API_ERROR_CODES)
        self.assertIn("validation_error", CONTROL_API_ERROR_CODES)
        self.assertIn("rate_limited", CONTROL_API_ERROR_CODES)
        self.assertEqual(CONTROL_API_EVENT_KINDS, frozenset({"snapshot", "command", "state", "heartbeat"}))
        self.assertIn("set_mode", CONTROL_COMMAND_NAMES)
        self.assertEqual(
            CONTROL_COMMAND_SOURCES,
            frozenset({"control-surface", "http", "internal", "mqtt"}),
        )
        self.assertEqual(CONTROL_COMMAND_STATUSES, frozenset({"accepted_in_flight", "applied", "rejected"}))
        self.assertEqual(normalized_control_api_version(" v1 "), "v1")
        self.assertEqual(normalized_control_api_version("v2"), "v1")
        self.assertEqual(normalized_control_api_transport(" HTTP "), "http")
        self.assertEqual(normalized_control_api_transport("mqtt"), "http")
        self.assertEqual(normalized_control_api_error_code(" Invalid-JSON "), "invalid_json")
        self.assertEqual(normalized_control_api_error_code("boom"), "bad_request")
        self.assertEqual(normalized_control_command_name(" set_mode "), "set_mode")
        with self.assertRaisesRegex(ValueError, "Unsupported control command 'unknown'"):
            normalized_control_command_name("unknown")
        self.assertEqual(normalized_control_command_source(" MQTT ", default="control-surface"), "mqtt")
        self.assertEqual(normalized_control_command_source("unknown", default="control-surface"), "control-surface")
        self.assertEqual(normalized_control_command_status(" applied "), "applied")
        self.assertEqual(normalized_control_command_status("ignored"), "rejected")

    def test_control_command_fields_normalize_shape_and_preserve_value(self) -> None:
        payload = normalized_control_command_fields(
            {
                "name": " set_mode ",
                "target": " mode ",
                "value": 1,
                "source": " HTTP ",
                "detail": " ok ",
                "command_id": " cmd-1 ",
                "idempotency_key": " idem-1 ",
            }
        )

        self.assertEqual(payload["name"], "set_mode")
        self.assertEqual(payload["target"], "mode")
        self.assertEqual(payload["value"], 1)
        self.assertEqual(payload["source"], "http")
        self.assertEqual(payload["detail"], "ok")
        self.assertEqual(payload["command_id"], "cmd-1")
        self.assertEqual(payload["idempotency_key"], "idem-1")

        with self.assertRaisesRegex(ValueError, "Unsupported control command ''"):
            normalized_control_command_fields({"value": 12.5}, default_source="mqtt")

    def test_control_result_fields_enforce_one_consistent_status_shape(self) -> None:
        applied = normalized_control_result_fields(
            {
                "command": {"name": "set_mode", "target": "mode", "value": 1},
                "status": "applied",
                "accepted": 0,
                "applied": 0,
                "persisted": 0,
                "reversible_failure": 1,
                "external_side_effect_started": 1,
                "detail": " done ",
            }
        )
        self.assertTrue(applied["accepted"])
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["persisted"])
        self.assertFalse(applied["reversible_failure"])
        self.assertTrue(applied["external_side_effect_started"])
        self.assertEqual(applied["detail"], "done")

        in_flight = normalized_control_result_fields(
            {
                "command": {"name": "set_current_setting", "target": "set_current", "value": 12.5},
                "status": "accepted_in_flight",
            }
        )
        self.assertTrue(in_flight["accepted"])
        self.assertFalse(in_flight["applied"])
        self.assertFalse(in_flight["persisted"])
        self.assertFalse(in_flight["reversible_failure"])
        self.assertTrue(in_flight["external_side_effect_started"])

        rejected = normalized_control_result_fields(
            {
                "command": {"name": "set_phase_selection", "target": "phase_selection", "value": "P1_P2_P3"},
                "status": "rejected",
                "accepted": 1,
                "external_side_effect_started": 1,
            }
        )
        self.assertFalse(rejected["accepted"])
        self.assertFalse(rejected["applied"])
        self.assertFalse(rejected["persisted"])
        self.assertTrue(rejected["reversible_failure"])
        self.assertFalse(rejected["external_side_effect_started"])

    def test_control_api_health_and_command_response_contracts_normalize_payloads(self) -> None:
        health = normalized_control_api_health_fields(
            {
                "ok": True,
                "api_version": " v1 ",
                "transport": "HTTP",
                "listen_host": " 127.0.0.1 ",
                "listen_port": "8765",
                "auth_required": 1,
                "read_auth_required": 1,
                "control_auth_required": 1,
                "localhost_only": 1,
                "unix_socket_path": " /tmp/control.sock ",
            }
        )
        self.assertTrue(health["ok"])
        self.assertEqual(health["api_version"], "v1")
        self.assertEqual(health["transport"], "http")
        self.assertEqual(health["listen_host"], "127.0.0.1")
        self.assertEqual(health["listen_port"], 8765)
        self.assertTrue(health["auth_required"])
        self.assertTrue(health["read_auth_required"])
        self.assertTrue(health["control_auth_required"])
        self.assertTrue(health["localhost_only"])
        self.assertEqual(health["unix_socket_path"], "/tmp/control.sock")

        error = normalized_control_api_error_fields(
            {
                "code": " unauthorized ",
                "message": " token missing ",
                "retryable": 0,
                "details": {"header": "Authorization"},
            }
        )
        self.assertEqual(error["code"], "unauthorized")
        self.assertEqual(error["message"], "token missing")
        self.assertFalse(error["retryable"])
        self.assertEqual(error["details"]["header"], "Authorization")

        response = normalized_control_api_command_response_fields(
            {
                "ok": 0,
                "detail": "  no  ",
                "replayed": 1,
                "command": {"name": "set_mode", "target": "mode", "value": 1},
                "result": {
                    "command": {"name": "set_mode", "target": "mode", "value": 1},
                    "status": "applied",
                },
                "error": {"code": "conflict", "message": " no "},
            }
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["detail"], "no")
        self.assertTrue(response["replayed"])
        self.assertEqual(response["command"]["name"], "set_mode")
        self.assertEqual(response["result"]["status"], "applied")
        self.assertEqual(response["error"]["code"], "conflict")

        empty = normalized_control_api_command_response_fields({"detail": "bad payload"})
        self.assertFalse(empty["ok"])
        self.assertEqual(empty["detail"], "bad payload")
        self.assertFalse(empty["replayed"])
        self.assertIsNone(empty["command"])
        self.assertIsNone(empty["result"])
        self.assertIsNone(empty["error"])

    def test_control_api_capabilities_contract_normalizes_lists_and_flags(self) -> None:
        payload = normalized_control_api_capabilities_fields(
            {
                "ok": 1,
                "api_version": " v1 ",
                "transport": " HTTP ",
                "auth_required": 1,
                "read_auth_required": 1,
                "control_auth_required": 1,
                "localhost_only": 1,
                "unix_socket_path": " /tmp/control.sock ",
                "auth_header": " Authorization: Bearer <token> ",
                "auth_scopes": ["control_admin", "read", "bogus"],
                "command_names": ["set_mode", "bogus"],
                "command_scope_requirements": {"set_mode": "control_basic", "trigger_software_update": "update_admin", "bogus": "nope"},
                "command_sources": ["HTTP", "mqtt", "bogus"],
                "state_endpoints": ["/v1/state/runtime", "/bad"],
                "endpoints": ["/v1/control/health", "/v1/capabilities", "/v1/events", "/bad"],
                "available_modes": [2, 0, 1],
                "supported_phase_selections": ["P1_P2", "P1", ""],
                "features": {"phase_selection_write": 1, "software_update_trigger": 0},
                "topology": {"backend_mode": " split ", "charger_backend": " goe_charger "},
                "versioning": {
                    "stable_endpoints": ["/v1/capabilities", "/v1/control/health"],
                    "experimental_endpoints": ["/v1/events"],
                    "breaking_change_policy": " stable requires bump ",
                },
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["transport"], "http")
        self.assertTrue(payload["auth_required"])
        self.assertTrue(payload["read_auth_required"])
        self.assertTrue(payload["control_auth_required"])
        self.assertTrue(payload["localhost_only"])
        self.assertEqual(payload["unix_socket_path"], "/tmp/control.sock")
        self.assertEqual(payload["auth_header"], "Authorization: Bearer <token>")
        self.assertEqual(payload["auth_scopes"], ["control_admin", "read"])
        self.assertEqual(payload["command_names"], ["set_mode"])
        self.assertEqual(
            payload["command_scope_requirements"],
            {
                "set_mode": "control_basic",
                "trigger_software_update": "update_admin",
            },
        )
        self.assertEqual(payload["command_sources"], ["http", "mqtt"])
        self.assertEqual(payload["state_endpoints"], ["/v1/state/runtime"])
        self.assertEqual(payload["endpoints"], ["/v1/capabilities", "/v1/control/health", "/v1/events"])
        self.assertEqual(payload["available_modes"], [0, 1, 2])
        self.assertEqual(payload["supported_phase_selections"], ["P1", "P1_P2"])
        self.assertTrue(payload["features"]["phase_selection_write"])
        self.assertFalse(payload["features"]["software_update_trigger"])
        self.assertEqual(payload["topology"]["backend_mode"], "split")
        self.assertEqual(payload["topology"]["charger_backend"], "goe_charger")
        self.assertEqual(payload["versioning"]["experimental_endpoints"], ["/v1/events"])
        self.assertEqual(payload["versioning"]["breaking_change_policy"], "stable requires bump")

    def test_control_api_capabilities_contract_falls_back_for_non_iterables_and_non_mappings(self) -> None:
        payload = normalized_control_api_capabilities_fields(
            {
                "command_names": None,
                "command_sources": None,
                "state_endpoints": None,
                "endpoints": None,
                "available_modes": None,
                "supported_phase_selections": None,
                "features": "not-a-mapping",
                "topology": "not-a-mapping",
                "versioning": "not-a-mapping",
            }
        )

        self.assertIn("set_mode", payload["command_names"])
        self.assertIn("http", payload["command_sources"])
        self.assertIn("/v1/state/summary", payload["state_endpoints"])
        self.assertIn("/v1/control/command", payload["endpoints"])
        self.assertEqual(payload["available_modes"], [0, 1, 2])
        self.assertEqual(payload["supported_phase_selections"], ["P1"])
        self.assertEqual(payload["features"], {})
        self.assertEqual(payload["topology"], {})
        self.assertIn("/v1/events", payload["versioning"]["experimental_endpoints"])

    def test_control_api_event_contract_normalizes_payload_and_kind(self) -> None:
        payload = normalized_control_api_event_fields(
            {
                "seq": "5",
                "api_version": " v1 ",
                "kind": " heartbeat ",
                "timestamp": "12.5",
                "resume_token": "5",
                "payload": {123: "ok"},
            }
        )

        self.assertEqual(normalized_control_api_event_kind(" snapshot "), "snapshot")
        self.assertEqual(normalized_control_api_event_kind(" heartbeat "), "heartbeat")
        self.assertEqual(normalized_control_api_event_kind("other"), "state")
        self.assertEqual(payload["seq"], 5)
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["kind"], "heartbeat")
        self.assertEqual(payload["timestamp"], 12.5)
        self.assertEqual(payload["resume_token"], "5")
        self.assertEqual(payload["payload"], {"123": "ok"})
        self.assertEqual(normalized_control_api_event_fields({"payload": "bad"})["payload"], {})
