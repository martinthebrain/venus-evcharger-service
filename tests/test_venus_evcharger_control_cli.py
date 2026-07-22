# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import runpy
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.control import cli
from venus_evcharger.control.client import ControlApiClientResponse

from tests.venus_evcharger_control_test_support import started_control_api_server


class _FakeClientForCliMainGuard:
    def __init__(self, *args: object, **kwargs: object) -> None:
        return None

    def state(self, name: str) -> ControlApiClientResponse:
        return ControlApiClientResponse(status=200, headers={}, body=json.dumps({"kind": name, "ok": True}))

    def health(self) -> ControlApiClientResponse:
        return ControlApiClientResponse(status=200, headers={}, body=json.dumps({"ok": True, "kind": "health"}))

    def openapi(self) -> ControlApiClientResponse:
        return ControlApiClientResponse(status=200, headers={}, body=json.dumps({"openapi": "3.1.0"}))


class TestVenusEvchargerControlCli(unittest.TestCase):
    def test_exit_code_contract_is_explicit(self) -> None:
        self.assertEqual(cli.EXIT_OK, 0)
        self.assertEqual(cli.EXIT_REQUEST_FAILED, 1)
        self.assertEqual(cli.EXIT_USAGE, 2)
        self.assertEqual(cli._exit_code_for_status(200), 0)
        self.assertEqual(cli._exit_code_for_status(202), 0)
        self.assertEqual(cli._exit_code_for_status(409), 1)

    def test_parse_cli_value_covers_common_scalar_shapes(self) -> None:
        self.assertTrue(cli._parse_cli_value("true"))
        self.assertFalse(cli._parse_cli_value("off"))
        self.assertEqual(cli._parse_cli_value("12"), 12)
        self.assertEqual(cli._parse_cli_value("12.5"), 12.5)
        self.assertEqual(cli._parse_cli_value('"hello"'), "hello")
        self.assertEqual(cli._parse_cli_value("raw-text"), "raw-text")

    def test_command_payload_and_compact_helpers_cover_optional_branches(self) -> None:
        namespace = SimpleNamespace(name="set-current-setting", value="12.5", target="set_current", detail="manual")
        payload = cli._command_payload(namespace)
        self.assertEqual(
            payload,
            {"name": "set_current_setting", "target": "set_current", "value": 12.5, "detail": "manual"},
        )

        compact_stdout = io.StringIO()
        with redirect_stdout(compact_stdout):
            cli._write_json({"ok": True}, compact=True)
        self.assertEqual(compact_stdout.getvalue(), '{"ok":true}\n')

        stream_stdout = io.StringIO()
        with redirect_stdout(stream_stdout):
            rc = cli._write_event_response(
                ControlApiClientResponse(status=200, headers={}, body='{"kind":"command"}'),
                compact=True,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(stream_stdout.getvalue(), '{"kind":"command"}\n')

        empty_stdout = io.StringIO()
        with redirect_stdout(empty_stdout):
            cli._write_stream_body("")
        self.assertEqual(empty_stdout.getvalue(), "")

    def test_run_capabilities_and_unknown_dispatch_paths_are_covered(self) -> None:
        with patch.object(cli, "_client") as client_factory:
            client_factory.return_value.capabilities.return_value = ControlApiClientResponse(
                status=200,
                headers={},
                body='{"ok":true,"kind":"capabilities"}',
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli._run_capabilities(SimpleNamespace(compact=False))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())["kind"], "capabilities")

        fake_parser = SimpleNamespace(parse_args=lambda _argv=None: SimpleNamespace(subcommand="unknown"))
        with patch.object(cli, "build_parser", return_value=fake_parser):
            with self.assertRaises(SystemExit) as exit_info:
                cli.main([])
        self.assertEqual(exit_info.exception.code, cli.EXIT_USAGE)

    def test_run_health_and_openapi_paths_are_covered(self) -> None:
        with patch.object(cli, "_client") as client_factory:
            client_factory.return_value.health.return_value = ControlApiClientResponse(
                status=200,
                headers={},
                body='{"ok":true,"kind":"health"}',
            )
            client_factory.return_value.openapi.return_value = ControlApiClientResponse(
                status=200,
                headers={},
                body='{"openapi":"3.1.0"}',
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                health_rc = cli._run_health(SimpleNamespace(compact=False))
            self.assertEqual(health_rc, 0)
            self.assertEqual(json.loads(stdout.getvalue())["kind"], "health")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                openapi_rc = cli._run_openapi(SimpleNamespace(compact=True))
            self.assertEqual(openapi_rc, 0)
            self.assertEqual(json.loads(stdout.getvalue())["openapi"], "3.1.0")

    def test_safe_write_result_reports_missing_state_token(self) -> None:
        fake_state_response = ControlApiClientResponse(status=200, headers={}, body='{"ok":true,"state":{}}')
        with patch.object(cli, "_client_for_token") as client_factory:
            client_factory.return_value.state.return_value = fake_state_response
            exit_code, payload = cli._safe_write_result(
                SimpleNamespace(
                    url="http://127.0.0.1:8765",
                    unix_socket="",
                    timeout=5.0,
                    token="control-token",
                    read_token="read-token",
                    idempotency_key="",
                    command_id="",
                ),
                token="control-token",
                state_endpoint="health",
                payload={"name": "set_mode", "value": 1},
            )
        self.assertEqual(exit_code, cli.EXIT_REQUEST_FAILED)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "missing_state_token")

    def test_response_payload_and_state_token_helpers_cover_non_dict_payloads(self) -> None:
        list_response = SimpleNamespace(json=lambda: ["event"], headers={}, status=200)
        self.assertEqual(cli._response_payload(list_response), {"value": ["event"]})

        self.assertEqual(
            cli._state_token_from_response(SimpleNamespace(json=lambda: {}, headers={"X-State-Token": '"abc"'}, status=200)),
            "abc",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(json=lambda: {"state": {"state_token": '"body-token"'}}, headers={}, status=200)
            ),
            "body-token",
        )
        self.assertEqual(
            cli._state_token_from_response(SimpleNamespace(json=lambda: {"state": 1}, headers={}, status=200)),
            "",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(json=lambda: {"state": {"state_token": 1}}, headers={}, status=200)
            ),
            "",
        )

        missing_token_response = ControlApiClientResponse(
            status=200,
            headers={},
            body='{"state":{"state_token":1}}',
        )
        self.assertEqual(cli._state_token_from_response(missing_token_response), "")
        self.assertEqual(
            cli._state_token_from_response(
                ControlApiClientResponse(status=200, headers={}, body='{"state":1}')
            ),
            "",
        )

    def test_doctor_without_tokens_still_reports_health_and_skips_authenticated_checks(self) -> None:
        with started_control_api_server() as (_service, server):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli.main(["--url", f"http://{server.bound_host}:{server.bound_port}", "doctor"])
            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["kind"], "doctor")
            self.assertEqual(payload["checks"][0]["name"], "health")
            self.assertGreaterEqual(payload["summary"]["skipped"], 1)

    def test_doctor_safe_write_without_control_token_is_reported_as_skipped(self) -> None:
        namespace = SimpleNamespace(
            url="http://127.0.0.1:8765",
            unix_socket="",
            timeout=5.0,
            token="",
            read_token="read-token",
            control_token="",
            safe_write=True,
            compact=False,
        )
        read_client = SimpleNamespace(
            capabilities=lambda: ControlApiClientResponse(status=200, headers={}, body='{"ok":true,"kind":"capabilities"}'),
            state=lambda name: ControlApiClientResponse(status=200, headers={}, body=json.dumps({"ok": True, "kind": name})),
            events=lambda **_kwargs: ControlApiClientResponse(status=200, headers={}, body='{"kind":"command"}'),
        )
        health_client = SimpleNamespace(
            health=lambda: ControlApiClientResponse(status=200, headers={}, body='{"ok":true,"kind":"health"}')
        )

        def _client_for_token(_namespace: object, token: str) -> object:
            return health_client if not token else read_client

        stdout = io.StringIO()
        with patch.object(cli, "_client_for_token", side_effect=_client_for_token), redirect_stdout(stdout):
            rc = cli._run_doctor(namespace)
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertIn("safe write skipped: no control token provided", payload["skipped"])

    def test_cli_module_main_guard_runs(self) -> None:
        stdout = io.StringIO()
        argv = sys.argv[:]
        sys.argv = ["venus_evcharger/control/cli.py", "--token", "read-token", "state", "summary"]
        try:
            with (
                patch("venus_evcharger.control.client.LocalControlApiClient", _FakeClientForCliMainGuard),
                redirect_stdout(stdout),
                self.assertRaises(SystemExit) as exit_info,
            ):
                runpy.run_path("venus_evcharger/control/cli.py", run_name="__main__")
        finally:
            sys.argv = argv
        self.assertEqual(exit_info.exception.code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["kind"], "summary")

    def test_state_command_and_events_work_against_live_server(self) -> None:
        with started_control_api_server() as (_service, server):
            base_args = [
                "--url",
                f"http://{server.bound_host}:{server.bound_port}",
            ]

            health_stdout = io.StringIO()
            with redirect_stdout(health_stdout):
                rc = cli.main([*base_args, "health"])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(health_stdout.getvalue())["ok"])

            state_stdout = io.StringIO()
            with redirect_stdout(state_stdout):
                rc = cli.main([*base_args, "--token", "read-token", "state", "summary"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(state_stdout.getvalue())["kind"], "summary")

            command_stdout = io.StringIO()
            with redirect_stdout(command_stdout):
                rc = cli.main([*base_args, "--token", "control-token", "command", "set-mode", "1"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(command_stdout.getvalue())["result"]["status"], "applied")

            openapi_stdout = io.StringIO()
            with redirect_stdout(openapi_stdout):
                rc = cli.main([*base_args, "openapi"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(openapi_stdout.getvalue())["openapi"], "3.1.0")

            events_stdout = io.StringIO()
            with redirect_stdout(events_stdout):
                rc = cli.main(
                    [
                        *base_args,
                        "--token",
                        "read-token",
                        "events",
                        "--kind",
                        "command",
                        "--once",
                    ]
                )
            self.assertEqual(rc, 0)
            events = json.loads(events_stdout.getvalue())
            self.assertTrue(events)
            self.assertEqual(events[-1]["kind"], "command")

    def test_doctor_safe_write_and_watch_work_against_live_server(self) -> None:
        with started_control_api_server() as (_service, server):
            base_args = ["--url", f"http://{server.bound_host}:{server.bound_port}"]

            doctor_stdout = io.StringIO()
            with redirect_stdout(doctor_stdout):
                rc = cli.main(
                    [
                        *base_args,
                        "--token",
                        "read-token",
                        "doctor",
                        "--control-token",
                        "control-token",
                        "--safe-write",
                    ]
                )
            self.assertEqual(rc, 0)
            doctor_payload = json.loads(doctor_stdout.getvalue())
            self.assertTrue(doctor_payload["ok"])
            self.assertIn("safe-write.set-mode", [item["name"] for item in doctor_payload["checks"]])

            safe_write_stdout = io.StringIO()
            with redirect_stdout(safe_write_stdout):
                rc = cli.main(
                    [
                        *base_args,
                        "--token",
                        "control-token",
                        "safe-write",
                        "set-mode",
                        "1",
                    ]
                )
            self.assertEqual(rc, 0)
            safe_write_payload = json.loads(safe_write_stdout.getvalue())
            self.assertTrue(safe_write_payload["ok"])
            self.assertTrue(safe_write_payload["state_token"])
            self.assertEqual(safe_write_payload["response"]["result"]["status"], "applied")

            cli.main([*base_args, "--token", "control-token", "command", "set-mode", "1"])
            watch_stdout = io.StringIO()
            with redirect_stdout(watch_stdout):
                rc = cli.main(
                    [
                        *base_args,
                        "--token",
                        "read-token",
                        "watch",
                        "--kind",
                        "command",
                        "--once",
                    ]
                )
            self.assertEqual(rc, 0)
            watch_lines = [json.loads(line) for line in watch_stdout.getvalue().splitlines() if line.strip()]
            self.assertTrue(watch_lines)
            self.assertEqual(watch_lines[-1]["kind"], "command")


if __name__ == "__main__":
    unittest.main()
