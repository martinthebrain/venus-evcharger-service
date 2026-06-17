# SPDX-License-Identifier: GPL-3.0-or-later
from tests.control_api_http_cases_tail_support import *  # noqa: F401,F403

class __ControlApiHttpTailCasesPart2:
    def test_command_endpoint_rejects_payloads_that_fail_strict_command_schema_validation(self) -> None:
        record_audit = MagicMock()
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(side_effect=ValueError("Control command 'set_mode' requires an integer value.")),
            _handle_control_command=MagicMock(),
            _record_control_api_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":"1"}')

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "validation_error")
        self.assertIn("requires an integer value", handler.json_payload()["error"]["message"])
        service._handle_control_command.assert_not_called()
        record_audit.assert_called_once()
        self.assertEqual(record_audit.call_args.kwargs["status_code"], 400)

    def test_read_json_payload_rejects_invalid_content_length_and_non_object_json(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        invalid_length_handler = _FakeHandler("/v1/control/command")
        invalid_length_handler.headers["Content-Length"] = "abc"
        list_handler = _FakeHandler("/v1/control/command", body=b"[]")

        self.assertIsNone(server._read_json_payload(invalid_length_handler))
        self.assertEqual(invalid_length_handler.status_code, 400)
        self.assertEqual(invalid_length_handler.json_payload()["error"]["code"], "invalid_content_length")
        self.assertIsNone(server._read_json_payload(list_handler))
        self.assertEqual(list_handler.status_code, 400)
        self.assertEqual(list_handler.json_payload()["error"]["code"], "invalid_payload")

    def test_write_command_result_rejects_value_errors_and_maps_statuses(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b"{}")

        with patch.object(service, "_control_command_from_payload", side_effect=ValueError("bad payload")):
            server._write_command_result(handler, {})

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["detail"], "bad payload")
        self.assertEqual(handler.json_payload()["error"]["code"], "validation_error")
        self.assertIsNone(handler.json_payload()["command"])
        self.assertIsNone(handler.json_payload()["result"])

        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        self.assertEqual(server._http_status_for_result(ControlResult.applied_result(command)), 200)
        self.assertEqual(server._http_status_for_result(ControlResult.accepted_in_flight_result(command)), 202)
        self.assertEqual(server._http_status_for_result(ControlResult.rejected_result(command)), 409)

    def test_rejected_command_response_contains_structured_error(self) -> None:
        command = ControlCommand(name="set_phase_selection", path="/PhaseSelection", value="P1_P2_P3", source="http")
        result = ControlResult.rejected_result(command, detail="Unsupported phase selection")
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name": "set_phase_selection", "value": "P1_P2_P3"}')

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 409)
        payload = handler.json_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "unsupported_for_topology")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["details"]["status"], "rejected")

    def test_command_endpoint_rejects_unknown_commands_with_semantic_error_code(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(side_effect=ValueError("Unsupported control command 'boom'.")),
            _handle_control_command=MagicMock(),
            _record_control_api_command_audit=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name":"boom","value":1}')

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "unsupported_command")

    def test_payload_error_code_maps_supported_choice_failures_to_unsupported_command(self) -> None:
        self.assertEqual(
            LocalControlApiHttpServer._payload_error_code("Control command 'set_mode' requires one of: 0, 1, 2."),
            "unsupported_command",
        )

    def test_result_error_code_maps_semantic_rejections(self) -> None:
        topology_command = ControlCommand(name="set_phase_selection", path="/PhaseSelection", value="P1_P2_P3")
        update_command = ControlCommand(name="trigger_software_update", path="/Update/Run", value=1)
        mode_command = ControlCommand(name="set_mode", path="/Mode", value=1)
        health_command = ControlCommand(name="set_enable", path="/Enable", value=1)

        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(topology_command, detail="Unsupported phase selection")
            ),
            "unsupported_for_topology",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(update_command, detail="Update already running")
            ),
            "update_in_progress",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(update_command, detail="Update rejected by policy")
            ),
            "command_rejected",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(health_command, detail="Health fault lockout active")
            ),
            "blocked_by_health",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(mode_command, detail="Mode blocked while charging")
            ),
            "blocked_by_mode",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(mode_command, detail="Mode changed externally")
            ),
            "command_rejected",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.accepted_in_flight_result(mode_command, detail="still busy")
            ),
            "conflict",
        )

    def test_rate_limiter_and_critical_cooldown_protect_control_endpoint(self) -> None:
        command = ControlCommand(name="trigger_software_update", path="/Auto/SoftwareUpdateRun", value=1, source="http")
        result = ControlResult.applied_result(command)
        rate_limiter = SimpleNamespace(
            allow_request=MagicMock(side_effect=[(False, 1.5), (True, 0.0)]),
            allow_command=MagicMock(return_value=(False, 2.25)),
        )
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=MagicMock(),
            _control_api_rate_limiter=lambda: rate_limiter,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        rate_handler = _FakeHandler("/v1/control/command", body=b'{"name":"trigger_software_update","value":1}')
        cooldown_handler = _FakeHandler("/v1/control/command", body=b'{"name":"trigger_software_update","value":1}')

        server._handle_post(rate_handler)
        server._handle_post(cooldown_handler)

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
