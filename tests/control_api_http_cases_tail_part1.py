# SPDX-License-Identifier: GPL-3.0-or-later
from tests.control_api_http_cases_tail_support import *  # noqa: F401,F403

class __ControlApiHttpTailCasesPart1:
    def test_json_extra_headers_are_allowlisted_and_crlf_sanitized(self) -> None:
        handler = _FakeHandler("/v1/state/summary")

        LocalControlApiHttpServer._write_json(
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

        LocalControlApiHttpServer._write_json(handler, HTTPStatus.OK, {"z": 1, "a": 2})

        self.assertEqual(handler.wfile.getvalue(), b'{"a": 2, "z": 1}')
        self.assertEqual(handler.response_headers["Content-Length"], "16")

    def test_response_helpers_emit_exact_json_contract(self) -> None:
        command = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.applied_result(command)
        error_payload = LocalControlApiHttpServer._error_response_payload("invalid_payload", "JSON body must be an object.")
        command_payload = LocalControlApiHttpServer._command_payload(command)
        result_payload = LocalControlApiHttpServer._result_payload(result)
        handler = _FakeHandler("/v1/control/command")

        LocalControlApiHttpServer._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_payload", "JSON body must be an object.")

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
        self.assertEqual(command_payload["path"], "/Mode")
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
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-5"),
            _control_api_event_bus=MagicMock(),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        service._control_api_event_bus.return_value.recent.return_value = [
            {"seq": 1, "api_version": "v1", "kind": "command", "timestamp": 1.0, "payload": {"detail": "cmd"}},
            {"seq": 2, "api_version": "v1", "kind": "state", "timestamp": 2.0, "payload": {"detail": "state"}},
        ]
        service._control_api_event_bus.return_value.wait_for_next.return_value = None
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?once=1&kind=command")

        server._handle_get(handler)

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
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-6"),
            _control_api_event_bus=MagicMock(return_value=event_bus),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=2&timeout=0.02&kind=command&heartbeat=0")

        server._handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(lines[0]["kind"], "command")
        self.assertEqual(lines[0]["resume_token"], "4")

    def test_write_live_events_returns_immediately_for_zero_timeout(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events")
        event_bus = MagicMock()

        server._write_live_events(
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
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events")
        event_bus = MagicMock()
        event_bus.wait_for_next.return_value = None

        server._write_live_events(
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
        self.assertEqual(server._event_wait_timeout(0.75, 0.0), 0.75)

    def test_loopback_helpers_cover_common_and_invalid_hosts(self) -> None:
        handler = SimpleNamespace(client_address="bad-client")

        self.assertEqual(LocalControlApiHttpServer._client_host(handler), "127.0.0.1")
        self.assertEqual(LocalControlApiHttpServer._client_host(SimpleNamespace()), "127.0.0.1")
        self.assertEqual(LocalControlApiHttpServer._client_host(SimpleNamespace(client_address=("10.0.0.5", 2345))), "10.0.0.5")
        self.assertTrue(LocalControlApiHttpServer._is_loopback_host("localhost"))
        self.assertTrue(LocalControlApiHttpServer._is_loopback_host("::1"))
        self.assertTrue(LocalControlApiHttpServer._is_loopback_host("127.0.0.1"))
        self.assertFalse(LocalControlApiHttpServer._is_loopback_host("192.168.1.10"))
        self.assertFalse(LocalControlApiHttpServer._is_loopback_host("not-an-ip"))

    def test_request_target_parser_keeps_blank_query_values(self) -> None:
        path, params = LocalControlApiHttpServer._parsed_request_target("/v1/events?once=&kind=command&kind=")

        self.assertEqual(path, "/v1/events")
        self.assertEqual(params, {"once": [""], "kind": ["command", ""]})

    def test_query_helpers_fall_back_for_invalid_values(self) -> None:
        self.assertEqual(LocalControlApiHttpServer._query_int({"limit": ["bad"]}, "limit", 3), 3)
        self.assertEqual(LocalControlApiHttpServer._query_float({"timeout": ["bad"]}, "timeout", 1.5), 1.5)
        self.assertEqual(
            LocalControlApiHttpServer._query_event_kinds({"kind": [" command , invalid ", "state"]}),
            frozenset({"command", "state"}),
        )

    def test_request_state_tokens_ignores_empty_items_and_normalizes_unquoted_tokens(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
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
            server._request_state_tokens(handler),
            {"etag-1", "plain-token", "token-2"},
        )
        self.assertEqual(LocalControlApiHttpServer._normalized_token("plain-token"), "plain-token")
        self.assertEqual(LocalControlApiHttpServer._normalized_token('""'), "")
        self.assertEqual(LocalControlApiHttpServer._normalized_token('W/""'), "")

        x_state_only = _FakeHandler(
            "/v1/control/command",
            headers={
                "If-Match": "",
                "X-State-Token": "state-only",
            },
        )
        self.assertEqual(server._request_state_tokens(x_state_only), {"state-only"})

    def test_scope_requirement_matrix_and_auth_errors_are_exact(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, read_token="read-token")

        self.assertTrue(server._scope_satisfies_requirement("read", "read"))
        self.assertTrue(server._scope_satisfies_requirement("control_basic", "read"))
        self.assertTrue(server._scope_satisfies_requirement("update_admin", "control_admin"))
        self.assertFalse(server._scope_satisfies_requirement(None, "read"))
        self.assertFalse(server._scope_satisfies_requirement("read", "control_basic"))
        self.assertFalse(server._scope_satisfies_requirement("unknown", "read"))
        self.assertFalse(server._scope_satisfies_requirement("update_admin", "unknown"))

        with patch.object(server, "_authorization_scope", return_value="unknown"):
            self.assertEqual(server._auth_error(_FakeHandler("/v1/state/summary"), required_scope="read"), server._UNAUTHORIZED_ERROR)
            self.assertEqual(
                server._auth_error(_FakeHandler("/v1/control/command"), required_scope="control_basic"),
                server._INSUFFICIENT_SCOPE_ERROR,
            )

    def test_authorization_scope_prefers_highest_matching_token(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
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

        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer update-token")), "update_admin")
        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer admin-token")), "control_admin")
        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer control-token")), "control_basic")
        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer read-token")), "read")
        self.assertIsNone(server._authorization_scope(_FakeHandler("/v1/capabilities")))
        self.assertIsNone(server._authorization_scope(_FakeHandler("/v1/capabilities", headers={"Authorization": ""})))

    def test_effective_token_fallbacks_are_ordered_by_scope(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )

        self.assertEqual(LocalControlApiHttpServer._first_configured_token("", ""), "")
        self.assertEqual(LocalControlApiHttpServer._first_configured_token("", "second"), "second")

        auth_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="legacy")
        self.assertEqual(auth_only._effective_read_token(), "legacy")
        self.assertEqual(auth_only._effective_control_token(), "legacy")
        self.assertEqual(auth_only._effective_admin_token(), "legacy")
        self.assertEqual(auth_only._effective_update_token(), "legacy")

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
        self.assertEqual(scoped._effective_read_token(), "read")
        self.assertEqual(scoped._effective_control_token(), "control")
        self.assertEqual(scoped._effective_admin_token(), "admin")
        self.assertEqual(scoped._effective_update_token(), "update")

        fallback = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            control_token="control",
            admin_token="admin",
        )
        self.assertEqual(fallback._effective_read_token(), "control")
        self.assertEqual(fallback._effective_update_token(), "admin")

        admin_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, admin_token="admin")
        self.assertEqual(admin_only._effective_read_token(), "admin")
        self.assertEqual(admin_only._effective_admin_token(), "admin")
        self.assertEqual(admin_only._effective_update_token(), "admin")

        update_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, update_token="update")
        self.assertEqual(update_only._effective_read_token(), "update")
        self.assertEqual(update_only._effective_update_token(), "update")

        control_only = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, control_token="control")
        self.assertEqual(control_only._effective_admin_token(), "control")
        self.assertEqual(control_only._effective_update_token(), "control")

        no_token = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        self.assertEqual(no_token._state_token(), "")
        self.assertEqual(no_token._state_token_headers(), {})

    def test_required_scope_for_command_payload_resolves_paths_and_falls_back_for_invalid_paths(self) -> None:
        resolved_command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        invalid_path = ValueError("bad path")
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(
                side_effect=[
                    resolved_command,
                    invalid_path,
                ]
            ),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        resolved_scope = server._required_scope_for_command_payload({"path": "/Mode", "value": 1})
        fallback_scope = server._required_scope_for_command_payload({"path": "/Unknown", "value": 1})
        explicit_scope = server._required_scope_for_command_payload({"name": "set_mode", "path": "/Mode", "value": 1})

        self.assertEqual(resolved_scope, "control_basic")
        self.assertEqual(fallback_scope, "control_admin")
        self.assertEqual(explicit_scope, "control_basic")
        self.assertEqual(service._control_command_from_payload.call_count, 2)
        self.assertEqual(
            service._control_command_from_payload.call_args_list[0].args[0],
            {"path": "/Mode", "value": 1, "command_id": "", "idempotency_key": ""},
        )
        self.assertEqual(service._control_command_from_payload.call_args_list[0].kwargs, {"source": "http"})

    def test_finer_scopes_gate_admin_and_update_commands(self) -> None:
        command = ControlCommand(name="set_auto_runtime_setting", path="/Auto/StartSurplusWatts", value=1800.0, source="http")
        result = ControlResult.applied_result(command)
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=MagicMock(),
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
            body=b'{"name":"set_auto_runtime_setting","path":"/Auto/StartSurplusWatts","value":1800.0}',
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

        server._handle_post(control_handler)
        service._control_command_from_payload.return_value = ControlCommand(
            name="trigger_software_update",
            path="/Auto/SoftwareUpdateRun",
            value=1,
            source="http",
        )
        server._handle_post(admin_handler)
        server._handle_post(update_handler)

        self.assertEqual(control_handler.status_code, 403)
        self.assertEqual(control_handler.json_payload()["error"]["code"], "insufficient_scope")
        self.assertEqual(admin_handler.status_code, 403)
        self.assertEqual(admin_handler.json_payload()["error"]["code"], "insufficient_scope")
        self.assertEqual(update_handler.status_code, 200)

    def test_start_and_stop_support_unix_socket_mode(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, unix_socket_path="/tmp/control.sock")
        fake_server = MagicMock()
        fake_thread = MagicMock()

        with (
            patch.object(server, "_prepare_unix_socket_path") as prepare_socket,
            patch("venus_evcharger.control.http_api._ThreadingLocalControlUnixHttpServer", return_value=fake_server) as server_factory,
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=fake_thread),
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            server.start()
            self.assertEqual(server.bound_unix_socket_path, "/tmp/control.sock")
            server.stop()

        prepare_socket.assert_called_once_with("/tmp/control.sock")
        server_factory.assert_called_once()
        unlink_mock.assert_called_once_with("/tmp/control.sock")

    def test_prepare_unix_socket_path_handles_missing_socket_existing_socket_and_non_socket(self) -> None:
        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=False),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            LocalControlApiHttpServer._prepare_unix_socket_path("/tmp/missing.sock")
        unlink_mock.assert_not_called()

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=0o140000)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=True),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            LocalControlApiHttpServer._prepare_unix_socket_path("/tmp/existing.sock")
        unlink_mock.assert_called_once_with("/tmp/existing.sock")

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=0o100644)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=False),
        ):
            with self.assertRaises(ValueError):
                LocalControlApiHttpServer._prepare_unix_socket_path("/tmp/not-a-socket")

    def test_get_and_post_unknown_paths_return_not_found(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        get_handler = _FakeHandler("/v1/control/unknown")
        post_handler = _FakeHandler("/v1/control/unknown", body=b"{}")

        server._handle_get(get_handler)
        server._handle_post(post_handler)

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
            LocalControlApiHttpServer._post_target_error("/v1/control/unknown"),
            (HTTPStatus.NOT_FOUND, "not_found", "Not found."),
        )
        self.assertIsNone(LocalControlApiHttpServer._post_target_error("/v1/control/command"))

    def test_command_endpoint_rejects_invalid_json_payloads(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b"{invalid")

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "invalid_json")
        service._control_command_from_payload.assert_not_called()
