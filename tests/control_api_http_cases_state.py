# SPDX-License-Identifier: GPL-3.0-or-later
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.control_api_http_cases_common import _FakeHandler, control_api_http_service
from venus_evcharger.control import ControlCommand, ControlResult, LocalControlApiHttpServer
from venus_evcharger.control.http_api_command_contracts import optional_error_payload
from venus_evcharger.control.http_api_command_payloads import tracked_command
from venus_evcharger.control.http_api_routing import ControlApiHttpStateReader


class _ControlApiHttpStateCases:
    def test_runtime_ports_are_composed_from_the_service_boundary(self) -> None:
        rate_limiter = SimpleNamespace(
            allow_request=lambda _client_key, *, now=None: (True, 0.0),
            allow_command=lambda _client_key, _command_name, *, now=None: (True, 0.0),
        )
        idempotency_store = SimpleNamespace(
            get=lambda _key: None,
            put=lambda _key, _fingerprint, _status, _response: None,
        )
        service = control_api_http_service(
            rate_limiter=lambda: rate_limiter,
            idempotency_store=lambda: idempotency_store,
        )
        server = LocalControlApiHttpServer(service, host="localhost", port=1)

        self.assertIs(server.rate_limit._limiter, rate_limiter)
        self.assertIs(server.idempotency._store, idempotency_store)

    def test_optional_error_payload_normalizes_nested_error_mapping_only(self) -> None:
        self.assertEqual(
            optional_error_payload({"error": {"code": "bad", 7: "numeric-key", "retryable": False}}),
            {"code": "bad", "7": "numeric-key", "retryable": False},
        )
        self.assertIsNone(optional_error_payload({"error": "bad"}))
        self.assertIsNone(optional_error_payload({}))

    def test_state_payload_contract_rejects_non_mapping_results(self) -> None:
        service = control_api_http_service(health_payload=lambda: ["not", "a", "dict"])
        state_reader = ControlApiHttpStateReader(service)
        with self.assertRaisesRegex(TypeError, "must return dict, got list"):
            state_reader.payload("/v1/state/health")

    def test_capabilities_and_state_get_endpoints_return_payloads(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="state-1"),
            capabilities_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": False,
                    "command_names": ["set_mode"],
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": True},
                    "topology": {"backend_mode": "combined"},
                }
            ),
            dbus_diagnostics_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {"/Auto/State": "idle"}}
            ),
            automation_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "kind": "automation",
                    "state": {"state_token": "state-1", "command_endpoint": "/v1/control/command"},
                }
            ),
            healthz_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "healthz", "state": {"alive": True}}),
            version_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "version", "state": {"service_version": "1.2.3"}}),
            build_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "build", "state": {"firmware_version": "FW"}}),
            contracts_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "contracts", "state": {"openapi_endpoint": "/v1/openapi.json"}}),
            summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {"mode": 1}}),
            operational_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {"mode": 1}}
            ),
            victron_bias_recommendation_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "kind": "victron-bias-recommendation",
                    "state": {"recommendation_reason": "telemetry_nominal"},
                }
            ),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        capabilities_handler = _FakeHandler("/v1/capabilities")
        diagnostics_handler = _FakeHandler("/v1/state/dbus-diagnostics")
        automation_handler = _FakeHandler("/v1/state/automation")
        healthz_handler = _FakeHandler("/v1/state/healthz")
        version_handler = _FakeHandler("/v1/state/version")
        build_handler = _FakeHandler("/v1/state/build")
        contracts_handler = _FakeHandler("/v1/state/contracts")
        summary_handler = _FakeHandler("/v1/state/summary")
        runtime_handler = _FakeHandler("/v1/state/runtime")
        operational_handler = _FakeHandler("/v1/state/operational")
        recommendation_handler = _FakeHandler("/v1/state/victron-bias-recommendation")

        server.router.handle_get(capabilities_handler)
        server.router.handle_get(diagnostics_handler)
        server.router.handle_get(automation_handler)
        server.router.handle_get(healthz_handler)
        server.router.handle_get(version_handler)
        server.router.handle_get(build_handler)
        server.router.handle_get(contracts_handler)
        server.router.handle_get(summary_handler)
        server.router.handle_get(runtime_handler)
        server.router.handle_get(operational_handler)
        server.router.handle_get(recommendation_handler)

        self.assertEqual(capabilities_handler.status_code, 200)
        self.assertEqual(capabilities_handler.response_headers["ETag"], '"state-1"')
        self.assertEqual(capabilities_handler.response_headers["X-State-Token"], "state-1")
        self.assertIn("set_mode", capabilities_handler.json_payload()["command_names"])
        self.assertEqual(diagnostics_handler.status_code, 200)
        self.assertEqual(diagnostics_handler.json_payload()["kind"], "dbus-diagnostics")
        self.assertEqual(automation_handler.status_code, 200)
        self.assertEqual(automation_handler.json_payload()["kind"], "automation")
        self.assertEqual(automation_handler.json_payload()["state"]["command_endpoint"], "/v1/control/command")
        self.assertEqual(healthz_handler.status_code, 200)
        self.assertEqual(healthz_handler.json_payload()["kind"], "healthz")
        self.assertEqual(version_handler.json_payload()["kind"], "version")
        self.assertEqual(build_handler.json_payload()["kind"], "build")
        self.assertEqual(contracts_handler.json_payload()["kind"], "contracts")
        self.assertEqual(summary_handler.status_code, 200)
        self.assertEqual(summary_handler.json_payload()["kind"], "summary")
        self.assertEqual(runtime_handler.json_payload()["kind"], "runtime")
        self.assertEqual(operational_handler.json_payload()["kind"], "operational")
        self.assertEqual(recommendation_handler.json_payload()["kind"], "victron-bias-recommendation")

    def test_public_get_routes_are_exact_and_carry_state_headers(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="public-state"),
            healthz_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "healthz"}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        self.assertEqual(server.router.public_get_payload("/v1/control/health"), server.health_payload())
        self.assertEqual(server.router.public_get_payload("/v1/state/healthz"), {"ok": True, "api_version": "v1", "kind": "healthz"})
        self.assertEqual(server.router.public_get_payload("/v1/openapi.json"), server.openapi_payload())
        self.assertIsNone(server.router.public_get_payload("/v1/capabilities"))

        for path, expected_key in (
            ("/v1/control/health", "http"),
            ("/v1/state/healthz", "healthz"),
            ("/v1/openapi.json", "3.1.0"),
        ):
            handler = _FakeHandler(path)
            server.router.handle_get(handler)
            payload = handler.json_payload()
            self.assertEqual(handler.status_code, HTTPStatus.OK)
            self.assertEqual(handler.response_headers["ETag"], '"public-state"')
            self.assertEqual(handler.response_headers["X-State-Token"], "public-state")
            self.assertIn(expected_key, {str(value) for value in payload.values()})

    def test_execute_payload_preserves_existing_tracking_when_service_returns_it(self) -> None:
        command = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        executed_command, _executed_result = server.execute_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-1", "idempotency_key": "idem-1"}
        )

        self.assertEqual(executed_command.command_id, "cmd-1")
        self.assertEqual(executed_command.idempotency_key, "idem-1")

    def test_tracked_command_keeps_matching_tracking_metadata_unchanged(self) -> None:
        command = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )

        tracked = tracked_command(
            {"command_id": "cmd-1", "idempotency_key": "idem-1"},
            command,
        )

        self.assertIs(tracked, command)

    def test_execute_payload_injects_tracking_when_service_returns_untracked_command(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        executed_command, _executed_result = server.execute_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-9", "idempotency_key": "idem-9"}
        )

        self.assertEqual(executed_command.command_id, "cmd-9")
        self.assertEqual(executed_command.idempotency_key, "idem-9")

    def test_command_endpoint_rejects_stale_if_match_state_token(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            record_command_audit=MagicMock(),
            state_token=MagicMock(return_value="fresh-state"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"If-Match": '"stale-state"'},
        )

        server.router.handle_post(handler)

        payload = handler.json_payload()
        self.assertEqual(handler.status_code, 409)
        self.assertEqual(payload["error"]["code"], "conflict")
        self.assertEqual(payload["error"]["details"]["current"], "fresh-state")
        self.assertEqual(handler.response_headers["ETag"], '"fresh-state"')
        service.handle_control_command.assert_not_called()

    def test_command_endpoint_accepts_matching_if_match_state_token(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            record_command_audit=MagicMock(),
            state_token=MagicMock(return_value="match-state"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"If-Match": 'W/"match-state"'},
        )

        server.router.handle_post(handler)

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.response_headers["ETag"], '"match-state"')

    def test_health_payload_uses_configured_host_and_auth_flag_before_bind(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.5", port=9000, auth_token="token")

        payload = server.health_payload()

        self.assertEqual(payload["listen_host"], "127.0.0.5")
        self.assertEqual(payload["listen_port"], 9000)
        self.assertTrue(payload["auth_required"])

    def test_capabilities_payload_normalizes_service_result(self) -> None:
        service = control_api_http_service(
            capabilities_payload=MagicMock(
                return_value={
                    "ok": 1,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": 1,
                    "command_names": ["set_mode"],
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": 1},
                    "topology": {"backend_mode": "combined"},
                }
            ),
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="token")

        payload = server.capabilities_payload()

        self.assertTrue(payload["auth_required"])
        self.assertEqual(payload["command_names"], ["set_mode"])
        self.assertEqual(payload["topology"]["backend_mode"], "combined")

    def test_bound_host_port_falls_back_for_non_tuple_server_address(self) -> None:
        fake_server = SimpleNamespace(server_address="unix")

        host, port = LocalControlApiHttpServer.bound_host_port(fake_server)

        self.assertEqual(host, "")
        self.assertEqual(port, 0)

    def test_command_endpoint_executes_payload_through_service_hooks(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        record_audit = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            record_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name": "set_mode", "value": 1}',
        )

        server.router.handle_post(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"]["name"], "set_mode")
        self.assertTrue(payload["command"]["command_id"])
        self.assertEqual(payload["result"]["status"], "applied")
        self.assertIsNone(payload["error"])
        sent_payload = service.control_command_from_payload.call_args.args[0]
        self.assertEqual(sent_payload["name"], "set_mode")
        self.assertEqual(sent_payload["value"], 1)
        self.assertTrue(sent_payload["command_id"])
        self.assertEqual(sent_payload["idempotency_key"], "")
        self.assertEqual(service.control_command_from_payload.call_args.kwargs, {"source": "http"})
        handled_command = service.handle_control_command.call_args.args[0]
        self.assertEqual(handled_command.name, "set_mode")
        self.assertEqual(handled_command.path, "/Mode")
        self.assertEqual(handled_command.value, 1)
        self.assertEqual(handled_command.source, "http")
        self.assertTrue(handled_command.command_id)
        record_audit.assert_called_once()
        self.assertEqual(record_audit.call_args.kwargs["status_code"], 200)

    def test_handler_class_routes_get_post_and_logs_messages(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler_class = server._handler_class()
        handler = object.__new__(handler_class)

        with (
            patch.object(server.router, "handle_get") as handle_get,
            patch.object(server.router, "handle_post") as handle_post,
            patch("venus_evcharger.control.http_api.logging.debug") as debug_mock,
        ):
            handler_class.do_GET(handler)
            handler_class.do_POST(handler)
            handler_class.log_message(handler, "hello %s", "world")

        handle_get.assert_called_once_with(handler)
        handle_post.assert_called_once_with(handler)
        debug_mock.assert_called_once_with("Control API HTTP: " + "hello %s", "world")
