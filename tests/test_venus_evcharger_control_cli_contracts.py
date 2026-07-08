# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from venus_evcharger.control import cli, cli_parser
from venus_evcharger.control.client import ControlApiClientResponse


class _FakeClient:
    def __init__(self, response: ControlApiClientResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, object]] = []

    def state(self, name: str) -> ControlApiClientResponse:
        self.calls.append(("state", name))
        return self.response

    def capabilities(self) -> ControlApiClientResponse:
        self.calls.append(("capabilities", None))
        return self.response

    def health(self) -> ControlApiClientResponse:
        self.calls.append(("health", None))
        return self.response

    def openapi(self) -> ControlApiClientResponse:
        self.calls.append(("openapi", None))
        return self.response

    def command(self, payload: object, **kwargs: object) -> ControlApiClientResponse:
        self.calls.append(("command", {"payload": payload, "kwargs": kwargs}))
        return self.response

    def events(self, **kwargs: object) -> ControlApiClientResponse:
        self.calls.append(("events", kwargs))
        return self.response


def _response(
    status: int = 200,
    body: str = '{"ok":true}',
    headers: dict[str, str] | None = None,
) -> ControlApiClientResponse:
    return ControlApiClientResponse(status=status, headers=headers or {}, body=body)


def _parser_help(parser: argparse.ArgumentParser, args: list[str]) -> str:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        with unittest.TestCase().assertRaises(SystemExit) as exit_info:
            parser.parse_args([*args, "--help"])
    if exit_info.exception.code != 0:
        raise AssertionError(f"help exited with {exit_info.exception.code}")
    return stdout.getvalue()


def _option_action(parser: argparse.ArgumentParser, option: str) -> argparse.Action:
    for action in parser._actions:
        if option in action.option_strings:
            return action
    raise AssertionError(f"missing parser option {option}")


def _positional_action(parser: argparse.ArgumentParser, dest: str) -> argparse.Action:
    for action in parser._actions:
        if not action.option_strings and action.dest == dest:
            return action
    raise AssertionError(f"missing parser positional {dest}")


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("missing subparsers action")


def _subcommand_help(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], name: str) -> str:
    for choice_action in subparsers._choices_actions:
        if choice_action.dest == name:
            return str(choice_action.help)
    raise AssertionError(f"missing subcommand help {name}")


