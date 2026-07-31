# SPDX-License-Identifier: GPL-3.0-or-later
from tests.control_api_http_cases_tail_support import *  # noqa: F401,F403

class __ControlApiHttpTailCasesPart1:
    def test_json_extra_headers_are_allowlisted_and_crlf_sanitized(self) -> None:
        handler = _FakeHandler("/v1/state/summary")

        ControlApiHttpResponder().write_json(
            handler,
            HTTPStatus.OK,
            {"ok": True},
            extra_headers={
                "Content-Type": "text/plain",
                "ETag": '"state\r\nInjected: bad"',
                "Retry-After": "5\n6",
                "X-State-Token": "token\rvalue",
                "X-Bad\r\nHeader": "evil",
            },
        )

        self.assertEqual(handler.response_headers["Content-Type"], "application/json")
        self.assertEqual(handler.response_headers["ETag"], '"stateInjected: bad"')
        self.assertEqual(handler.response_headers["Retry-After"], "56")
        self.assertEqual(handler.response_headers["X-State-Token"], "tokenvalue")
        self.assertNotIn("X-Bad\r\nHeader", handler.response_headers)
        for key, value in handler.response_headers.items():
            self.assertNotIn("\r", key + value)
            self.assertNotIn("\n", key + value)

    def test_write_json_uses_deterministic_sorted_utf8_bytes(self) -> None:
        handler = _FakeHandler("/v1/state/summary")

        ControlApiHttpResponder().write_json(handler, HTTPStatus.OK, {"z": 1, "a": 2})

        self.assertEqual(handler.wfile.getvalue(), b'{"a": 2, "z": 1}')
        self.assertEqual(handler.response_headers["Content-Length"], "16")

    def test_response_helpers_emit_exact_json_contract(self) -> None:
        command = ControlCommand(
            name="set_mode",
            target="mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.applied_result(command)
        error_payload = ControlApiHttpResponder.error_payload("invalid_payload", "JSON body must be an object.")
        command_payload = ControlApiHttpResponder.command_payload(command)
        result_payload = ControlApiHttpResponder.result_payload(result)
        handler = _FakeHandler("/v1/control/command")

        ControlApiHttpResponder().write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_payload", "JSON body must be an object.")

        self.assertEqual(
            error_payload,
            {
                "ok": False,
                "detail": "JSON body must be an object.",
                "command": None,
                "result": None,
                "replayed": False,
                "error": {
                    "code": "invalid_payload",
                    "message": "JSON body must be an object.",
                    "retryable": False,
                    "details": {},
                },
            },
        )
        self.assertEqual(command_payload["name"], "set_mode")
        self.assertEqual(command_payload["target"], "mode")
        self.assertEqual(command_payload["value"], 1)
        self.assertEqual(command_payload["source"], "http")
        self.assertEqual(command_payload["command_id"], "cmd-1")
        self.assertEqual(command_payload["idempotency_key"], "idem-1")
        self.assertEqual(result_payload["status"], "applied")
        self.assertTrue(result_payload["accepted"])
        self.assertTrue(result_payload["applied"])
        self.assertFalse(result_payload["reversible_failure"])
        self.assertEqual(handler.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(handler.response_headers["Content-Type"], "application/json")
        self.assertEqual(handler.response_headers["Content-Length"], str(len(handler.wfile.getvalue())))
        self.assertEqual(handler.json_payload(), error_payload)

    def test_events_endpoint_filters_recent_events_by_kind(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="state-5"),
            event_bus=MagicMock(),
            event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        service.event_bus.return_value.recent.return_value = [
            {"seq": 1, "api_version": "v1", "kind": "command", "timestamp": 1.0, "payload": {"detail": "cmd"}},
            {"seq": 2, "api_version": "v1", "kind": "state", "timestamp": 2.0, "payload": {"detail": "state"}},
        ]
        service.event_bus.return_value.wait_for_next.return_value = None
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?once=1&kind=command")

        server.router.handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["kind"], "command")

    def test_events_endpoint_waits_past_unmatched_events_until_matching_kind_arrives(self) -> None:
        event_bus = MagicMock()
        event_bus.recent.return_value = []
        event_bus.wait_for_next.side_effect = [
            {"seq": 3, "api_version": "v1", "kind": "state", "timestamp": 3.0, "payload": {"detail": "skip"}},
            {"seq": 4, "api_version": "v1", "kind": "command", "timestamp": 4.0, "payload": {"detail": "keep"}},
            None,
        ]
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            state_token=MagicMock(return_value="state-6"),
            event_bus=MagicMock(return_value=event_bus),
            event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=2&timeout=0.02&kind=command&heartbeat=0")

        server.router.handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(lines[0]["kind"], "command")
        self.assertEqual(lines[0]["resume_token"], "4")

    def test_write_live_events_returns_immediately_for_zero_timeout(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events")
        event_bus = MagicMock()

        server.events.write_live_events(
            handler,
            event_bus,
            after_seq=0,
            timeout=0.0,
            heartbeat_interval=1.0,
            event_kinds=frozenset(),
            retry_ms=1000,
        )

        event_bus.wait_for_next.assert_not_called()

    def test_write_live_events_stops_without_heartbeat_and_wait_timeout_uses_remaining(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events")
        event_bus = MagicMock()
        event_bus.wait_for_next.return_value = None

        server.events.write_live_events(
            handler,
            event_bus,
            after_seq=4,
            timeout=0.02,
            heartbeat_interval=0.0,
            event_kinds=frozenset(),
            retry_ms=1000,
        )

        event_bus.wait_for_next.assert_called_once()
        self.assertEqual(handler.wfile.getvalue(), b"")
        self.assertEqual(server.events.event_wait_timeout(0.75, 0.0), 0.75)

    def test_loopback_helpers_cover_common_and_invalid_hosts(self) -> None:
        handler = SimpleNamespace(client_address="bad-client")

        self.assertEqual(ControlApiHttpAuthenticator.client_host(handler), "127.0.0.1")
        self.assertEqual(ControlApiHttpAuthenticator.client_host(SimpleNamespace()), "127.0.0.1")
        self.assertEqual(ControlApiHttpAuthenticator.client_host(SimpleNamespace(client_address=("198.51.100.5", 2345))), "198.51.100.5")
        self.assertTrue(ControlApiHttpAuthenticator.is_loopback_host("localhost"))
        self.assertTrue(ControlApiHttpAuthenticator.is_loopback_host("::1"))
        self.assertTrue(ControlApiHttpAuthenticator.is_loopback_host("127.0.0.1"))
        self.assertFalse(ControlApiHttpAuthenticator.is_loopback_host("192.0.2.10"))
        self.assertFalse(ControlApiHttpAuthenticator.is_loopback_host("not-an-ip"))

    def test_request_target_parser_keeps_blank_query_values(self) -> None:
        path, params = ControlApiHttpAuthenticator.parse_request_target("/v1/events?once=&kind=command&kind=")

        self.assertEqual(path, "/v1/events")
        self.assertEqual(params, {"once": [""], "kind": ["command", ""]})

    def test_query_helpers_fall_back_for_invalid_values(self) -> None:
        self.assertEqual(ControlApiHttpEventEndpoint.query_int({"limit": ["bad"]}, "limit", 3), 3)
        self.assertEqual(ControlApiHttpEventEndpoint.query_float({"timeout": ["bad"]}, "timeout", 1.5), 1.5)
        self.assertEqual(
            ControlApiHttpEventEndpoint.query_event_kinds({"kind": [" command , invalid ", "state"]}),
            frozenset({"command", "state"}),
        )

    def test_request_state_tokens_ignores_empty_items_and_normalizes_unquoted_tokens(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            headers={
                "If-Match": ' ,W/"etag-1",plain-token',
                "X-State-Token": ",token-2,",
            },
        )

        self.assertEqual(
            server.authenticator.request_state_tokens(handler),
            {"etag-1", "plain-token", "token-2"},
        )
        self.assertEqual(ControlApiHttpAuthenticator.normalized_token("plain-token"), "plain-token")
        self.assertEqual(ControlApiHttpAuthenticator.normalized_token('""'), "")
        self.assertEqual(ControlApiHttpAuthenticator.normalized_token('W/""'), "")

        x_state_only = _FakeHandler(
            "/v1/control/command",
            headers={
                "If-Match": "",
                "X-State-Token": "state-only",
            },
        )
        self.assertEqual(server.authenticator.request_state_tokens(x_state_only), {"state-only"})

    def test_scope_requirement_matrix_and_auth_errors_are_exact(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, read_token="read-token")

        self.assertTrue(server.authenticator.scope_satisfies_requirement("read", "read"))
        self.assertTrue(server.authenticator.scope_satisfies_requirement("control_basic", "read"))
        self.assertTrue(server.authenticator.scope_satisfies_requirement("update_admin", "control_admin"))
        self.assertFalse(server.authenticator.scope_satisfies_requirement(None, "read"))
        self.assertFalse(server.authenticator.scope_satisfies_requirement("read", "control_basic"))
        self.assertFalse(server.authenticator.scope_satisfies_requirement("unknown", "read"))
        self.assertFalse(server.authenticator.scope_satisfies_requirement("update_admin", "unknown"))

        with patch.object(server.authenticator, "authorization_scope", return_value="unknown"):
            self.assertEqual(server.authenticator.auth_error(_FakeHandler("/v1/state/summary"), required_scope="read"), UNAUTHORIZED_ERROR)
            self.assertEqual(
                server.authenticator.auth_error(_FakeHandler("/v1/control/command"), required_scope="control_basic"),
                INSUFFICIENT_SCOPE_ERROR,
            )

    def test_authorization_scope_prefers_highest_matching_token(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            read_token="read-token",
            control_token="control-token",
            admin_token="admin-token",
            update_token="update-token",
        )

        self.assertEqual(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer update-token")), "update_admin")
        self.assertEqual(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer admin-token")), "control_admin")
        self.assertEqual(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer control-token")), "control_basic")
        self.assertEqual(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer read-token")), "read")
        self.assertIsNone(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities")))
        self.assertIsNone(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities", headers={"Authorization": ""})))
        self.assertIsNone(server.authenticator.authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer wrong-token")))

        with patch("venus_evcharger.control.http_api_auth.secrets.compare_digest", return_value=True) as compare:
            self.assertTrue(server.authenticator.matches_bearer_token("Bearer supplied", "configured"))
        compare.assert_called_once_with("Bearer supplied", "Bearer configured")
        with patch("venus_evcharger.control.http_api_auth.secrets.compare_digest") as compare:
            self.assertFalse(server.authenticator.matches_bearer_token("Bearer supplied", ""))
        compare.assert_not_called()

    def test_effective_token_fallbacks_are_ordered_by_scope(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )

        self.assertEqual(ControlApiHttpAuthenticator.first_configured_token("", ""), "")
        self.assertEqual(ControlApiHttpAuthenticator.first_configured_token("", "second"), "second")

        auth_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="legacy")
        self.assertEqual(auth_only.authenticator.effective_read_token, "legacy")
        self.assertEqual(auth_only.authenticator.effective_control_token, "legacy")
        self.assertEqual(auth_only.authenticator.effective_admin_token, "legacy")
        self.assertEqual(auth_only.authenticator.effective_update_token, "legacy")

        scoped = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            auth_token="legacy",
            read_token="read",
            control_token="control",
            admin_token="admin",
            update_token="update",
        )
        self.assertEqual(scoped.authenticator.effective_read_token, "read")
        self.assertEqual(scoped.authenticator.effective_control_token, "control")
        self.assertEqual(scoped.authenticator.effective_admin_token, "admin")
        self.assertEqual(scoped.authenticator.effective_update_token, "update")

        fallback = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            control_token="control",
            admin_token="admin",
        )
        self.assertEqual(fallback.authenticator.effective_read_token, "control")
        self.assertEqual(fallback.authenticator.effective_update_token, "admin")

        admin_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, admin_token="admin")
        self.assertEqual(admin_only.authenticator.effective_read_token, "admin")
        self.assertEqual(admin_only.authenticator.effective_admin_token, "admin")
        self.assertEqual(admin_only.authenticator.effective_update_token, "admin")

        update_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, update_token="update")
        self.assertEqual(update_only.authenticator.effective_read_token, "update")
        self.assertEqual(update_only.authenticator.effective_update_token, "update")

        control_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, control_token="control")
        self.assertEqual(control_only.authenticator.effective_admin_token, "control")
        self.assertEqual(control_only.authenticator.effective_update_token, "control")

        no_token = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        self.assertFalse(no_token.authenticator.has_configured_token)
        self.assertEqual(no_token.authenticator.state_token, "")
        self.assertEqual(no_token.authenticator.state_token_headers, {})
        self.assertTrue(auth_only.authenticator.has_configured_token)

    def test_required_scope_for_command_uses_name_and_defaults_unknown_names_to_admin(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        resolved_scope = server.authenticator.required_scope_for_command({"name": "set_mode", "value": 1})
        fallback_scope = server.authenticator.required_scope_for_command({"name": "unknown", "value": 1})
        missing_name_scope = server.authenticator.required_scope_for_command({"target": "mode", "value": 1})

        self.assertEqual(resolved_scope, "control_basic")
        self.assertEqual(fallback_scope, "control_admin")
        self.assertEqual(missing_name_scope, "control_admin")
        service.control_command_from_payload.assert_not_called()

    def test_finer_scopes_gate_admin_and_update_commands(self) -> None:
        command = ControlCommand(
            name="set_auto_runtime_setting",
            target="auto_start_surplus_watts",
            value=1800.0,
            source="http",
        )
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            record_command_audit=MagicMock(),
        )
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            control_token="control-token",
            admin_token="admin-token",
            update_token="update-token",
        )
        control_handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_auto_runtime_setting","target":"auto_start_surplus_watts","value":1800.0}',
            authorization="Bearer control-token",
        )
        admin_handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"trigger_software_update","value":1}',
            authorization="Bearer admin-token",
        )
        update_handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"trigger_software_update","value":1}',
            authorization="Bearer update-token",
        )

        server.router.handle_post(control_handler)
        service.control_command_from_payload.return_value = ControlCommand(
            name="trigger_software_update",
            target="software_update_run",
            value=1,
            source="http",
        )
        server.router.handle_post(admin_handler)
        server.router.handle_post(update_handler)

        self.assertEqual(control_handler.status_code, 403)
        self.assertEqual(control_handler.json_payload()["error"]["code"], "insufficient_scope")
        self.assertEqual(admin_handler.status_code, 403)
        self.assertEqual(admin_handler.json_payload()["error"]["code"], "insufficient_scope")
        self.assertEqual(update_handler.status_code, 200)

    def test_start_and_stop_support_unix_socket_mode(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, unix_socket_path="/tmp/control.sock")
        fake_server = MagicMock()
        fake_thread = MagicMock()

        with (
            patch.object(server, "prepare_unix_socket_path") as prepare_socket,
            patch.object(server, "secure_unix_socket_path") as secure_socket,
            patch("venus_evcharger.control.http_api._ThreadingLocalControlUnixHttpServer", return_value=fake_server) as server_factory,
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=fake_thread),
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            server.start()
            self.assertEqual(server.bound_unix_socket_path, "/tmp/control.sock")
            server.stop()

        prepare_socket.assert_called_once_with("/tmp/control.sock")
        secure_socket.assert_called_once_with("/tmp/control.sock")
        server_factory.assert_called_once()
        unlink_mock.assert_called_once_with("/tmp/control.sock")

    def test_prepare_unix_socket_path_handles_missing_socket_existing_socket_and_non_socket(self) -> None:
        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=False),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            LocalControlApiHttpServer.prepare_unix_socket_path("/tmp/missing.sock")
        unlink_mock.assert_not_called()

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=0o140000)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=True),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            LocalControlApiHttpServer.prepare_unix_socket_path("/tmp/existing.sock")
        unlink_mock.assert_called_once_with("/tmp/existing.sock")

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=0o100644)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=False),
        ):
            with self.assertRaises(ValueError):
                LocalControlApiHttpServer.prepare_unix_socket_path("/tmp/not-a-socket")

    def test_get_and_post_unknown_paths_return_not_found(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        get_handler = _FakeHandler("/v1/control/unknown")
        post_handler = _FakeHandler("/v1/control/unknown", body=b"{}")

        server.router.handle_get(get_handler)
        server.router.handle_post(post_handler)

        self.assertEqual(get_handler.status_code, 404)
        self.assertEqual(post_handler.status_code, 404)
        for payload in (get_handler.json_payload(), post_handler.json_payload()):
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["detail"], "Not found.")
            self.assertFalse(payload["replayed"])
            self.assertEqual(payload["error"]["code"], "not_found")
            self.assertEqual(payload["error"]["message"], "Not found.")
            self.assertFalse(payload["error"]["retryable"])
            self.assertEqual(payload["error"]["details"], {})
            self.assertIsNone(payload["command"])
            self.assertIsNone(payload["result"])
        self.assertEqual(
            ControlApiHttpRouter.post_target_error("/v1/control/unknown"),
            (HTTPStatus.NOT_FOUND, "not_found", "Not found."),
        )
        self.assertIsNone(ControlApiHttpRouter.post_target_error("/v1/control/command"))

    def test_command_endpoint_rejects_invalid_json_payloads(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b"{invalid")

        server.router.handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "invalid_json")
        service.control_command_from_payload.assert_not_called()
