# SPDX-License-Identifier: GPL-3.0-or-later
from tests.control_api_http_cases_tail_support import *  # noqa: F401,F403
from venus_evcharger.control.http_api_commands import CONTROL_API_MAX_REQUEST_BODY_BYTES
from venus_evcharger.control.http_api_command_payloads import (
    command_response_payload,
    idempotency_conflict_response,
    idempotency_fingerprint,
    optimistic_concurrency_payload,
    replayed_payload,
    throttled_response,
    tracked_command,
    tracked_payload,
)
from venus_evcharger.control.service import ControlApiV1Service

class __ControlApiHttpTailCasesPart2:
    def test_command_endpoint_rejects_payloads_that_fail_strict_command_schema_validation(self) -> None:
        record_audit = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=MagicMock(side_effect=ValueError("Control command 'set_mode' requires an integer value.")),
            handle_control_command=MagicMock(),
            record_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":"1"}')

        server.router.handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "validation_error")
        self.assertIn("requires an integer value", handler.json_payload()["error"]["message"])
        service.handle_control_command.assert_not_called()
        record_audit.assert_called_once()
        self.assertEqual(record_audit.call_args.kwargs["status_code"], 400)

    def test_read_json_payload_rejects_invalid_content_length_and_non_object_json(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        invalid_length_handler = _FakeHandler("/v1/control/command")
        invalid_length_handler.headers["Content-Length"] = "abc"
        list_handler = _FakeHandler("/v1/control/command", body=b"[]")

        self.assertIsNone(server.commands.read_json_payload(invalid_length_handler))
        self.assertEqual(invalid_length_handler.status_code, 400)
        self.assertEqual(invalid_length_handler.json_payload()["error"]["code"], "invalid_content_length")
        self.assertEqual(invalid_length_handler.json_payload()["detail"], "Invalid Content-Length.")
        self.assertIsNone(server.commands.read_json_payload(list_handler))
        self.assertEqual(list_handler.status_code, 400)
        self.assertEqual(list_handler.json_payload()["error"]["code"], "invalid_payload")
        self.assertEqual(list_handler.json_payload()["detail"], "JSON body must be an object.")

    def test_read_json_payload_accepts_empty_and_valid_json_and_rejects_bad_utf8(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        empty_handler = _FakeHandler("/v1/control/command", body=b"")
        valid_handler = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":1}')
        bad_utf8_handler = _FakeHandler("/v1/control/command", body=b"\xff")
        negative_length_handler = _FakeHandler("/v1/control/command", body=b'{"ignored":true}')
        negative_length_handler.headers["Content-Length"] = "-3"
        missing_length_handler = _FakeHandler("/v1/control/command", body=b"")
        del missing_length_handler.headers["Content-Length"]

        self.assertEqual(server.commands.read_json_payload(empty_handler), {})
        self.assertEqual(server.commands.read_json_payload(valid_handler), {"name": "set_mode", "value": 1})
        self.assertIsNone(server.commands.read_json_payload(negative_length_handler))
        self.assertEqual(negative_length_handler.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(negative_length_handler.json_payload()["error"]["code"], "invalid_content_length")
        self.assertEqual(negative_length_handler.rfile.tell(), 0)
        self.assertEqual(server.commands.read_json_payload(missing_length_handler), {})
        self.assertIsNone(server.commands.read_json_payload(bad_utf8_handler))
        self.assertEqual(bad_utf8_handler.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(bad_utf8_handler.json_payload()["error"]["code"], "invalid_json")
        self.assertEqual(bad_utf8_handler.json_payload()["detail"], "Invalid JSON body.")

    def test_read_json_payload_rejects_oversized_body_without_reading_it(self) -> None:
        server = LocalControlApiHttpServer(
            control_api_http_service(),
            host="127.0.0.1",
            port=8765,
        )
        handler = _FakeHandler("/v1/control/command", body=b"{}")
        handler.headers["Content-Length"] = str(CONTROL_API_MAX_REQUEST_BODY_BYTES + 1)

        self.assertIsNone(server.commands.read_json_payload(handler))
        self.assertEqual(handler.status_code, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(handler.json_payload()["error"]["code"], "payload_too_large")
        self.assertIn(str(CONTROL_API_MAX_REQUEST_BODY_BYTES), handler.json_payload()["detail"])
        self.assertEqual(handler.rfile.tell(), 0)

    def test_command_role_rate_limit_replay_cache_concurrency_and_audit_paths(self) -> None:
        command = ControlCommand(
            name="set_mode",
            target="mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.applied_result(command, detail="Applied.")
        event_publish = MagicMock()
        audit = MagicMock()
        store = SimpleNamespace(get=MagicMock(return_value=None), put=MagicMock())
        limiter = SimpleNamespace(
            allow_request=MagicMock(return_value=(True, 0.0)),
            allow_command=MagicMock(return_value=(True, 0.0)),
        )
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            idempotency_store=MagicMock(return_value=store),
            rate_limiter=MagicMock(return_value=limiter),
            publish_command_event=event_publish,
            record_command_audit=audit,
            state_token=MagicMock(return_value="state-1"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        self.assertIsNone(server.rate_limit.error("", "set_mode"))
        limiter.allow_request.assert_called_with("local")
        limiter.allow_command.assert_called_with("local", "set_mode")

        limiter.allow_request.return_value = (False, 1.2)
        request_limit = server.rate_limit.error("client-a", "set_mode")
        self.assertIsNotNone(request_limit)
        assert request_limit is not None
        self.assertEqual(request_limit[0], HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(request_limit[1]["error"]["code"], "rate_limited")
        self.assertEqual(request_limit[1]["detail"], "Too many control requests in a short time window.")
        self.assertEqual(request_limit[1]["error"]["message"], "Too many control requests in a short time window.")
        limiter.allow_command.assert_called_once_with("local", "set_mode")

        limiter.allow_request.return_value = (True, 0.0)
        limiter.allow_command.return_value = (False, 2.0)
        command_limit = server.rate_limit.error("client-b", "set_mode")
        self.assertIsNotNone(command_limit)
        assert command_limit is not None
        self.assertEqual(command_limit[1]["error"]["code"], "cooldown_active")
        self.assertIn("set_mode", command_limit[1]["detail"])
        self.assertEqual(command_limit[1]["error"]["message"], "Command 'set_mode' is temporarily cooling down.")

        self.assertIsNone(server.idempotency.replayed_response({}))
        self.assertIsNone(server.idempotency.replayed_response({"idempotency_key": ""}))
        self.assertIsNone(server.idempotency.replayed_response({"idempotency_key": "missing"}))
        cached_payload = {
            "ok": True,
            "detail": "Applied.",
            "command": {"name": "set_mode"},
            "result": {"status": "applied"},
            "replayed": False,
            "error": None,
        }
        store.get.return_value = (idempotency_fingerprint({"idempotency_key": "idem-1", "value": 1}), 200, cached_payload)
        replay = server.idempotency.replayed_response({"idempotency_key": "idem-1", "value": 1})
        self.assertEqual(replay, (HTTPStatus.OK, {**cached_payload, "replayed": True}))
        event_publish.assert_called_once_with({"name": "set_mode"}, {"status": "applied"}, replayed=True)

        conflict = server.idempotency.replayed_response({"idempotency_key": "idem-1", "value": 2})
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict[0], HTTPStatus.CONFLICT)
        self.assertEqual(conflict[1]["error"]["code"], "idempotency_conflict")
        self.assertEqual(conflict[1]["error"]["details"], {"idempotency_key": "idem-1"})

        serialized_command = server.responder.command_payload(command)
        serialized_result = server.responder.result_payload(result)
        server.idempotency.cache_response(
            {},
            HTTPStatus.OK,
            cached_payload,
            command_payload=serialized_command,
            result_payload=serialized_result,
        )
        server.idempotency.cache_response(
            {"idempotency_key": ""},
            HTTPStatus.OK,
            cached_payload,
            command_payload=serialized_command,
            result_payload=serialized_result,
        )
        put_calls_after_empty = store.put.call_count
        server.idempotency.cache_response(
            {"idempotency_key": "idem-1", "value": 1},
            HTTPStatus.ACCEPTED,
            cached_payload,
            command_payload=serialized_command,
            result_payload=serialized_result,
        )
        self.assertEqual(store.put.call_count, put_calls_after_empty + 1)
        put_args = store.put.call_args.args
        self.assertEqual(put_args[0], "idem-1")
        self.assertEqual(put_args[2], int(HTTPStatus.ACCEPTED))
        self.assertEqual(put_args[3]["command"]["command_id"], "cmd-1")
        self.assertEqual(put_args[3]["result"]["status"], "applied")

        no_token_handler = _FakeHandler("/v1/control/command")
        wildcard_handler = _FakeHandler("/v1/control/command", headers={"If-Match": "*"})
        matching_handler = _FakeHandler("/v1/control/command", headers={"If-Match": '"state-1"'})
        stale_handler = _FakeHandler("/v1/control/command", headers={"If-Match": '"stale"'})
        self.assertIsNone(server.authenticator.concurrency_error(no_token_handler))
        self.assertIsNone(server.authenticator.concurrency_error(wildcard_handler))
        self.assertIsNone(server.authenticator.concurrency_error(matching_handler))
        concurrency = server.authenticator.concurrency_error(stale_handler)
        self.assertIsNotNone(concurrency)
        assert concurrency is not None
        self.assertEqual(concurrency[0], HTTPStatus.CONFLICT)
        self.assertEqual(concurrency[1]["error"]["details"]["current"], "state-1")
        self.assertEqual(concurrency[2], {"ETag": '"state-1"', "X-State-Token": "state-1"})

        server.commands.record_audit(
            command={"name": "set_mode"},
            result={"status": "applied"},
            error=None,
            replayed=False,
            client_host="client-c",
            status=HTTPStatus.OK,
        )
        self.assertEqual(
            audit.call_args.kwargs,
            {
                "command": {"name": "set_mode"},
                "result": {"status": "applied"},
                "error": None,
                "replayed": False,
                "scope": "control",
                "client_host": "client-c",
                "status_code": 200,
                "transport": "http",
            },
        )

    def test_write_command_result_delegates_each_branch_with_tracked_payload_and_client_host(self) -> None:
        command = ControlCommand(name="set_mode", target="mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", client_host="198.51.100.7")
        tracked = {"name": "set_mode", "command_id": "cmd-1", "idempotency_key": "idem-1"}
        replay = (HTTPStatus.OK, {"replayed": True})
        rate_limit = (HTTPStatus.TOO_MANY_REQUESTS, {"error": {"code": "rate_limited"}}, {"Retry-After": "1"})

        with (
            patch("venus_evcharger.control.http_api_commands.tracked_payload", return_value=tracked) as tracked_payload_mock,
            patch.object(server.authenticator, "client_host", return_value="client-x") as client_host_mock,
            patch.object(server.idempotency, "replayed_response", return_value=replay) as replay_mock,
            patch.object(server.commands, "write_replayed_response") as write_replay,
        ):
            server.commands.write_command_result(handler, {"raw": True})
        tracked_payload_mock.assert_called_once_with(handler, {"raw": True})
        client_host_mock.assert_called_once_with(handler)
        replay_mock.assert_called_once_with(tracked)
        write_replay.assert_called_once_with(handler, replay, "client-x")

        with (
            patch("venus_evcharger.control.http_api_commands.tracked_payload", return_value=tracked),
            patch.object(server.authenticator, "client_host", return_value="client-y"),
            patch.object(server.idempotency, "replayed_response", return_value=None),
            patch.object(service, "control_command_from_payload", side_effect=ValueError("bad command")),
            patch.object(server.commands, "write_validation_error") as write_validation,
        ):
            server.commands.write_command_result(handler, {"raw": True})
        write_validation.assert_called_once_with(handler, tracked, "bad command", "client-y")

        service.control_command_from_payload.side_effect = None
        service.control_command_from_payload.return_value = command
        with (
            patch("venus_evcharger.control.http_api_commands.tracked_payload", return_value=tracked),
            patch.object(server.authenticator, "client_host", return_value="client-z"),
            patch.object(server.idempotency, "replayed_response", return_value=None),
            patch("venus_evcharger.control.http_api_commands.tracked_command", return_value=command) as tracked_command_mock,
            patch.object(server.rate_limit, "error", return_value=rate_limit) as rate_limit_mock,
            patch.object(server.commands, "write_rate_limit_error") as write_rate_limit,
        ):
            server.commands.write_command_result(handler, {"raw": True})
        service.control_command_from_payload.assert_called_with(tracked, source="http")
        tracked_command_mock.assert_called_once_with(tracked, command)
        rate_limit_mock.assert_called_once_with("client-z", "set_mode")
        write_rate_limit.assert_called_once_with(handler, command, rate_limit, "client-z")

        with (
            patch("venus_evcharger.control.http_api_commands.tracked_payload", return_value=tracked),
            patch.object(server.authenticator, "client_host", return_value="client-ok"),
            patch.object(server.idempotency, "replayed_response", return_value=None),
            patch("venus_evcharger.control.http_api_commands.tracked_command", return_value=command),
            patch.object(server.rate_limit, "error", return_value=None),
            patch.object(server.commands, "write_new_response") as write_new,
        ):
            server.commands.write_command_result(handler, {"raw": True})
        service.handle_control_command.assert_called_with(command)
        write_new.assert_called_once_with(handler, tracked, command, result, "client-ok")

    def test_command_role_response_writers_emit_exact_audit_and_json_calls(self) -> None:
        command = ControlCommand(
            name="set_mode",
            target="mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.applied_result(command)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            state_token=MagicMock(return_value="state-1"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command")
        replay_payload = {
            "command": {"name": "set_mode"},
            "result": {"status": "applied"},
            "error": {"code": "replayed"},
            "replayed": True,
        }
        limit_payload = {"error": {"code": "rate_limited"}, "detail": "Slow down."}

        with patch.object(service, "record_command_audit") as audit_mock, patch.object(server.responder, "write_json") as write_mock:
            server.commands.write_replayed_response(handler, (HTTPStatus.ACCEPTED, replay_payload), "client-a")
            server.commands.write_validation_error(
                handler,
                {"name": "bad"},
                "Control command 'set_mode' does not support target 'bad'.",
                "client-b",
            )
            server.commands.write_rate_limit_error(
                handler,
                command,
                (HTTPStatus.TOO_MANY_REQUESTS, limit_payload, {"Retry-After": "5"}),
                "client-c",
            )
            server.commands.write_new_response(handler, {"idempotency_key": ""}, command, result, "client-d")

        self.assertEqual(audit_mock.call_count, 4)
        self.assertEqual(write_mock.call_count, 4)
        self.assertEqual(
            audit_mock.call_args_list[0].kwargs,
            {
                "command": {"name": "set_mode"},
                "result": {"status": "applied"},
                "error": {"code": "replayed"},
                "replayed": True,
                "scope": "control",
                "client_host": "client-a",
                "status_code": int(HTTPStatus.ACCEPTED),
                "transport": "http",
            },
        )
        self.assertEqual(audit_mock.call_args_list[1].kwargs["command"], {"name": "bad"})
        self.assertIsNone(audit_mock.call_args_list[1].kwargs["result"])
        self.assertEqual(audit_mock.call_args_list[1].kwargs["error"]["code"], "unsupported_command")
        self.assertIs(audit_mock.call_args_list[1].kwargs["replayed"], False)
        self.assertEqual(audit_mock.call_args_list[1].kwargs["scope"], "control")
        self.assertEqual(audit_mock.call_args_list[1].kwargs["client_host"], "client-b")
        self.assertEqual(audit_mock.call_args_list[1].kwargs["status_code"], int(HTTPStatus.BAD_REQUEST))
        self.assertEqual(audit_mock.call_args_list[2].kwargs["command"], command)
        self.assertIsNone(audit_mock.call_args_list[2].kwargs["result"])
        self.assertEqual(audit_mock.call_args_list[2].kwargs["error"], {"code": "rate_limited"})
        self.assertIs(audit_mock.call_args_list[2].kwargs["replayed"], False)
        self.assertEqual(audit_mock.call_args_list[2].kwargs["scope"], "control")
        self.assertEqual(audit_mock.call_args_list[2].kwargs["client_host"], "client-c")
        self.assertEqual(audit_mock.call_args_list[2].kwargs["status_code"], int(HTTPStatus.TOO_MANY_REQUESTS))
        self.assertEqual(audit_mock.call_args_list[3].kwargs["command"], command)
        self.assertEqual(audit_mock.call_args_list[3].kwargs["result"], result)
        self.assertIsNone(audit_mock.call_args_list[3].kwargs["error"])
        self.assertIs(audit_mock.call_args_list[3].kwargs["replayed"], False)
        self.assertEqual(audit_mock.call_args_list[3].kwargs["scope"], "control")
        self.assertEqual(audit_mock.call_args_list[3].kwargs["client_host"], "client-d")
        self.assertEqual(audit_mock.call_args_list[3].kwargs["status_code"], int(HTTPStatus.OK))
        self.assertEqual(write_mock.call_args_list[0].args, (handler, HTTPStatus.ACCEPTED, replay_payload))
        self.assertEqual(write_mock.call_args_list[0].kwargs["extra_headers"], {"ETag": '"state-1"', "X-State-Token": "state-1"})
        self.assertEqual(write_mock.call_args_list[1].args[0], handler)
        self.assertEqual(write_mock.call_args_list[1].args[1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(write_mock.call_args_list[1].args[2]["error"]["code"], "unsupported_command")
        self.assertEqual(write_mock.call_args_list[1].kwargs["extra_headers"], {"ETag": '"state-1"', "X-State-Token": "state-1"})
        self.assertEqual(write_mock.call_args_list[2].args, (handler, HTTPStatus.TOO_MANY_REQUESTS, limit_payload))
        self.assertEqual(write_mock.call_args_list[2].kwargs["extra_headers"], {"ETag": '"state-1"', "X-State-Token": "state-1", "Retry-After": "5"})
        self.assertEqual(write_mock.call_args_list[3].args[0], handler)
        self.assertEqual(write_mock.call_args_list[3].args[1], HTTPStatus.OK)
        self.assertEqual(write_mock.call_args_list[3].args[2]["result"]["status"], "applied")
        self.assertIs(write_mock.call_args_list[3].args[2]["replayed"], False)
        self.assertEqual(write_mock.call_args_list[3].kwargs["extra_headers"], {"ETag": '"state-1"', "X-State-Token": "state-1"})

    def test_idempotent_replay_and_cache_ignore_missing_or_blank_keys_without_store_access(self) -> None:
        command = ControlCommand(name="set_mode", target="mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        response_payload = {"ok": True, "detail": "Applied.", "command": {}, "result": {}, "replayed": False, "error": None}

        for payload in ({}, {"idempotency_key": ""}, {"idempotency_key": "   "}):
            store = SimpleNamespace(get=MagicMock(), put=MagicMock())
            service = control_api_http_service(
                control_command_from_payload=MagicMock(return_value=command),
                handle_control_command=MagicMock(return_value=result),
                idempotency_store=MagicMock(return_value=store),
            )
            server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

            self.assertIsNone(server.idempotency.replayed_response(dict(payload)))
            self.assertIsNone(
                server.idempotency.cache_response(
                    dict(payload),
                    HTTPStatus.OK,
                    response_payload,
                    command_payload=server.responder.command_payload(command),
                    result_payload=server.responder.result_payload(result),
                )
            )
            store.get.assert_not_called()
            store.put.assert_not_called()

    def test_replayed_response_uses_exact_cache_key_and_emits_exact_event_payload(self) -> None:
        command_payload = {"name": "set_mode", "target": "mode", "value": 1}
        result_payload = {"status": "applied", "accepted": True}
        cached_payload = {
            "ok": True,
            "detail": "Applied.",
            "command": command_payload,
            "result": result_payload,
            "replayed": False,
            "error": None,
        }
        store = SimpleNamespace(
            get=MagicMock(return_value=('{"value":1}', int(HTTPStatus.OK), cached_payload)),
            put=MagicMock(),
        )
        event_publish = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            idempotency_store=MagicMock(return_value=store),
            publish_command_event=event_publish,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        replay = server.idempotency.replayed_response({"idempotency_key": " idem-1 ", "value": 1})

        self.assertEqual(replay, (HTTPStatus.OK, {**cached_payload, "replayed": True}))
        store.get.assert_called_once_with("idem-1")
        event_publish.assert_called_once_with(command_payload, result_payload, replayed=True)

    def test_replay_conflict_payload_preserves_the_original_idempotency_key(self) -> None:
        cached_payload = {"ok": True, "detail": "Applied.", "command": {}, "result": {}, "replayed": False, "error": None}
        store = SimpleNamespace(
            get=MagicMock(return_value=('{"value":1}', int(HTTPStatus.OK), cached_payload)),
            put=MagicMock(),
        )
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            idempotency_store=MagicMock(return_value=store),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        conflict = server.idempotency.replayed_response({"idempotency_key": " idem-2 ", "value": 2})

        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict[0], HTTPStatus.CONFLICT)
        self.assertEqual(conflict[1]["error"]["details"], {"idempotency_key": "idem-2"})

    def test_replayed_command_event_requires_command_and_result_payloads(self) -> None:
        event_publish = MagicMock()
        request = {"idempotency_key": "idem", "value": 1}
        fingerprint = idempotency_fingerprint(request)
        store = SimpleNamespace(
            get=MagicMock(
                side_effect=[
                    (fingerprint, 200, {"command": None, "result": {"status": "applied"}}),
                    (fingerprint, 200, {"command": {"name": "set_mode"}, "result": None}),
                    (fingerprint, 200, {"command": {"name": "set_mode"}, "result": {"status": "applied"}}),
                ]
            ),
            put=MagicMock(),
        )
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            idempotency_store=MagicMock(return_value=store),
            publish_command_event=event_publish,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        server.idempotency.replayed_response(request)
        server.idempotency.replayed_response(request)
        event_publish.assert_not_called()

        server.idempotency.replayed_response(request)
        event_publish.assert_called_once_with({"name": "set_mode"}, {"status": "applied"}, replayed=True)

    def test_command_response_payload_method_uses_exact_replay_flag_and_rejected_error_contract(self) -> None:
        command = ControlCommand(
            name="set_phase_selection",
            target="phase_selection",
            value="P1_P2_P3",
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.rejected_result(command, detail="Unsupported phase selection", reversible_failure=False)
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        payload = command_response_payload(
            result,
            replayed=True,
            command_payload=server.responder.command_payload(command),
            result_payload=server.responder.result_payload(result),
        )

        self.assertIs(payload["replayed"], True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"]["command_id"], "cmd-1")
        self.assertEqual(payload["result"]["status"], "rejected")
        self.assertEqual(
            payload["error"],
            {
                "code": "unsupported_for_topology",
                "message": "Unsupported phase selection",
                "retryable": False,
                "details": {
                    "status": "rejected",
                    "target": "phase_selection",
                    "command_id": "cmd-1",
                    "idempotency_key": "idem-1",
                },
            },
        )

    def test_new_rejected_command_response_preserves_error_payload_in_audit_and_cache(self) -> None:
        command = ControlCommand(
            name="set_phase_selection",
            target="phase_selection",
            value="P1_P2_P3",
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.rejected_result(command, detail="Unsupported phase selection", reversible_failure=False)
        store = SimpleNamespace(get=MagicMock(), put=MagicMock())
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            idempotency_store=MagicMock(return_value=store),
            state_token=MagicMock(return_value="state-1"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command")

        with patch.object(service, "record_command_audit") as audit_mock, patch.object(server.responder, "write_json") as write_mock:
            server.commands.write_new_response(handler, {"idempotency_key": "idem-1", "value": "P1_P2_P3"}, command, result, "client-e")

        persisted = store.put.call_args.args[3]
        self.assertEqual(store.put.call_args.args[:3], ("idem-1", '{"value":"P1_P2_P3"}', int(HTTPStatus.CONFLICT)))
        self.assertEqual(persisted["error"]["code"], "unsupported_for_topology")
        self.assertEqual(persisted["command"]["command_id"], "cmd-1")
        self.assertEqual(persisted["result"]["status"], "rejected")
        self.assertNotIn("RESULT", persisted)
        self.assertNotIn("XXresultXX", persisted)
        self.assertEqual(audit_mock.call_args.kwargs["error"], persisted["error"])
        self.assertIs(audit_mock.call_args.kwargs["replayed"], False)
        self.assertEqual(audit_mock.call_args.kwargs["status_code"], int(HTTPStatus.CONFLICT))
        self.assertEqual(write_mock.call_args.args[1], HTTPStatus.CONFLICT)
        self.assertEqual(write_mock.call_args.args[2]["error"], persisted["error"])

    def test_record_command_audit_preserves_non_empty_error_payload_exactly(self) -> None:
        audit = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            record_command_audit=audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        error_payload = {"code": "blocked_by_health", "message": "fault"}

        server.commands.record_audit(
            command={"name": "set_enable"},
            result={"status": "rejected"},
            error=error_payload,
            replayed=True,
            client_host="client-f",
            status=HTTPStatus.CONFLICT,
        )

        self.assertEqual(
            audit.call_args.kwargs,
            {
                "command": {"name": "set_enable"},
                "result": {"status": "rejected"},
                "error": error_payload,
                "replayed": True,
                "scope": "control",
                "client_host": "client-f",
                "status_code": 409,
                "transport": "http",
            },
        )

    def test_write_command_result_rejects_value_errors_and_maps_statuses(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b"{}")

        with patch.object(service, "control_command_from_payload", side_effect=ValueError("bad payload")):
            server.commands.write_command_result(handler, {})

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["detail"], "bad payload")
        self.assertEqual(handler.json_payload()["error"]["code"], "validation_error")
        self.assertIsNone(handler.json_payload()["command"])
        self.assertIsNone(handler.json_payload()["result"])

        command = ControlCommand(name="set_mode", target="mode", value=1, source="http")
        self.assertEqual(http_status_for_result(ControlResult.applied_result(command)), 200)
        self.assertEqual(http_status_for_result(ControlResult.accepted_in_flight_result(command)), 202)
        self.assertEqual(http_status_for_result(ControlResult.rejected_result(command)), 409)

    def test_rejected_command_response_contains_structured_error(self) -> None:
        command = ControlCommand(
            name="set_phase_selection",
            target="phase_selection",
            value="P1_P2_P3",
            source="http",
        )
        result = ControlResult.rejected_result(command, detail="Unsupported phase selection")
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name": "set_phase_selection", "value": "P1_P2_P3"}')

        server.router.handle_post(handler)

        self.assertEqual(handler.status_code, 409)
        payload = handler.json_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "unsupported_for_topology")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["details"]["status"], "rejected")

    def test_command_endpoint_rejects_unknown_commands_with_semantic_error_code(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(side_effect=ValueError("Unsupported control command 'boom'.")),
            handle_control_command=MagicMock(),
            record_command_audit=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name":"boom","value":1}')

        server.router.handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "unsupported_command")

    def test_real_command_boundary_rejects_unsupported_or_unnamed_commands_without_dispatch(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets={"set_current"},
            auto_runtime_setting_targets=set(),
        )
        dispatch = MagicMock()
        record_audit = MagicMock()
        service = control_api_http_service(
            control_command_from_payload=api.command_from_payload,
            handle_control_command=dispatch,
            record_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        scenarios = (
            (
                b'{"name":"set_everything_on","value":1}',
                "Unsupported control command 'set_everything_on'.",
                "unsupported_command",
            ),
            (
                b'{"target":"unknown","value":1}',
                "Control command payload must include 'name'.",
                "validation_error",
            ),
        )

        for body, expected_detail, expected_code in scenarios:
            with self.subTest(body=body):
                handler = _FakeHandler("/v1/control/command", body=body)
                server.router.handle_post(handler)
                payload = handler.json_payload()
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(payload["detail"], expected_detail)
                self.assertEqual(payload["error"]["code"], expected_code)
                self.assertFalse(payload["error"]["retryable"])
                self.assertIsNone(payload["command"])
                self.assertIsNone(payload["result"])

        dispatch.assert_not_called()
        self.assertEqual(record_audit.call_count, len(scenarios))

    def test_payload_error_code_maps_supported_choice_failures_to_unsupported_command(self) -> None:
        self.assertEqual(
            payload_error_code("Control command 'set_mode' requires one of: 0, 1, 2."),
            "unsupported_command",
        )
        self.assertEqual(
            payload_error_code("Control command 'set_mode' does not support target 'bogus'."),
            "unsupported_command",
        )
        self.assertEqual(
            payload_error_code("Control command 'set_current_setting' requires an explicit 'target'."),
            "validation_error",
        )
        self.assertEqual(payload_error_code("Plain validation failure."), "validation_error")

    def test_command_payload_helpers_emit_exact_structured_contracts(self) -> None:
        conflict_status, conflict_payload = idempotency_conflict_response("idem-1")
        throttled_status, throttled_payload, throttled_headers = throttled_response("rate_limited", "Slow down.", 1.25)
        integer_status, integer_payload, integer_headers = throttled_response("cooldown_active", "Cooling.", 2.0)
        minimum_status, minimum_payload, minimum_headers = throttled_response("rate_limited", "Tiny retry.", 0.2)
        optimistic_payload = optimistic_concurrency_payload({"z-token", "a-token"}, "current-token")

        self.assertEqual(conflict_status, HTTPStatus.CONFLICT)
        self.assertEqual(
            conflict_payload,
            {
                "ok": False,
                "detail": "Idempotency-Key was already used for a different payload.",
                "command": None,
                "result": None,
                "replayed": False,
                "error": {
                    "code": "idempotency_conflict",
                    "message": "Idempotency-Key was already used for a different payload.",
                    "retryable": False,
                    "details": {"idempotency_key": "idem-1"},
                },
            },
        )
        self.assertEqual(throttled_status, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(throttled_headers, {"Retry-After": "2"})
        self.assertEqual(
            throttled_payload,
            {
                "ok": False,
                "detail": "Slow down.",
                "command": None,
                "result": None,
                "replayed": False,
                "error": {
                    "code": "rate_limited",
                    "message": "Slow down.",
                    "retryable": True,
                    "details": {"retry_after_seconds": 1.25},
                },
            },
        )
        self.assertEqual(integer_status, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(integer_headers, {"Retry-After": "2"})
        self.assertEqual(integer_payload["error"]["code"], "cooldown_active")
        self.assertEqual(minimum_status, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(minimum_headers, {"Retry-After": "1"})
        self.assertEqual(minimum_payload["error"]["message"], "Tiny retry.")
        self.assertEqual(
            optimistic_payload,
            {
                "ok": False,
                "detail": "If-Match state token does not match the current local service state.",
                "command": None,
                "result": None,
                "replayed": False,
                "error": {
                    "code": "conflict",
                    "message": "If-Match state token does not match the current local service state.",
                    "retryable": True,
                    "details": {"expected": ["a-token", "z-token"], "current": "current-token"},
                },
            },
        )

    def test_tracking_payload_helpers_normalize_body_headers_and_missing_fields(self) -> None:
        command = ControlCommand(
            name="set_mode",
            target="mode",
            value=1,
            source="http",
            command_id="old-command",
            idempotency_key="old-idem",
        )
        header_handler = _FakeHandler(
            "/v1/control/command",
            headers={"X-Command-Id": " header-command ", "Idempotency-Key": " header-idem "},
        )
        body_handler = _FakeHandler(
            "/v1/control/command",
            headers={"X-Command-Id": "ignored-command", "Idempotency-Key": "ignored-idem"},
        )

        with patch("venus_evcharger.control.http_api_command_payloads.uuid.uuid4", return_value=SimpleNamespace(hex="generated-command")):
            generated = tracked_payload(_FakeHandler("/v1/control/command"), {"name": "set_mode"})
        from_headers = tracked_payload(header_handler, {"name": "set_mode"})
        from_body = tracked_payload(body_handler, {"command_id": " body-command ", "idempotency_key": " body-idem "})

        self.assertEqual(generated["command_id"], "generated-command")
        self.assertEqual(generated["idempotency_key"], "")
        self.assertEqual(from_headers["command_id"], "header-command")
        self.assertEqual(from_headers["idempotency_key"], "header-idem")
        self.assertEqual(from_body["command_id"], "body-command")
        self.assertEqual(from_body["idempotency_key"], "body-idem")
        self.assertEqual(tracked_command({"idempotency_key": "old-idem"}, command), command)
        self.assertEqual(tracked_command({}, command).command_id, "")
        self.assertEqual(tracked_command({}, command).idempotency_key, "")
        updated = tracked_command({"command_id": " new-command ", "idempotency_key": " new-idem "}, command)
        self.assertEqual(updated.command_id, "new-command")
        self.assertEqual(updated.idempotency_key, "new-idem")

    def test_idempotency_fingerprint_is_stable_and_ignores_tracking_fields(self) -> None:
        class _Opaque:
            def __str__(self) -> str:
                return "opaque-value"

        left = {
            "z": 2,
            "a": _Opaque(),
            "command_id": "cmd-1",
            "idempotency_key": "idem-1",
            "idempotency_note": "kept",
        }
        right = {
            "idempotency_key": "different",
            "command_id": "different",
            "idempotency_note": "kept",
            "a": _Opaque(),
            "z": 2,
        }

        self.assertEqual(idempotency_fingerprint(left), '{"a":"opaque-value","idempotency_note":"kept","z":2}')
        self.assertEqual(idempotency_fingerprint(left), idempotency_fingerprint(right))

    def test_command_response_payloads_are_exact_for_accepted_replayed_and_rejected(self) -> None:
        command = ControlCommand(
            name="set_enable",
            target="enable",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        command_payload = {"name": "set_enable", "target": "enable", "value": 1}
        applied_result_payload = {"status": "applied", "accepted": True}
        applied = command_response_payload(
            ControlResult.applied_result(command, detail="Applied."),
            replayed=True,
            command_payload=command_payload,
            result_payload=applied_result_payload,
        )
        rejected_result_payload = {"status": "rejected", "accepted": False}
        rejected = command_response_payload(
            ControlResult.rejected_result(command, detail="", reversible_failure=False),
            replayed=False,
            command_payload=command_payload,
            result_payload=rejected_result_payload,
        )

        self.assertEqual(
            applied,
            {
                "ok": True,
                "detail": "Applied.",
                "command": command_payload,
                "result": applied_result_payload,
                "replayed": True,
                "error": None,
            },
        )
        self.assertEqual(replayed_payload({**applied, "replayed": False})["replayed"], True)
        self.assertEqual(
            rejected,
            {
                "ok": False,
                "detail": "",
                "command": command_payload,
                "result": rejected_result_payload,
                "replayed": False,
                "error": {
                    "code": "command_rejected",
                    "message": "Command rejected.",
                    "retryable": False,
                    "details": {
                        "status": "rejected",
                        "target": "enable",
                        "command_id": "cmd-1",
                        "idempotency_key": "idem-1",
                    },
                },
            },
        )

    def test_result_error_code_maps_semantic_rejections(self) -> None:
        topology_command = ControlCommand(
            name="set_phase_selection",
            target="phase_selection",
            value="P1_P2_P3",
        )
        update_command = ControlCommand(
            name="trigger_software_update",
            target="auto_software_update_run",
            value=1,
        )
        mode_command = ControlCommand(name="set_mode", target="mode", value=1)
        health_command = ControlCommand(name="set_enable", target="enable", value=1)

        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(topology_command, detail="Unsupported phase selection")
            ),
            "unsupported_for_topology",
        )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(update_command, detail="Update already running")
            ),
            "update_in_progress",
        )
        for detail in ("Update in progress", "Update running now", "Update busy", "Update already queued"):
            self.assertEqual(
                result_error_code(ControlResult.rejected_result(update_command, detail=detail)),
                "update_in_progress",
            )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(update_command, detail="Update rejected by policy")
            ),
            "command_rejected",
        )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(topology_command, detail="Path unsupported for this topology")
            ),
            "unsupported_for_topology",
        )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(mode_command, detail="Unsupported mode transition")
            ),
            "blocked_by_mode",
        )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(health_command, detail="Health fault lockout active")
            ),
            "blocked_by_health",
        )
        for detail in ("Health degraded", "Fault active", "Lockout active", "Recovery running"):
            self.assertEqual(
                result_error_code(ControlResult.rejected_result(health_command, detail=detail)),
                "blocked_by_health",
            )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(mode_command, detail="Mode blocked while charging")
            ),
            "blocked_by_mode",
        )
        for detail in ("Mode blocked", "Mode cannot change", "Mode while update runs"):
            self.assertEqual(
                result_error_code(ControlResult.rejected_result(mode_command, detail=detail)),
                "blocked_by_mode",
            )
        self.assertEqual(
            result_error_code(
                ControlResult.rejected_result(mode_command, detail="Mode changed externally")
            ),
            "command_rejected",
        )
        self.assertEqual(
            result_error_code(
                ControlResult.accepted_in_flight_result(mode_command, detail="still busy")
            ),
            "conflict",
        )

    def test_rate_limiter_and_critical_cooldown_protect_control_endpoint(self) -> None:
        command = ControlCommand(
            name="trigger_software_update",
            target="auto_software_update_run",
            value=1,
            source="http",
        )
        result = ControlResult.applied_result(command)
        rate_limiter = SimpleNamespace(
            allow_request=MagicMock(side_effect=[(False, 1.5), (True, 0.0)]),
            allow_command=MagicMock(return_value=(False, 2.25)),
        )
        service = control_api_http_service(
            control_command_from_payload=MagicMock(return_value=command),
            handle_control_command=MagicMock(return_value=result),
            record_command_audit=MagicMock(),
            rate_limiter=lambda: rate_limiter,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        rate_handler = _FakeHandler("/v1/control/command", body=b'{"name":"trigger_software_update","value":1}')
        cooldown_handler = _FakeHandler("/v1/control/command", body=b'{"name":"trigger_software_update","value":1}')

        server.router.handle_post(rate_handler)
        server.router.handle_post(cooldown_handler)

        self.assertEqual(rate_handler.status_code, 429)
        self.assertEqual(rate_handler.json_payload()["error"]["code"], "rate_limited")
        self.assertEqual(rate_handler.response_headers["Retry-After"], "2")
        self.assertEqual(cooldown_handler.status_code, 429)
        self.assertEqual(cooldown_handler.json_payload()["error"]["code"], "cooldown_active")
        self.assertEqual(cooldown_handler.response_headers["Retry-After"], "3")

    def test_rate_limiter_handles_window_retry_and_critical_cooldown_directly(self) -> None:
        limiter = ControlApiRateLimiter(max_requests=1, window_seconds=5.0, critical_cooldown_seconds=3.0)

        self.assertEqual(limiter.allow_request("local", now=10.0), (True, 0.0))
        allowed, retry_after = limiter.allow_request("local", now=12.0)
        self.assertFalse(allowed)
        self.assertEqual(retry_after, 3.0)
        self.assertEqual(limiter.allow_request("local", now=15.5), (True, 0.0))
        self.assertEqual(limiter.allow_command("local", "set_mode", now=20.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("local", "trigger_software_update", now=20.0), (True, 0.0))
        critical_allowed, critical_retry = limiter.allow_command("local", "trigger_software_update", now=21.0)
        self.assertFalse(critical_allowed)
        self.assertEqual(critical_retry, 2.0)