class TestControlCliContracts(unittest.TestCase):
    def test_parser_global_defaults_and_state_choices_are_stable(self) -> None:
        parser = cli_parser.build_parser()
        namespace = parser.parse_args(["state", "summary"])
        self.assertEqual(namespace.subcommand, "state")
        self.assertEqual(namespace.state_name, "summary")
        self.assertEqual(namespace.url, "http://127.0.0.1:8765")
        self.assertEqual(namespace.unix_socket, "")
        self.assertEqual(namespace.token, "")
        self.assertEqual(namespace.timeout, 5.0)
        self.assertFalse(namespace.compact)

        namespace = parser.parse_args(
            ["--url", "http://api", "--unix-socket", "/tmp/control.sock", "--token", "t", "--timeout", "9.5", "--compact", "health"]
        )
        self.assertEqual(namespace.subcommand, "health")
        self.assertEqual(namespace.url, "http://api")
        self.assertEqual(namespace.unix_socket, "/tmp/control.sock")
        self.assertEqual(namespace.token, "t")
        self.assertEqual(namespace.timeout, 9.5)
        self.assertTrue(namespace.compact)

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["state", "unknown"])

    def test_parser_action_structure_is_exact(self) -> None:
        parser = cli_parser.build_parser()
        self.assertEqual(parser.description, "Small local client for the Venus EV charger Control API.")
        self.assertEqual(_option_action(parser, "--url").default, "http://127.0.0.1:8765")
        self.assertEqual(_option_action(parser, "--url").help, "Base URL for the local HTTP API.")
        self.assertEqual(_option_action(parser, "--unix-socket").default, "")
        self.assertEqual(_option_action(parser, "--unix-socket").help, "Optional unix socket path to use instead of TCP.")
        self.assertEqual(_option_action(parser, "--token").default, "")
        self.assertEqual(_option_action(parser, "--token").help, "Bearer token for read/control access.")
        self.assertIs(_option_action(parser, "--timeout").type, float)
        self.assertEqual(_option_action(parser, "--timeout").default, 5.0)
        self.assertEqual(_option_action(parser, "--timeout").help, "Request timeout in seconds.")
        self.assertFalse(_option_action(parser, "--compact").default)
        self.assertEqual(_option_action(parser, "--compact").help, "Print compact JSON instead of pretty JSON.")

        subparsers = _subparsers_action(parser)
        self.assertEqual(subparsers.dest, "subcommand")
        self.assertTrue(subparsers.required)
        self.assertEqual(
            set(subparsers.choices),
            {"state", "capabilities", "health", "openapi", "doctor", "command", "events", "watch", "safe-write"},
        )
        self.assertEqual(_subcommand_help(subparsers, "state"), "Read one normalized state payload.")
        self.assertEqual(_subcommand_help(subparsers, "capabilities"), "Read the capabilities payload.")
        self.assertEqual(_subcommand_help(subparsers, "health"), "Read the local API health payload.")
        self.assertEqual(_subcommand_help(subparsers, "openapi"), "Read the OpenAPI 3.1 description.")
        self.assertEqual(_subcommand_help(subparsers, "doctor"), "Run a small local API/CLI self-test.")
        self.assertEqual(_subcommand_help(subparsers, "command"), "Send one canonical control command.")
        self.assertEqual(_subcommand_help(subparsers, "events"), "Read one NDJSON event stream snapshot.")
        self.assertEqual(_subcommand_help(subparsers, "watch"), "Follow the event stream with ergonomic defaults.")
        self.assertEqual(
            _subcommand_help(subparsers, "safe-write"),
            "Fetch the current state token and send one command with If-Match.",
        )

        state_parser = subparsers.choices["state"]
        self.assertEqual(
            tuple(_positional_action(state_parser, "state_name").choices or ()),
            (
                "summary",
                "automation",
                "victron-bias-recommendation",
                "runtime",
                "operational",
                "dbus-diagnostics",
                "topology",
                "update",
                "health",
                "healthz",
                "version",
                "build",
                "contracts",
                "config-effective",
            ),
        )

        command_parser = subparsers.choices["command"]
        self.assertEqual(_positional_action(command_parser, "name").help, "Canonical command name, for example set-mode.")
        self.assertEqual(
            _positional_action(command_parser, "value").help,
            "Command value. JSON scalars such as 1, 12.5, true are accepted.",
        )
        self.assertEqual(_option_action(command_parser, "--path").default, "")
        self.assertEqual(_option_action(command_parser, "--path").help, "Explicit write path when the command family requires it.")
        self.assertEqual(_option_action(command_parser, "--detail").default, "")
        self.assertEqual(_option_action(command_parser, "--detail").help, "Optional detail string carried with the command.")
        self.assertEqual(_option_action(command_parser, "--command-id").default, "")
        self.assertEqual(_option_action(command_parser, "--command-id").help, "Optional client-supplied command id.")
        self.assertEqual(_option_action(command_parser, "--idempotency-key").default, "")
        self.assertEqual(_option_action(command_parser, "--idempotency-key").help, "Optional replay-safe idempotency key.")
        self.assertEqual(_option_action(command_parser, "--if-match").default, "")
        self.assertEqual(_option_action(command_parser, "--if-match").help, "Optional optimistic concurrency token.")

        safe_parser = subparsers.choices["safe-write"]
        self.assertEqual(tuple(_option_action(safe_parser, "--state-endpoint").choices or ()), ("automation", "health", "operational"))
        self.assertEqual(_option_action(safe_parser, "--state-endpoint").default, "automation")
        self.assertEqual(
            _option_action(safe_parser, "--state-endpoint").help,
            "State endpoint used to fetch the current optimistic concurrency token.",
        )
        self.assertEqual(_option_action(safe_parser, "--read-token").default, "")
        self.assertEqual(_option_action(safe_parser, "--read-token").help, "Optional explicit read token used to fetch the state token before writing.")

        doctor_parser = subparsers.choices["doctor"]
        self.assertEqual(_option_action(doctor_parser, "--read-token").default, "")
        self.assertEqual(_option_action(doctor_parser, "--read-token").help, "Optional explicit read token for doctor checks.")
        self.assertEqual(_option_action(doctor_parser, "--control-token").default, "")
        self.assertEqual(_option_action(doctor_parser, "--control-token").help, "Optional explicit control token for the safe-write doctor step.")
        self.assertFalse(_option_action(doctor_parser, "--safe-write").default)
        self.assertEqual(_option_action(doctor_parser, "--safe-write").help, "Also perform one optimistic-concurrency safe write using the current mode value.")

        for subcommand, timeout, heartbeat, limit in (("events", 2.0, 0.5, 20), ("watch", 30.0, 1.0, 50)):
            events_parser = subparsers.choices[subcommand]
            self.assertIs(_option_action(events_parser, "--limit").type, int)
            self.assertEqual(_option_action(events_parser, "--limit").default, limit)
            self.assertIs(_option_action(events_parser, "--after").type, int)
            self.assertIsNone(_option_action(events_parser, "--after").default)
            self.assertIs(_option_action(events_parser, "--resume").type, int)
            self.assertIsNone(_option_action(events_parser, "--resume").default)
            self.assertIs(_option_action(events_parser, "--timeout").type, float)
            self.assertEqual(_option_action(events_parser, "--timeout").default, timeout)
            self.assertIs(_option_action(events_parser, "--heartbeat").type, float)
            self.assertEqual(_option_action(events_parser, "--heartbeat").default, heartbeat)
            self.assertEqual(_option_action(events_parser, "--kind").default, [])
            self.assertEqual(_option_action(events_parser, "--kind").help, "Optional event kind filter.")
            self.assertFalse(_option_action(events_parser, "--once").default)

    def test_parser_command_safe_write_doctor_and_events_contracts(self) -> None:
        parser = cli_parser.build_parser()
        command = parser.parse_args(
            [
                "command",
                "set-mode",
                "2",
                "--path",
                "/Mode",
                "--detail",
                "manual",
                "--command-id",
                "cmd",
                "--idempotency-key",
                "idem",
                "--if-match",
                "token",
            ]
        )
        self.assertEqual(command.subcommand, "command")
        self.assertEqual(command.name, "set-mode")
        self.assertEqual(command.value, "2")
        self.assertEqual(command.path, "/Mode")
        self.assertEqual(command.detail, "manual")
        self.assertEqual(command.command_id, "cmd")
        self.assertEqual(command.idempotency_key, "idem")
        self.assertEqual(command.if_match, "token")

        safe_write = parser.parse_args(
            [
                "safe-write",
                "set-current",
                "6",
                "--state-endpoint",
                "operational",
                "--read-token",
                "read",
                "--command-id",
                "cmd",
                "--idempotency-key",
                "idem",
            ]
        )
        self.assertEqual(safe_write.subcommand, "safe-write")
        self.assertEqual(safe_write.state_endpoint, "operational")
        self.assertEqual(safe_write.read_token, "read")
        self.assertFalse(hasattr(safe_write, "if_match"))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["safe-write", "set-mode", "1", "--if-match", "token"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["safe-write", "set-mode", "1", "--state-endpoint", "runtime"])

        doctor = parser.parse_args(["doctor", "--read-token", "r", "--control-token", "c", "--safe-write"])
        self.assertEqual(doctor.subcommand, "doctor")
        self.assertEqual(doctor.read_token, "r")
        self.assertEqual(doctor.control_token, "c")
        self.assertTrue(doctor.safe_write)

        capabilities = parser.parse_args(["capabilities"])
        self.assertEqual(capabilities.subcommand, "capabilities")
        self.assertEqual(capabilities.state_name, "")

        events = parser.parse_args(["events", "--limit", "7", "--after", "3", "--resume", "4", "--kind", "command", "--kind", "state", "--once"])
        self.assertEqual(events.subcommand, "events")
        self.assertEqual(events.limit, 7)
        self.assertEqual(events.after, 3)
        self.assertEqual(events.resume, 4)
        self.assertEqual(events.timeout, 2.0)
        self.assertEqual(events.heartbeat, 0.5)
        self.assertEqual(events.kind, ["command", "state"])
        self.assertTrue(events.once)

        watch = parser.parse_args(["watch"])
        self.assertEqual(watch.subcommand, "watch")
        self.assertEqual(watch.limit, 50)
        self.assertEqual(watch.timeout, 30.0)
        self.assertEqual(watch.heartbeat, 1.0)
        self.assertEqual(watch.kind, [])
        self.assertFalse(watch.once)

    def test_parser_help_text_is_a_public_contract(self) -> None:
        parser = cli_parser.build_parser()
        top_help = parser.format_help()
        for expected in (
            "Small local client for the Venus EV charger Control API.",
            "--url URL",
            "Base URL for the local HTTP API.",
            "--unix-socket UNIX_SOCKET",
            "Optional unix socket path to use instead of TCP.",
            "--token TOKEN",
            "Bearer token for read/control access.",
            "--timeout TIMEOUT",
            "Request timeout in seconds.",
            "--compact",
            "Print compact JSON instead of pretty JSON.",
            "Read the capabilities payload.",
            "Read the local API health payload.",
            "Read the OpenAPI 3.1 description.",
            "Run a small local API/CLI self-test.",
            "Send one canonical control command.",
            "Read one NDJSON event stream snapshot.",
            "Follow the event stream with ergonomic defaults.",
            "Fetch the current state token and send one command",
            "with If-Match.",
        ):
            self.assertIn(expected, top_help)

        state_help = _parser_help(parser, ["state"])
        for expected in (
            "summary",
            "automation",
            "victron-bias-recommendation",
            "runtime",
            "operational",
            "dbus-diagnostics",
            "topology",
            "config-effective",
        ):
            self.assertIn(expected, state_help)

        command_help = _parser_help(parser, ["command"])
        for expected in (
            "Canonical command name, for example set-mode.",
            "Command value. JSON scalars such as 1, 12.5, true are",
            "accepted.",
            "--path PATH",
            "Explicit write path when the command family requires",
            "it.",
            "--detail DETAIL",
            "Optional detail string carried with the command.",
            "--command-id COMMAND_ID",
            "Optional client-supplied command id.",
            "--idempotency-key IDEMPOTENCY_KEY",
            "Optional replay-safe idempotency key.",
            "--if-match IF_MATCH",
            "Optional optimistic concurrency token.",
        ):
            self.assertIn(expected, command_help)

        safe_help = _parser_help(parser, ["safe-write"])
        for expected in (
            "--state-endpoint {automation,health,operational}",
            "State endpoint used to fetch the current optimistic",
            "concurrency token.",
            "--read-token READ_TOKEN",
            "Optional explicit read token used to fetch the state",
            "token before writing.",
        ):
            self.assertIn(expected, safe_help)
        self.assertNotIn("--if-match", safe_help)

        doctor_help = _parser_help(parser, ["doctor"])
        for expected in (
            "--read-token READ_TOKEN",
            "Optional explicit read token for doctor checks.",
            "--control-token CONTROL_TOKEN",
            "Optional explicit control token for the safe-write",
            "doctor step.",
            "--safe-write",
            "Also perform one optimistic-concurrency safe write",
            "using the current mode value.",
        ):
            self.assertIn(expected, doctor_help)

        events_help = _parser_help(parser, ["events"])
        watch_help = _parser_help(parser, ["watch"])
        for help_text in (events_help, watch_help):
            for expected in (
                "--limit LIMIT",
                "--after AFTER",
                "--resume RESUME",
                "--timeout TIMEOUT",
                "--heartbeat HEARTBEAT",
                "--kind KIND",
                "Optional event kind filter.",
                "--once",
            ):
                self.assertIn(expected, help_text)

    def test_parse_cli_value_and_command_name_contracts(self) -> None:
        for truthy in (" YES ", "true", "TRUE", "on", "ON"):
            self.assertIs(cli._parse_cli_value(truthy), True)
        for falsy in (" no ", "false", "FALSE", "off", "OFF"):
            self.assertIs(cli._parse_cli_value(falsy), False)
        self.assertEqual(cli._parse_cli_value("12"), 12)
        self.assertEqual(cli._parse_cli_value("12.5"), 12.5)
        self.assertEqual(cli._parse_cli_value('{"a":1}'), {"a": 1})
        self.assertIsNone(cli._parse_cli_value("null"))
        self.assertEqual(cli._parse_cli_value("  [1,2]  "), [1, 2])
        self.assertEqual(cli._parse_cli_value("raw-text"), "raw-text")
        self.assertEqual(cli._normalized_command_name(" set-mode-now "), "set_mode_now")

    def test_client_factory_passes_all_connection_arguments(self) -> None:
        namespace = SimpleNamespace(url="http://host", unix_socket="/tmp/api.sock", timeout=7.5, token="global")

        with patch("venus_evcharger.control.cli.LocalControlApiClient") as client_class:
            cli._client(namespace)
            cli._client_for_token(namespace, "explicit")

        self.assertEqual(client_class.call_args_list[0].kwargs["base_url"], "http://host")
        self.assertEqual(client_class.call_args_list[0].kwargs["unix_socket_path"], "/tmp/api.sock")
        self.assertEqual(client_class.call_args_list[0].kwargs["bearer_token"], "global")
        self.assertEqual(client_class.call_args_list[0].kwargs["timeout"], 7.5)
        self.assertEqual(client_class.call_args_list[1].kwargs["bearer_token"], "explicit")

    def test_json_and_stream_writers_have_stable_output_shapes(self) -> None:
        pretty_stdout = io.StringIO()
        with redirect_stdout(pretty_stdout):
            cli._write_json({"z": 1, "a": 2}, compact=False)
        self.assertEqual(pretty_stdout.getvalue(), '{\n  "a": 2,\n  "z": 1\n}\n')

        compact_stdout = io.StringIO()
        with redirect_stdout(compact_stdout):
            cli._write_json({"ok": True}, compact=True)
        self.assertEqual(compact_stdout.getvalue(), '{"ok":true}\n')
        with self.assertRaises(TypeError) as error_info:
            cli._write_json({"ok": True}, compact=cast(bool, None))
        self.assertEqual(str(error_info.exception), "compact must be bool")

        stream_stdout = io.StringIO()
        with redirect_stdout(stream_stdout):
            cli._write_stream_body("line")
        self.assertEqual(stream_stdout.getvalue(), "line\n")

        newline_stdout = io.StringIO()
        with redirect_stdout(newline_stdout):
            cli._write_stream_body("line\n")
        self.assertEqual(newline_stdout.getvalue(), "line\n")

        empty_stdout = io.StringIO()
        with redirect_stdout(empty_stdout):
            cli._write_stream_body("")
        self.assertEqual(empty_stdout.getvalue(), "")

    def test_response_helpers_extract_status_payload_and_state_token(self) -> None:
        dict_response = SimpleNamespace(status=204, json=lambda: {"kind": "ok"}, headers={})
        list_response = SimpleNamespace(status=409, json=lambda: [1, 2], headers={})

        self.assertTrue(cli._response_ok(dict_response))
        self.assertFalse(cli._response_ok(list_response))
        self.assertTrue(cli._response_ok(SimpleNamespace(status=200)))
        self.assertTrue(cli._response_ok(SimpleNamespace(status=299)))
        self.assertFalse(cli._response_ok(SimpleNamespace(status=199)))
        self.assertFalse(cli._response_ok(SimpleNamespace(status=300)))
        self.assertEqual(cli._response_payload(dict_response), {"kind": "ok"})
        self.assertEqual(cli._response_payload(list_response), {"value": [1, 2]})
        self.assertEqual(
            cli._state_token_from_response(SimpleNamespace(status=200, json=lambda: {}, headers={"X-State-Token": '"XhX"'})),
            "XhX",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(status=200, json=lambda: {}, headers={"X-State-Token": " unquoted "})
            ),
            "unquoted",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(status=200, json=lambda: {"state": {"state_token": '"body"'}}, headers={})
            ),
            "body",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(status=200, json=lambda: {"state": {"state_token": '"XbodyX"'}}, headers={})
            ),
            "XbodyX",
        )
        self.assertEqual(
            cli._state_token_from_response(SimpleNamespace(status=200, json=lambda: {"state": {}}, headers={})),
            "",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(status=200, json=lambda: {"state": {"state_token": 5}}, headers={})
            ),
            "",
        )
        self.assertEqual(
            cli._state_token_from_response(SimpleNamespace(status=200, json=lambda: {"state": {"other": "x"}}, headers={})),
            "",
        )
        self.assertEqual(
            cli._state_token_from_response(
                SimpleNamespace(status=200, json=lambda: {"state": {"state_token": "  spaced  "}}, headers={})
            ),
            "spaced",
        )
        self.assertEqual(cli._state_token_from_response(SimpleNamespace(status=200, json=lambda: {"state": 1}, headers={})), "")
        self.assertEqual(
            cli._state_token_from_response(ControlApiClientResponse(status=200, headers={}, body='{"state":{"state_token":"c"}}')),
            "c",
        )
        self.assertEqual(cli._exit_code_for_status(199), cli.EXIT_REQUEST_FAILED)
        self.assertEqual(cli._exit_code_for_status(200), cli.EXIT_OK)
        self.assertEqual(cli._exit_code_for_status(299), cli.EXIT_OK)
        self.assertEqual(cli._exit_code_for_status(300), cli.EXIT_REQUEST_FAILED)

    def test_command_event_and_doctor_payload_builders_are_stable(self) -> None:
        namespace = argparse.Namespace(
            name="set-mode",
            value="1",
            path="/Mode",
            detail="manual",
            limit=10,
            after=2,
            resume=3,
            timeout=4.0,
            heartbeat=0.5,
            kind=["command", "state"],
            once=True,
        )

        self.assertEqual(
            cli._command_payload(namespace),
            {"name": "set_mode", "value": 1, "path": "/Mode", "detail": "manual"},
        )
        self.assertEqual(
            cli._event_request_kwargs(namespace),
            {
                "limit": 10,
                "after": 2,
                "resume": 3,
                "timeout": 4.0,
                "heartbeat": 0.5,
                "kinds": ("command", "state"),
                "once": True,
            },
        )
        self.assertEqual(
            cli._doctor_payload([{"ok": True}, {"ok": False}], ["skip"], [{"ok": False}])["summary"],
            {"passed": 1, "failed": 1, "skipped": 1},
        )
        doctor = cli._doctor_payload([{"ok": True}], [], [])
        self.assertTrue(doctor["ok"])
        self.assertEqual(doctor["kind"], "doctor")
        self.assertEqual(doctor["checks"], [{"ok": True}])
        self.assertEqual(doctor["skipped"], [])
        self.assertEqual(cli._safe_payload_kind({"kind": "custom"}), "custom")
        self.assertEqual(cli._safe_payload_kind({"kind": ""}), "safe-write")
        self.assertEqual(cli._safe_payload_kind({"kind": 7}), "safe-write")
        self.assertEqual(cli._safe_payload_kind({}), "safe-write")

        self.assertEqual(
            cli._doctor_check("state.summary", SimpleNamespace(status=201, json=lambda: {"kind": "state"}, headers={})),
            {"name": "state.summary", "ok": True, "status": 201, "kind": "state"},
        )
        self.assertEqual(
            cli._doctor_check("bad", SimpleNamespace(status=500, json=lambda: ["not-dict"], headers={})),
            {"name": "bad", "ok": False, "status": 500, "kind": ""},
        )
        event_response = ControlApiClientResponse(status=200, headers={}, body='{"a":1}\n{"b":2}\n')
        self.assertEqual(
            cli._doctor_event_check("events.once", event_response),
            {"name": "events.once", "ok": True, "status": 200, "kind": "events", "event_count": 2},
        )

    def test_run_helpers_call_expected_client_methods_and_return_status_codes(self) -> None:
        response = ControlApiClientResponse(status=200, headers={}, body='{"ok":true,"kind":"payload"}')
        fake_client = _FakeClient(response)
        namespace = argparse.Namespace(
            compact=True,
            state_name="summary",
            name="set-mode",
            value="1",
            path="",
            detail="",
            idempotency_key="idem",
            command_id="cmd",
            if_match="token",
            limit=1,
            after=None,
            resume=None,
            timeout=1.0,
            heartbeat=0.5,
            kind=[],
            once=True,
        )

        stdout = io.StringIO()
        with patch.object(cli, "_client", return_value=fake_client), redirect_stdout(stdout):
            self.assertEqual(cli._run_state(namespace), cli.EXIT_OK)
            self.assertEqual(cli._run_capabilities(namespace), cli.EXIT_OK)
            self.assertEqual(cli._run_health(namespace), cli.EXIT_OK)
            self.assertEqual(cli._run_openapi(namespace), cli.EXIT_OK)
            self.assertEqual(cli._run_command(namespace), cli.EXIT_OK)
            self.assertEqual(cli._run_events(namespace), cli.EXIT_OK)
            self.assertEqual(cli._run_watch(namespace), cli.EXIT_OK)

        self.assertEqual(
            [name for name, _payload in fake_client.calls],
            ["state", "capabilities", "health", "openapi", "command", "events", "events"],
        )
        self.assertIn('"kind":"payload"', stdout.getvalue())
        command_call = fake_client.calls[4][1]
        self.assertIsInstance(command_call, dict)
        self.assertEqual(command_call["kwargs"], {"idempotency_key": "idem", "command_id": "cmd", "if_match": "token"})
        self.assertEqual(
            fake_client.calls[5][1],
            {"limit": 1, "after": None, "resume": None, "timeout": 1.0, "heartbeat": 0.5, "kinds": (), "once": True},
        )

    def test_run_helpers_preserve_namespace_compact_and_payload_contracts(self) -> None:
        response = ControlApiClientResponse(status=207, headers={}, body='{"payload":true}')
        namespace = argparse.Namespace(
            compact=False,
            state_name="health",
            name="set-current",
            value="6",
            path="/SetCurrent",
            detail="limit",
            idempotency_key="idem",
            command_id="cmd",
            if_match="state-token",
            limit=5,
            after=1,
            resume=2,
            timeout=3.0,
            heartbeat=0.25,
            kind=["command"],
            once=False,
        )

        for runner, expected_call in (
            (cli._run_state, ("state", "health")),
            (cli._run_capabilities, ("capabilities", None)),
            (cli._run_health, ("health", None)),
            (cli._run_openapi, ("openapi", None)),
        ):
            fake_client = _FakeClient(response)
            stdout = io.StringIO()
            with patch.object(cli, "_client", return_value=fake_client) as client_factory, redirect_stdout(stdout):
                self.assertEqual(runner(namespace), cli.EXIT_OK)
            client_factory.assert_called_once_with(namespace)
            self.assertEqual(fake_client.calls, [expected_call])
            self.assertEqual(json.loads(stdout.getvalue()), {"payload": True})

        fake_client = _FakeClient(response)
        stdout = io.StringIO()
        with patch.object(cli, "_client", return_value=fake_client) as client_factory, redirect_stdout(stdout):
            self.assertEqual(cli._run_command(namespace), cli.EXIT_OK)
        client_factory.assert_called_once_with(namespace)
        self.assertEqual(
            fake_client.calls,
            [
                (
                    "command",
                    {
                        "payload": {"name": "set_current", "value": 6, "path": "/SetCurrent", "detail": "limit"},
                        "kwargs": {"idempotency_key": "idem", "command_id": "cmd", "if_match": "state-token"},
                    },
                )
            ],
        )
        self.assertEqual(json.loads(stdout.getvalue()), {"payload": True})

        event_response = ControlApiClientResponse(status=207, headers={}, body='{"e":1}\n')
        fake_client = _FakeClient(event_response)
        stdout = io.StringIO()
        with patch.object(cli, "_client", return_value=fake_client) as client_factory, redirect_stdout(stdout):
            self.assertEqual(cli._run_events(namespace), cli.EXIT_OK)
        client_factory.assert_called_once_with(namespace)
        self.assertEqual(
            fake_client.calls,
            [
                (
                    "events",
                    {"limit": 5, "after": 1, "resume": 2, "timeout": 3.0, "heartbeat": 0.25, "kinds": ("command",), "once": False},
                )
            ],
        )
        self.assertEqual(json.loads(stdout.getvalue()), [{"e": 1}])

        fake_client = _FakeClient(event_response)
        stdout = io.StringIO()
        with patch.object(cli, "_client", return_value=fake_client) as client_factory, redirect_stdout(stdout):
            self.assertEqual(cli._run_watch(namespace), cli.EXIT_OK)
        client_factory.assert_called_once_with(namespace)
        self.assertEqual(stdout.getvalue(), '{"e":1}\n')

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(cli._write_event_response(event_response, compact=False), cli.EXIT_OK)
        self.assertEqual(stdout.getvalue(), '[\n  {\n    "e": 1\n  }\n]\n')
        with self.assertRaises(TypeError) as error_info:
            cli._write_event_response(event_response, compact=cast(bool, None))
        self.assertEqual(str(error_info.exception), "compact must be bool")

    def test_safe_write_paths_cover_missing_and_present_state_tokens(self) -> None:
        namespace = argparse.Namespace(
            url="http://host",
            unix_socket="",
            timeout=5.0,
            token="control",
            read_token="read",
            idempotency_key="idem",
            command_id="cmd",
            compact=True,
            state_endpoint="health",
            name="set-mode",
            value="1",
            path="",
            detail="manual",
        )
        missing_token_client = _FakeClient(ControlApiClientResponse(status=200, headers={}, body='{"state":{}}'))
        command_client = _FakeClient(ControlApiClientResponse(status=202, headers={}, body='{"result":{"status":"accepted"}}'))
        token_client = _FakeClient(ControlApiClientResponse(status=200, headers={"X-State-Token": '"state-token"'}, body="{}"))

        with patch.object(cli, "_client_for_token", side_effect=[missing_token_client]):
            exit_code, payload = cli._safe_write_result(namespace, token="control", state_endpoint="health", payload={"name": "set_mode"})
        self.assertEqual(exit_code, cli.EXIT_REQUEST_FAILED)
        self.assertEqual(
            payload,
            {"ok": False, "kind": "safe-write", "error": "missing_state_token", "state_endpoint": "health"},
        )
        self.assertEqual(missing_token_client.calls, [("state", "health")])

        with patch.object(cli, "_client_for_token", side_effect=[token_client, command_client]) as client_factory:
            exit_code, payload = cli._safe_write_result(namespace, token="control", state_endpoint="health", payload={"name": "set_mode"})
        self.assertEqual(exit_code, cli.EXIT_OK)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "kind": "safe-write",
                "state_endpoint": "health",
                "state_token": "state-token",
                "command": {"name": "set_mode"},
                "response": {"result": {"status": "accepted"}},
            },
        )
        self.assertEqual(token_client.calls, [("state", "health")])
        self.assertEqual([call.args for call in client_factory.call_args_list], [(namespace, "read"), (namespace, "control")])
        self.assertEqual(
            command_client.calls,
            [
                (
                    "command",
                    {
                        "payload": {"name": "set_mode"},
                        "kwargs": {"idempotency_key": "idem", "command_id": "cmd", "if_match": "state-token"},
                    },
                )
            ],
        )

        default_namespace = argparse.Namespace(url="http://host", unix_socket="", timeout=5.0, token="global")
        token_client = _FakeClient(ControlApiClientResponse(status=200, headers={"X-State-Token": "fallback-token"}, body="{}"))
        command_client = _FakeClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))
        with patch.object(cli, "_client_for_token", side_effect=[token_client, command_client]) as client_factory:
            exit_code, payload = cli._safe_write_result(
                default_namespace,
                token="control",
                state_endpoint="summary",
                payload={"name": "set_current", "value": 6},
            )
        self.assertEqual(exit_code, cli.EXIT_OK)
        self.assertEqual([call.args for call in client_factory.call_args_list], [(default_namespace, "global"), (default_namespace, "control")])
        self.assertEqual(
            command_client.calls,
            [
                (
                    "command",
                    {
                        "payload": {"name": "set_current", "value": 6},
                        "kwargs": {"idempotency_key": "", "command_id": "", "if_match": "fallback-token"},
                    },
                )
            ],
        )
        self.assertEqual(payload["state_endpoint"], "summary")

        run_stdout = io.StringIO()
        token_client = _FakeClient(ControlApiClientResponse(status=200, headers={"X-State-Token": "run-token"}, body="{}"))
        command_client = _FakeClient(ControlApiClientResponse(status=409, headers={}, body='{"error":"conflict"}'))
        with patch.object(cli, "_client_for_token", side_effect=[token_client, command_client]) as client_factory, redirect_stdout(
            run_stdout
        ):
            self.assertEqual(cli._run_safe_write(namespace), cli.EXIT_REQUEST_FAILED)
        self.assertEqual([call.args for call in client_factory.call_args_list], [(namespace, "read"), (namespace, "control")])
        self.assertEqual(
            json.loads(run_stdout.getvalue()),
            {
                "command": {"detail": "manual", "name": "set_mode", "value": 1},
                "kind": "safe-write",
                "ok": False,
                "response": {"error": "conflict"},
                "state_endpoint": "health",
                "state_token": "run-token",
            },
        )

    def test_doctor_helpers_append_read_and_safe_write_checks(self) -> None:
        read_client = _FakeClient(ControlApiClientResponse(status=200, headers={}, body='{"kind":"read"}'))
        namespace = argparse.Namespace(safe_write=False, read_token="", control_token="")
        checks: list[dict[str, object]] = []
        skipped: list[str] = []

        cli._append_doctor_read_checks(namespace, "", checks, skipped)
        self.assertEqual(skipped, ["authenticated read checks skipped: no read token provided"])

        with patch.object(cli, "_client_for_token", return_value=read_client) as client_factory:
            cli._append_doctor_read_checks(namespace, "read", checks, skipped)
        client_factory.assert_called_once_with(namespace, "read")
        self.assertEqual([check["name"] for check in checks], ["capabilities", "state.summary", "state.health", "events.once"])
        self.assertEqual(
            read_client.calls,
            [
                ("capabilities", None),
                ("state", "summary"),
                ("state", "health"),
                ("events", {"kinds": ("command",), "once": True, "limit": 10, "timeout": 2.0, "heartbeat": 0.5}),
            ],
        )

        skipped.clear()
        cli._append_doctor_safe_write_checks(namespace, "read", "", checks, skipped)
        self.assertEqual(skipped, [])

        namespace.safe_write = True
        cli._append_doctor_safe_write_checks(namespace, "read", "", checks, skipped)
        self.assertEqual(skipped, ["safe write skipped: no control token provided"])

        operational_response = ControlApiClientResponse(status=200, headers={"X-State-Token": "op-token"}, body='{"state":{"mode":2}}')
        safe_namespace = argparse.Namespace(
            safe_write=True,
            read_token="read",
            control_token="control",
            token="global",
            url="http://host",
            unix_socket="",
            timeout=3.0,
            compact=True,
        )
        derived = cli._doctor_safe_write_namespace(safe_namespace, "reader", operational_response)
        self.assertEqual(derived.name, "set-mode")
        self.assertEqual(derived.value, "2")
        self.assertEqual(derived.path, "")
        self.assertEqual(derived.detail, "doctor-safe-write")
        self.assertEqual(derived.command_id, "doctor-safe-write")
        self.assertEqual(derived.idempotency_key, "doctor-safe-write")
        self.assertEqual(derived.read_token, "reader")
        self.assertEqual(derived.state_endpoint, "health")

        control_client = _FakeClient(operational_response)
        command_client = _FakeClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))
        derived_for_read_token = cli._doctor_safe_write_namespace(safe_namespace, "read", operational_response)
        with patch.object(cli, "_client_for_token", side_effect=[control_client, control_client, command_client]) as client_factory:
            checks = []
            skipped = []
            cli._append_doctor_safe_write_checks(safe_namespace, "read", "control", checks, skipped)
        self.assertEqual(
            [call.args for call in client_factory.call_args_list],
            [(safe_namespace, "control"), (derived_for_read_token, "read"), (derived_for_read_token, "control")],
        )
        self.assertEqual(control_client.calls[0], ("state", "operational"))
        self.assertEqual(skipped, [])
        self.assertEqual([check["name"] for check in checks], ["state.operational", "safe-write.set-mode"])
        self.assertEqual(checks[1], {"name": "safe-write.set-mode", "ok": True, "status": 200, "kind": "safe-write"})

        with patch.object(cli, "_doctor_safe_write_namespace", return_value=derived) as namespace_factory, patch.object(
            cli, "_safe_write_payload", return_value={"name": "set_mode", "value": 2}
        ) as payload_factory, patch.object(
            cli, "_safe_write_result", return_value=(cli.EXIT_REQUEST_FAILED, {"kind": "custom-safe"})
        ) as result_factory:
            self.assertEqual(
                cli._doctor_safe_write_check(safe_namespace, "reader", "control", operational_response),
                {"name": "safe-write.set-mode", "ok": False, "status": 409, "kind": "custom-safe"},
            )
        namespace_factory.assert_called_once_with(safe_namespace, "reader", operational_response)
        payload_factory.assert_called_once_with(derived)
        result_factory.assert_called_once_with(
            derived,
            token="control",
            state_endpoint="health",
            payload={"name": "set_mode", "value": 2},
        )

        missing_mode = cli._doctor_safe_write_namespace(
            safe_namespace,
            "reader",
            ControlApiClientResponse(status=200, headers={}, body='{"state":{}}'),
        )
        self.assertEqual(missing_mode.value, "0")
        missing_state = cli._doctor_safe_write_namespace(
            safe_namespace,
            "reader",
            ControlApiClientResponse(status=200, headers={}, body='{"kind":"operational"}'),
        )
        self.assertEqual(missing_state.value, "0")

    def test_run_doctor_reports_success_skips_and_failures(self) -> None:
        namespace = argparse.Namespace(
            token="",
            read_token="",
            control_token="",
            safe_write=True,
            compact=True,
            url="http://host",
            unix_socket="",
            timeout=2.0,
        )
        health_client = _FakeClient(_response(body='{"kind":"health"}'))
        stdout = io.StringIO()

        with patch.object(cli, "_client_for_token", side_effect=[health_client]) as client_factory, redirect_stdout(stdout):
            self.assertEqual(cli._run_doctor(namespace), cli.EXIT_OK)
        self.assertEqual([call.args for call in client_factory.call_args_list], [(namespace, "")])

        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"], {"passed": 1, "failed": 0, "skipped": 2})
        self.assertEqual(payload["checks"], [{"kind": "health", "name": "health", "ok": True, "status": 200}])
        self.assertEqual(
            payload["skipped"],
            [
                "authenticated read checks skipped: no read token provided",
                "safe write skipped: no control token provided",
            ],
        )

        failing_namespace = argparse.Namespace(
            token="",
            read_token="read",
            control_token="control",
            safe_write=True,
            compact=True,
            url="http://host",
            unix_socket="",
            timeout=2.0,
        )
        clients = [
            _FakeClient(_response(503, '{"kind":"health"}')),
            _FakeClient(_response(body='{"state":{"mode":1}}', headers={"X-State-Token": "op"})),
            _FakeClient(_response(body='{"state":{"mode":1}}', headers={"X-State-Token": "op"})),
            _FakeClient(_response(body='{"state":{"mode":1}}', headers={"X-State-Token": "op"})),
            _FakeClient(_response(409, '{"kind":"safe-write"}')),
        ]
        stdout = io.StringIO()
        with patch.object(cli, "_client_for_token", side_effect=clients) as client_factory, redirect_stdout(stdout):
            self.assertEqual(cli._run_doctor(failing_namespace), cli.EXIT_REQUEST_FAILED)
        self.assertEqual(
            [call.args for call in client_factory.call_args_list],
            [
                (failing_namespace, ""),
                (failing_namespace, "read"),
                (failing_namespace, "control"),
                (
                    argparse.Namespace(
                        token="",
                        read_token="read",
                        control_token="control",
                        safe_write=True,
                        compact=True,
                        url="http://host",
                        unix_socket="",
                        timeout=2.0,
                        name="set-mode",
                        value="1",
                        path="",
                        detail="doctor-safe-write",
                        command_id="doctor-safe-write",
                        idempotency_key="doctor-safe-write",
                        state_endpoint="health",
                    ),
                    "read",
                ),
                (
                    argparse.Namespace(
                        token="",
                        read_token="read",
                        control_token="control",
                        safe_write=True,
                        compact=True,
                        url="http://host",
                        unix_socket="",
                        timeout=2.0,
                        name="set-mode",
                        value="1",
                        path="",
                        detail="doctor-safe-write",
                        command_id="doctor-safe-write",
                        idempotency_key="doctor-safe-write",
                        state_endpoint="health",
                    ),
                    "control",
                ),
            ],
        )

        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["summary"]["failed"], 2)
        self.assertEqual(payload["checks"][0], {"kind": "health", "name": "health", "ok": False, "status": 503})
        self.assertEqual(payload["checks"][-1], {"kind": "safe-write", "name": "safe-write.set-mode", "ok": False, "status": 409})

    def test_main_dispatches_each_subcommand(self) -> None:
        for subcommand in ("state", "capabilities", "health", "openapi", "doctor", "command", "events", "watch", "safe-write"):
            fake_parser = SimpleNamespace(parse_args=lambda _args=None, name=subcommand: SimpleNamespace(subcommand=name))
            with patch.object(cli, "build_parser", return_value=fake_parser), patch.dict(
                cli.__dict__,
                {
                    f"_run_{subcommand.replace('-', '_')}": MagicMock(return_value=cli.EXIT_OK),
                },
            ):
                self.assertEqual(cli.main([subcommand]), cli.EXIT_OK)

        fake_parser = SimpleNamespace(parse_args=lambda _args=None: SimpleNamespace(subcommand="unknown"))
        with patch.object(cli, "build_parser", return_value=fake_parser):
            with self.assertRaises(SystemExit) as exit_info:
                cli.main([])
        self.assertEqual(exit_info.exception.code, cli.EXIT_USAGE)

        parser = MagicMock()
        parser.parse_args.return_value = SimpleNamespace(subcommand="health")
        with patch.object(cli, "build_parser", return_value=parser), patch.object(cli, "_run_health", return_value=7) as runner:
            self.assertEqual(cli.main(("health", "--compact")), 7)
        parser.parse_args.assert_called_once_with(["health", "--compact"])
        runner.assert_called_once_with(parser.parse_args.return_value)


if __name__ == "__main__":
    unittest.main()
