# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast lifecycle contracts for the local Control API transport adapter."""

from __future__ import annotations

import unittest
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.control_api_http_cases_common import ControlApiHttpServiceHarness, control_api_http_service
from venus_evcharger.control.http_api import LocalControlApiHttpServer
from venus_evcharger.control.http_api_command_payloads import tracked_command
from venus_evcharger.control.models import ControlCommand, ControlResult


class ControlHttpLifecycleContractTests(unittest.TestCase):
    def _service(self) -> ControlApiHttpServiceHarness:
        return control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
            capabilities_payload=MagicMock(return_value={}),
        )

    def test_constructor_normalizes_every_option_and_initializes_runtime_state(self) -> None:
        service = self._service()
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.7",
            port="8123",
            auth_token=" auth ",
            read_token=" read ",
            control_token=" control ",
            admin_token=" admin ",
            update_token=" update ",
            localhost_only=False,
            unix_socket_path=" /tmp/control.sock ",
        )
        self.assertIs(server._service, service)
        self.assertEqual(server._host, "127.0.0.7")
        self.assertEqual(server._port, 8123)
        self.assertEqual(
            server.authenticator.scope_tokens(),
            (("update_admin", "update"), ("control_admin", "admin"), ("control_basic", "control"), ("read", "read")),
        )
        self.assertFalse(server._localhost_only)
        self.assertEqual(server._unix_socket_path, "/tmp/control.sock")
        self.assertIsNone(server._server)
        self.assertIsNone(server._thread)
        self.assertIsNotNone(server.idempotency)
        self.assertIsNotNone(server.rate_limit)
        self.assertEqual((server.bound_host, server.bound_port, server.bound_unix_socket_path), ("", 0, ""))

        defaults = LocalControlApiHttpServer(service, host="host", port=1)
        self.assertEqual(
            (
                defaults.authenticator.effective_read_token,
                defaults.authenticator.effective_control_token,
                defaults.authenticator.effective_admin_token,
                defaults.authenticator.effective_update_token,
                defaults._localhost_only,
                defaults._unix_socket_path,
            ),
            ("", "", "", "", True, ""),
        )

    def test_bound_host_port_accepts_only_network_address_tuples(self) -> None:
        self.assertEqual(
            LocalControlApiHttpServer.bound_host_port(SimpleNamespace(server_address=("host", "17"))),
            ("host", 17),
        )
        self.assertEqual(
            LocalControlApiHttpServer.bound_host_port(SimpleNamespace(server_address=("only-one",))),
            ("", 0),
        )
        self.assertEqual(
            LocalControlApiHttpServer.bound_host_port(SimpleNamespace(server_address="/tmp/unix")),
            ("", 0),
        )

    def test_tcp_start_and_stop_have_exact_lifecycle(self) -> None:
        server = LocalControlApiHttpServer(self._service(), host="127.0.0.1", port=0)
        transport = MagicMock(server_address=("127.0.0.1", 4321))
        thread = MagicMock()
        with (
            patch.object(server, "_build_server", return_value=transport) as build,
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=thread) as thread_factory,
            patch("venus_evcharger.control.http_api.logging.info") as info,
        ):
            server.start()
            self.assertIs(server._server, transport)
            self.assertIs(server._thread, thread)
            self.assertEqual((server.bound_host, server.bound_port, server.bound_unix_socket_path), ("127.0.0.1", 4321, ""))
            server.start()
            server.stop()
        build.assert_called_once_with()
        thread_factory.assert_called_once_with(
            target=transport.serve_forever,
            name="venus-evcharger-control-api",
            daemon=True,
        )
        thread.start.assert_called_once_with()
        info.assert_called_once_with("Started local Control API v1 on %s", "http://127.0.0.1:4321")
        transport.shutdown.assert_called_once_with()
        transport.server_close.assert_called_once_with()
        thread.join.assert_called_once_with(timeout=1.0)
        self.assertIsNone(server._server)
        self.assertIsNone(server._thread)
        self.assertEqual((server.bound_host, server.bound_port, server.bound_unix_socket_path), ("", 0, ""))

    def test_unix_transport_start_and_stop_remove_the_bound_path(self) -> None:
        server = LocalControlApiHttpServer(
            self._service(), host="ignored", port=99, unix_socket_path="/tmp/control.sock"
        )
        transport = MagicMock(server_address="/tmp/control.sock")
        thread = MagicMock()
        with (
            patch.object(server, "_build_server", return_value=transport),
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=thread),
            patch("venus_evcharger.control.http_api.logging.info") as info,
            patch.object(server, "secure_unix_socket_path") as secure_socket,
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True) as exists,
            patch("venus_evcharger.control.http_api.os.unlink") as unlink,
        ):
            server.start()
            self.assertEqual((server.bound_host, server.bound_port), ("", 0))
            self.assertEqual(server.bound_unix_socket_path, "/tmp/control.sock")
            server.stop()
        info.assert_called_once_with("Started local Control API v1 on %s", "unix:///tmp/control.sock")
        secure_socket.assert_called_once_with("/tmp/control.sock")
        exists.assert_called_once_with("/tmp/control.sock")
        unlink.assert_called_once_with("/tmp/control.sock")

    def test_transport_security_rejects_tokenless_remote_tcp_before_binding(self) -> None:
        for host in ("0.0.0.0", "::", "192.0.2.10", "external.example"):
            with self.subTest(host=host):
                server = LocalControlApiHttpServer(self._service(), host=host, port=8765, localhost_only=False)
                with patch.object(server, "_build_server") as build:
                    with self.assertRaisesRegex(ValueError, "outside loopback require an authentication token"):
                        server.start()
                build.assert_not_called()

    def test_transport_security_allows_local_unix_and_authenticated_remote_listeners(self) -> None:
        for host in ("localhost", "127.0.0.1", "127.0.0.9", "::1"):
            with self.subTest(host=host):
                LocalControlApiHttpServer(self._service(), host=host, port=8765).validate_transport_security()
        LocalControlApiHttpServer(
            self._service(), host="0.0.0.0", port=8765, auth_token="secret", localhost_only=False
        ).validate_transport_security()
        LocalControlApiHttpServer(
            self._service(), host="ignored", port=0, unix_socket_path="/run/control.sock"
        ).validate_transport_security()

    def test_unix_socket_security_uses_owner_only_permissions(self) -> None:
        with patch("venus_evcharger.control.http_api.os.chmod") as chmod:
            LocalControlApiHttpServer.secure_unix_socket_path("/run/control.sock")
        chmod.assert_called_once_with("/run/control.sock", 0o600)

    def test_stop_without_server_resets_all_public_bind_state(self) -> None:
        server = LocalControlApiHttpServer(self._service(), host="host", port=4)
        server.bound_host = "old"
        server.bound_port = 5
        server.bound_unix_socket_path = "/tmp/old.sock"
        server.stop()
        self.assertEqual((server.bound_host, server.bound_port, server.bound_unix_socket_path), ("", 0, ""))

    def test_health_payload_is_the_exact_normalized_transport_contract(self) -> None:
        server = LocalControlApiHttpServer(
            self._service(),
            host="127.0.0.5",
            port=9000,
            read_token="read",
            control_token="control",
            localhost_only=False,
            unix_socket_path="/tmp/configured.sock",
        )
        self.assertEqual(
            server.health_payload(),
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "listen_host": "127.0.0.5",
                "listen_port": 9000,
                "auth_required": True,
                "read_auth_required": True,
                "control_auth_required": True,
                "localhost_only": False,
                "unix_socket_path": "/tmp/configured.sock",
            },
        )
        server.bound_host = "127.0.0.9"
        server.bound_port = 9100
        server.bound_unix_socket_path = "/tmp/bound.sock"
        payload = server.health_payload()
        self.assertEqual(payload["listen_host"], "127.0.0.9")
        self.assertEqual(payload["listen_port"], 9100)
        self.assertEqual(payload["unix_socket_path"], "/tmp/bound.sock")

    def test_health_payload_passes_the_exact_raw_contract_to_normalization(self) -> None:
        server = LocalControlApiHttpServer(
            self._service(), host="configured", port=90, read_token="read", localhost_only=False
        )
        sentinel = {"sentinel": True}
        with patch(
            "venus_evcharger.control.http_api.normalized_control_api_health_fields",
            return_value=sentinel,
        ) as normalize:
            self.assertIs(server.health_payload(), sentinel)
        normalize.assert_called_once_with(
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "listen_host": "configured",
                "listen_port": 90,
                "auth_required": True,
                "read_auth_required": True,
                "control_auth_required": False,
                "localhost_only": False,
                "unix_socket_path": "",
            }
        )

    def test_capabilities_payload_normalizes_the_service_contract_once(self) -> None:
        service = self._service()
        raw = {"ok": True, "api_version": "v1"}
        service.capabilities_payload.return_value = raw
        server = LocalControlApiHttpServer(service, host="host", port=1)
        sentinel = {"normalized": True}
        with patch(
            "venus_evcharger.control.http_api.normalized_control_api_capabilities_fields",
            return_value=sentinel,
        ) as normalize:
            self.assertIs(server.capabilities_payload(), sentinel)
        service.capabilities_payload.assert_called_once_with()
        normalize.assert_called_once_with(raw)

    def test_execute_payload_preserves_tracking_or_rebuilds_it_exactly_once(self) -> None:
        service = self._service()
        server = LocalControlApiHttpServer(service, host="localhost", port=1)
        tracked = ControlCommand(
            name="set_mode", path="/Mode", value=1, source="http", command_id="c", idempotency_key="i"
        )
        result = ControlResult.applied_result(tracked)
        service.control_command_from_payload.return_value = tracked
        service.handle_control_command.return_value = result
        payload = {"name": "set_mode", "value": 1, "idempotency_key": "i"}
        with patch("venus_evcharger.control.http_api_commands.tracked_command", wraps=tracked_command) as rebuild:
            self.assertEqual(server.execute_payload(payload), (tracked, result))
        rebuild.assert_not_called()
        service.control_command_from_payload.assert_called_once_with(payload, source="http")
        service.handle_control_command.assert_called_once_with(tracked)

        untracked = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        replacement = ControlCommand(
            name="set_mode", path="/Mode", value=1, source="http", command_id="new", idempotency_key="new-i"
        )
        replacement_result = ControlResult.applied_result(replacement)
        service.control_command_from_payload.return_value = untracked
        service.handle_control_command.return_value = replacement_result
        with patch("venus_evcharger.control.http_api_commands.tracked_command", return_value=replacement) as rebuild:
            self.assertEqual(server.execute_payload({"idempotency_key": "new-i"}), (replacement, replacement_result))
        rebuild.assert_called_once_with({"idempotency_key": "new-i"}, untracked)
        service.handle_control_command.assert_called_with(replacement)

        command_id_only = ControlCommand(
            name="set_mode", path="/Mode", value=1, source="http", command_id="existing"
        )
        service.control_command_from_payload.return_value = command_id_only
        with patch("venus_evcharger.control.http_api_commands.tracked_command", return_value=replacement) as rebuild:
            server.execute_payload({"idempotency_key": "different"})
        rebuild.assert_called_once_with({"idempotency_key": "different"}, command_id_only)

        for idempotency_key in ("None", "XXXX"):
            command_with_default_sentinel = ControlCommand(
                name="set_mode",
                path="/Mode",
                value=1,
                source="http",
                command_id="existing",
                idempotency_key=idempotency_key,
            )
            service.control_command_from_payload.return_value = command_with_default_sentinel
            with self.subTest(idempotency_key=idempotency_key), patch(
                "venus_evcharger.control.http_api_commands.tracked_command", return_value=replacement
            ) as rebuild:
                server.execute_payload({})
            rebuild.assert_called_once_with({}, command_with_default_sentinel)

    def test_server_factory_selects_exact_transport_and_handler(self) -> None:
        tcp = LocalControlApiHttpServer(self._service(), host="127.0.0.2", port=8124)
        handler_class = type("Handler", (BaseHTTPRequestHandler,), {})
        transport = MagicMock()
        with (
            patch.object(tcp, "_handler_class", return_value=handler_class) as handler,
            patch("venus_evcharger.control.http_api._ThreadingLocalControlHttpServer", return_value=transport) as factory,
        ):
            self.assertIs(tcp._build_server(), transport)
        handler.assert_called_once_with()
        factory.assert_called_once_with(("127.0.0.2", 8124), handler_class)

        unix = LocalControlApiHttpServer(
            self._service(), host="ignored", port=0, unix_socket_path="/tmp/control.sock"
        )
        with (
            patch.object(unix, "_handler_class", return_value=handler_class),
            patch.object(unix, "prepare_unix_socket_path") as prepare,
            patch("venus_evcharger.control.http_api._ThreadingLocalControlUnixHttpServer", return_value=transport) as factory,
        ):
            self.assertIs(unix._build_server(), transport)
        prepare.assert_called_once_with("/tmp/control.sock")
        factory.assert_called_once_with("/tmp/control.sock", handler_class)

    def test_unix_path_preparation_has_exact_file_type_policy(self) -> None:
        with patch("venus_evcharger.control.http_api.os.path.exists", return_value=False) as exists:
            LocalControlApiHttpServer.prepare_unix_socket_path("/tmp/missing.sock")
        exists.assert_called_once_with("/tmp/missing.sock")

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=123)) as stat_call,
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=True) as is_socket,
            patch("venus_evcharger.control.http_api.os.unlink") as unlink,
        ):
            LocalControlApiHttpServer.prepare_unix_socket_path("/tmp/socket")
        stat_call.assert_called_once_with("/tmp/socket")
        is_socket.assert_called_once_with(123)
        unlink.assert_called_once_with("/tmp/socket")

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=456)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=False),
        ):
            with self.assertRaises(ValueError) as raised:
                LocalControlApiHttpServer.prepare_unix_socket_path("/tmp/file")
        self.assertEqual(
            str(raised.exception),
            "Control API unix socket path already exists and is not a socket: /tmp/file",
        )

    def test_generated_handler_delegates_get_post_and_log_calls(self) -> None:
        server = LocalControlApiHttpServer(self._service(), host="host", port=1)
        with (
            patch.object(server.router, "handle_get") as get,
            patch.object(server.router, "handle_post") as post,
            patch("venus_evcharger.control.http_api.logging.debug") as debug,
        ):
            handler_type = server._handler_class()
            handler = object.__new__(handler_type)
            handler.do_GET()
            handler.do_POST()
            handler.log_message("value=%s", 7)
        get.assert_called_once_with(handler)
        post.assert_called_once_with(handler)
        debug.assert_called_once_with("Control API HTTP: value=%s", 7)


if __name__ == "__main__":
    unittest.main()
