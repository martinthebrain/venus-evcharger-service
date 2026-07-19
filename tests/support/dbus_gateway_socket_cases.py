# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter Unix socket request and lifecycle scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    Path,
    SocketClientStub,
    SocketServerStub,
    gateway_paths,
    json,
    patch,
    process_socket_module,
    tempfile,
)


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
                adapter.process_socket_once()

            self.assertEqual(conn.timeouts, [0.1])
            self.assertEqual(conn.sent, [])

    def test_socket_payload_and_socket_poll_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertFalse(adapter.handle_socket_payload("{")["ok"])
            self.assertFalse(adapter.handle_socket_payload("[]")["ok"])
            self.assertTrue(adapter.handle_socket_payload('{"type":"snapshot"}')["ok"])
            self.assertTrue(adapter.handle_socket_payload('{"type":"health"}')["ok"])
            for request_type in ("refresh_value", "refresh_services", "publish_desired", "publish_value", "set_value"):
                self.assertTrue(adapter.handle_socket_payload(json.dumps({"type": request_type}))["ok"])
            self.assertFalse(adapter.handle_socket_payload('{"type":"wat"}')["ok"])

            adapter._server = None
            adapter.process_socket_once()
            server = SocketServerStub(SocketClientStub(), error=BlockingIOError())
            adapter._server = server
            with patch.object(process_socket_module.select, "select", return_value=([], [], [])):
                adapter.process_socket_once()
            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.process_socket_once()
            self.assertEqual(server.accept_calls, 1)

    def test_socket_process_sends_json_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            conn = SocketClientStub(b'{"type":"snapshot"}')
            server = SocketServerStub(conn)
            adapter._server = server
            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.process_socket_once()
            self.assertEqual(len(conn.sent), 1)
            self.assertTrue(json.loads(conn.sent[0].decode("utf-8"))["ok"])

    def test_socket_lifecycle_creates_and_removes_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            Path(adapter.paths.socket_path).write_text("stale", encoding="utf-8")

            adapter.start_socket()
            self.assertIsNotNone(adapter._server)
            adapter.close_socket()
            self.assertIsNone(adapter._server)
            adapter.close_socket()
