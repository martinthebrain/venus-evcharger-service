# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter Unix socket request and lifecycle scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    SocketClientStub,
    SocketServerStub,
    gateway_paths,
    install_mock,
    json,
    patch,
    process_socket_module,
    tempfile,
)
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class GatewaySocketCases(GatewayAdapterContractCase):
    """Exercise Unix socket request and lifecycle scenarios."""

    def test_socket_client_timeout_does_not_block_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            conn = SocketClientStub(error=TimeoutError("idle"))
            server = SocketServerStub(conn)
            adapter._server = server

            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.socket_role.process_socket_once()

            self.assertEqual(len(conn.timeouts), 1)
            self.assertGreater(conn.timeouts[0], 0.0)
            self.assertLessEqual(conn.timeouts[0], 0.1)
            self.assertEqual(conn.sent, [])

    def test_socket_payload_and_socket_poll_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertFalse(adapter.socket_role.handle_socket_payload("{")["ok"])
            self.assertFalse(adapter.socket_role.handle_socket_payload("[]")["ok"])
            self.assertTrue(adapter.socket_role.handle_socket_payload('{"type":"snapshot"}')["ok"])
            self.assertTrue(adapter.socket_role.handle_socket_payload('{"type":"health"}')["ok"])
            enqueue = install_mock(adapter.commands, "enqueue", MagicMock(return_value="queued.json"))
            refresh = EnergyRefreshRequest(
                request_id="socket-grid",
                scope="grid",
                max_age_seconds=2.0,
                urgency="priority",
                reason="socket-test",
            ).to_command(source="socket-test")
            self.assertTrue(adapter.socket_role.handle_socket_payload(json.dumps(refresh))["ok"])
            enqueue.assert_called_once_with(refresh)
            for request_type in (
                "refresh_value",
                "refresh_services",
                "publish_desired",
                "publish_value",
                "set_value",
            ):
                self.assertFalse(adapter.socket_role.handle_socket_payload(json.dumps({"type": request_type}))["ok"])
            self.assertFalse(adapter.socket_role.handle_socket_payload('{"type":"wat"}')["ok"])

            adapter._server = None
            adapter.socket_role.process_socket_once()
            server = SocketServerStub(SocketClientStub(), error=BlockingIOError())
            adapter._server = server
            with patch.object(process_socket_module.select, "select", return_value=([], [], [])):
                adapter.socket_role.process_socket_once()
            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.socket_role.process_socket_once()
            self.assertEqual(server.accept_calls, 1)

    def test_socket_process_sends_json_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            conn = SocketClientStub(b'{"type":"snapshot"}\n')
            server = SocketServerStub(conn)
            adapter._server = server
            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.socket_role.process_socket_once()
            self.assertEqual(len(conn.sent), 1)
            self.assertTrue(json.loads(conn.sent[0].decode("utf-8"))["ok"])

    def test_socket_lifecycle_creates_and_removes_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            Path(adapter.paths.socket_path).write_text("stale", encoding="utf-8")

            server = MagicMock()
            with patch.object(process_socket_module.socket, "socket", return_value=server) as socket_factory:
                adapter.socket_role.start_socket()
            socket_factory.assert_called_once_with(
                process_socket_module.socket.AF_UNIX,
                process_socket_module.socket.SOCK_STREAM,
            )
            server.bind.assert_called_once_with(adapter.paths.socket_path)
            server.listen.assert_called_once_with(8)
            server.setblocking.assert_called_once_with(False)
            self.assertIs(adapter._server, server)
            self.assertFalse(Path(adapter.paths.socket_path).exists())

            adapter.socket_role.close_socket()
            server.close.assert_called_once_with()
            self.assertIsNone(adapter._server)
            adapter.socket_role.close_socket()
