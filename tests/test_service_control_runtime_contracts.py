# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.control.http_api_command_contracts import ControlApiHttpService
from venus_evcharger.service.control_runtime import ControlRuntime


def _runtime(**service_overrides: object) -> tuple[ControlRuntime, SimpleNamespace]:
    values: dict[str, object] = {
        "control_api_enabled": False,
        "control_api_audit_max_entries": 7,
        "control_api_audit_path": "",
        "control_api_idempotency_max_entries": 8,
        "control_api_idempotency_path": "",
        "control_api_rate_limit_max_requests": 9,
        "control_api_rate_limit_window_seconds": 4.0,
        "control_api_critical_cooldown_seconds": 1.5,
    }
    values.update(service_overrides)
    service = SimpleNamespace(**values)
    http_service = MagicMock(spec=ControlApiHttpService)
    return ControlRuntime(service, http_service), service


class TestControlRuntimeContracts(unittest.TestCase):
    def test_owned_runtime_components_are_created_once(self) -> None:
        runtime, _service = _runtime()

        self.assertIs(runtime.audit_trail(), runtime.audit_trail())
        self.assertIs(runtime.idempotency_store(), runtime.idempotency_store())
        self.assertIs(runtime.rate_limiter(), runtime.rate_limiter())
        self.assertIs(runtime.event_bus(), runtime.event_bus())
        self.assertFalse(runtime.running)

    def test_command_and_result_payloads_have_one_exact_shape(self) -> None:
        command = ControlCommand(
            name="set_mode",
            target="mode",
            value=2,
            source="http",
            detail="scheduled",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult(
            command=command,
            status="applied",
            accepted=True,
            applied=True,
            persisted=False,
            reversible_failure=False,
            external_side_effect_started=True,
            detail="ok",
        )

        self.assertEqual(ControlRuntime.command_payload(None, "http"), {})
        self.assertEqual(ControlRuntime.command_payload({"name": "set_enable"}, "http"), {"name": "set_enable"})
        self.assertEqual(
            ControlRuntime.command_payload(command, "ignored"),
            {
                "name": "set_mode",
                "target": "mode",
                "value": 2,
                "source": "http",
                "detail": "scheduled",
                "command_id": "cmd-1",
                "idempotency_key": "idem-1",
            },
        )
        self.assertEqual(ControlRuntime.result_payload(None), {})
        self.assertEqual(ControlRuntime.result_payload({"status": "queued"}), {"status": "queued"})
        self.assertEqual(
            ControlRuntime.result_payload(result),
            {
                "status": "applied",
                "accepted": True,
                "applied": True,
                "persisted": False,
                "reversible_failure": False,
                "external_side_effect_started": True,
                "detail": "ok",
            },
        )

    def test_command_audit_and_events_preserve_semantic_payloads(self) -> None:
        runtime, _service = _runtime()
        command = {"name": "set_mode", "value": 1}
        result = {"status": "applied", "accepted": True}

        with patch("venus_evcharger.service.control_runtime.time.time", return_value=123.5):
            entry = runtime.record_command_audit(
                command=command,
                result=result,
                error={"code": "none"},
                replayed=True,
                scope="control_basic",
                client_host="127.0.0.1",
                status_code=200,
            )

        self.assertEqual(entry["timestamp"], 123.5)
        self.assertEqual(entry["command"], command)
        self.assertEqual(entry["result"], result)
        self.assertEqual(entry["error"], {"code": "none"})
        self.assertEqual(runtime.audit_trail().count(), 1)

        runtime._events = MagicMock()
        runtime.publish_command_event(command, result, replayed=True)
        runtime.publish_state_event({"mode": 2})
        self.assertEqual(
            runtime._events.publish.call_args_list[0].args,
            ("command", {"command": command, "result": result, "replayed": True}),
        )
        self.assertEqual(runtime._events.publish.call_args_list[1].args, ("snapshot", {"mode": 2}))

    def test_disabled_server_is_a_noop_and_enabled_server_is_owned(self) -> None:
        runtime, service = _runtime()
        runtime.start_server({"mode": 0})
        self.assertFalse(runtime.running)

        service.control_api_enabled = True
        service.control_api_host = "127.0.0.1"
        service.control_api_port = 8765
        service.control_api_auth_token = "legacy"
        service.control_api_read_token = "read"
        service.control_api_control_token = "control"
        service.control_api_admin_token = "admin"
        service.control_api_update_token = "update"
        service.control_api_localhost_only = True
        service.control_api_unix_socket_path = "/run/control.sock"
        fake_server = MagicMock(
            bound_host="127.0.0.1",
            bound_port=8765,
            bound_unix_socket_path="/run/control.sock",
        )

        with patch(
            "venus_evcharger.service.control_runtime.LocalControlApiHttpServer",
            return_value=fake_server,
        ) as factory:
            runtime.start_server({"mode": 2})
            runtime.start_server({"mode": 1})

        factory.assert_called_once_with(
            runtime.http_service,
            host="127.0.0.1",
            port=8765,
            auth_token="legacy",
            read_token="read",
            control_token="control",
            admin_token="admin",
            update_token="update",
            localhost_only=True,
            unix_socket_path="/run/control.sock",
        )
        self.assertEqual(fake_server.start.call_count, 2)
        self.assertTrue(runtime.running)
        self.assertEqual(service.control_api_listen_host, "127.0.0.1")
        self.assertEqual(service.control_api_listen_port, 8765)
        self.assertEqual(service.control_api_bound_unix_socket_path, "/run/control.sock")

        runtime.stop_server()
        fake_server.stop.assert_called_once_with()

    def test_stop_without_server_is_idempotent(self) -> None:
        runtime, _service = _runtime()
        runtime.stop_server()
        self.assertFalse(runtime.running)


if __name__ == "__main__":
    unittest.main()
