# SPDX-License-Identifier: GPL-3.0-or-later
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from tests.control_api_http_cases_common import _FakeHandler, control_api_http_service
from venus_evcharger.control import (
    ControlApiIdempotencyStore,
    ControlCommand,
    ControlResult,
    LocalControlApiHttpServer,
)


class _ControlApiHttpAuthEventsCases:
    def test_command_endpoint_enforces_bearer_token_when_configured(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name": "set_mode", "value": 1}',
        )

        server.router.handle_post(handler)

        self.assertEqual(handler.status_code, 401)
        self.assertEqual(handler.rfile.tell(), 0)
        service.control_command_from_payload.assert_not_called()

    def test_state_and_capabilities_endpoints_enforce_bearer_token_but_health_and_openapi_stay_open(self) -> None:
        service = control_api_http_service(
            capabilities_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": True,
                    "command_names": ["set_mode"],
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": True},
                    "topology": {"backend_mode": "combined"},
                }
            ),
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            dbus_diagnostics_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}}
            ),
            summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {}}),
            operational_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")

        capabilities_handler = _FakeHandler("/v1/capabilities")
        state_handler = _FakeHandler("/v1/state/summary")
        health_handler = _FakeHandler("/v1/control/health")
        openapi_handler = _FakeHandler("/v1/openapi.json")

        server.router.handle_get(capabilities_handler)
        server.router.handle_get(state_handler)
        server.router.handle_get(health_handler)
        server.router.handle_get(openapi_handler)

        self.assertEqual(capabilities_handler.status_code, 401)
        self.assertEqual(state_handler.status_code, 401)
        self.assertEqual(health_handler.status_code, 200)
        self.assertEqual(openapi_handler.status_code, 200)
        self.assertEqual(state_handler.json_payload()["error"]["code"], "unauthorized")

    def test_events_endpoint_enforces_bearer_token_when_configured(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler("/v1/events")

        server.router.handle_get(handler)

        self.assertEqual(handler.status_code, 401)
        self.assertEqual(handler.json_payload()["error"]["code"], "unauthorized")

    def test_additional_state_endpoints_return_payloads(self) -> None:
        service = control_api_http_service(
            capabilities_payload=MagicMock(),
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            config_effective_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "config-effective", "state": {"host": "charger.local"}}
            ),
            automation_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "automation", "state": {"command_endpoint": "/v1/control/command"}}
            ),
            dbus_diagnostics_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}}),
            health_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "health", "state": {"health_code": 0}}),
            operational_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {}}),
            runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {}}),
            summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            topology_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "topology", "state": {"backend_mode": "split"}}),
            update_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "update", "state": {"available": False}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        handlers = {
            "automation": _FakeHandler("/v1/state/automation"),
            "config": _FakeHandler("/v1/state/config-effective"),
            "health": _FakeHandler("/v1/state/health"),
            "topology": _FakeHandler("/v1/state/topology"),
            "update": _FakeHandler("/v1/state/update"),
        }
        for handler in handlers.values():
            server.router.handle_get(handler)

        self.assertEqual(handlers["config"].json_payload()["kind"], "config-effective")
        self.assertEqual(handlers["automation"].json_payload()["state"]["command_endpoint"], "/v1/control/command")
        self.assertEqual(handlers["health"].json_payload()["kind"], "health")
        self.assertEqual(handlers["topology"].json_payload()["state"]["backend_mode"], "split")
        self.assertEqual(handlers["update"].json_payload()["kind"], "update")

    def test_read_token_allows_gets_but_not_control_post(self) -> None:
        service = control_api_http_service(
            capabilities_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": True,
                    "read_auth_required": True,
                    "control_auth_required": True,
                    "auth_scopes": ["read", "control_basic", "control_admin", "update_admin"],
                    "command_names": ["set_mode"],
                    "command_scope_requirements": {"set_mode": "control_basic"},
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "available_modes": [0, 1, 2],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": True},
                    "topology": {"backend_mode": "combined"},
                    "versioning": {"stable_endpoints": ["/v1/capabilities"], "experimental_endpoints": ["/v1/events"]},
                }
            ),
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            config_effective_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "config-effective", "state": {}}),
            dbus_diagnostics_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}}),
            health_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "health", "state": {}}),
            operational_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {}}),
            runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {}}),
            summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            topology_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "topology", "state": {}}),
            update_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "update", "state": {}}),
        )
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            read_token="read-token",
            control_token="control-token",
        )
        read_get = _FakeHandler("/v1/state/summary", authorization="Bearer read-token")
        read_post = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":1}', authorization="Bearer read-token")

        server.router.handle_get(read_get)
        server.router.handle_post(read_post)

        self.assertEqual(read_get.status_code, 200)
        self.assertEqual(read_post.status_code, 403)
        self.assertEqual(read_post.json_payload()["error"]["code"], "insufficient_scope")

    def test_localhost_only_rejects_remote_clients(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, localhost_only=True)
        handler = _FakeHandler("/v1/control/health", client_host="192.0.2.10")

        server.router.handle_get(handler)

        self.assertEqual(handler.status_code, 403)
        self.assertEqual(handler.json_payload()["error"]["code"], "forbidden_remote_client")

    def test_control_post_rejects_remote_clients_and_locality_can_be_disabled(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        localhost_only_server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, localhost_only=True)
        remote_post = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":1}', client_host="192.0.2.10")

        localhost_only_server.router.handle_post(remote_post)

        self.assertEqual(remote_post.status_code, 403)
        self.assertIsNone(LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, localhost_only=False).authenticator.locality_error(remote_post))

    def test_idempotency_key_replays_matching_payload_and_rejects_conflicts(self) -> None:
        command = ControlCommand(name="set_mode", target="mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        record_audit = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            record_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        first = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-1"},
        )
        replay = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-1"},
        )
        conflict = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":2}',
            headers={"Idempotency-Key": "idem-1"},
        )

        server.router.handle_post(first)
        server.router.handle_post(replay)
        server.router.handle_post(conflict)

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json_payload()["replayed"])
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json_payload()["replayed"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json_payload()["error"]["code"], "idempotency_conflict")
        service.handle_control_command.assert_called_once()
        self.assertEqual(record_audit.call_count, 3)
        self.assertTrue(record_audit.call_args_list[1].kwargs["replayed"])
        self.assertEqual(record_audit.call_args_list[2].kwargs["status_code"], 409)

    def test_idempotency_key_replays_after_server_restart_when_runtime_store_is_shared(self) -> None:
        command = ControlCommand(name="set_mode", target="mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        with TemporaryDirectory() as tmpdir:
            store = ControlApiIdempotencyStore(path=f"{tmpdir}/idempotency.json")
            service = control_api_http_service(
                control_command_from_payload=MagicMock(return_value=command),
                handle_control_command=MagicMock(return_value=result),
                idempotency_store=lambda: store,
                record_command_audit=MagicMock(),
            )
            first_server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
            second_server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
            first = _FakeHandler(
                "/v1/control/command",
                body=b'{"name":"set_mode","value":1}',
                headers={"Idempotency-Key": "idem-restart"},
            )
            replay = _FakeHandler(
                "/v1/control/command",
                body=b'{"name":"set_mode","value":1}',
                headers={"Idempotency-Key": "idem-restart"},
            )

            first_server.router.handle_post(first)
            second_server.router.handle_post(replay)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json_payload()["replayed"])
        service.handle_control_command.assert_called_once()

    def test_replayed_idempotent_response_publishes_command_event_when_supported(self) -> None:
        command = ControlCommand(name="set_mode", target="mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        publish_event = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            publish_command_event=publish_event,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        first = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-2"},
        )
        replay = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-2"},
        )

        server.router.handle_post(first)
        server.router.handle_post(replay)

        publish_event.assert_called_once()
        self.assertTrue(replay.json_payload()["replayed"])

    def test_events_endpoint_streams_snapshot_and_recent_events(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="state-2"),
            event_bus=MagicMock(),
            event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        service.event_bus.return_value.recent.return_value = [
            {"seq": 1, "api_version": "v1", "kind": "command", "timestamp": 1.0, "payload": {"detail": "ok"}}
        ]
        service.event_bus.return_value.wait_for_next.return_value = None
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?once=1")

        server.router.handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.response_headers["X-Control-Api-Retry-Ms"], "1000")
        self.assertEqual(lines[0]["kind"], "snapshot")
        self.assertEqual(lines[0]["resume_token"], "0")
        self.assertEqual(lines[0]["payload"]["state_token"], "state-2")
        self.assertEqual(lines[1]["kind"], "command")
        self.assertEqual(lines[1]["resume_token"], "1")

    def test_events_endpoint_supports_live_follow_and_query_fallbacks(self) -> None:
        event_bus = MagicMock()
        event_bus.recent.return_value = []
        returned_event = False

        def _wait_for_next(**_kwargs: object) -> dict[str, object] | None:
            nonlocal returned_event
            if returned_event:
                return None
            returned_event = True
            return {"seq": 2, "api_version": "v1", "kind": "state", "timestamp": 2.0, "payload": {"detail": "later"}}

        event_bus.wait_for_next.side_effect = _wait_for_next
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="state-3"),
            event_bus=MagicMock(return_value=event_bus),
            event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=7&resume=9&limit=bad&timeout=bad&heartbeat=bad&once=0")

        server.router.handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(lines[0]["kind"], "state")
        self.assertEqual(lines[0]["resume_token"], "2")
        event_bus.recent.assert_called_once_with(limit=20, after_seq=9)
        event_bus.wait_for_next.assert_called()

    def test_events_endpoint_emits_heartbeat_when_live_follow_is_idle(self) -> None:
        event_bus = MagicMock()
        event_bus.recent.return_value = []
        event_bus.wait_for_next.return_value = None
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="state-4"),
            event_bus=MagicMock(return_value=event_bus),
            event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=4&timeout=0.02&heartbeat=0.01")

        server.router.handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(lines[0]["kind"], "heartbeat")
        self.assertEqual(lines[0]["resume_token"], "4")
        self.assertTrue(lines[0]["payload"]["alive"])
        self.assertEqual(lines[0]["payload"]["retry_hint_ms"], 250)
        self.assertEqual(lines[0]["payload"]["resume_hint"], "4")
