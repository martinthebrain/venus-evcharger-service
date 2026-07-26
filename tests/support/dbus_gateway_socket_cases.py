# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter binary socket lifecycle scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    patch,
    process_socket_module,
    tempfile,
)
from venus_evcharger.ipc.fast_publication_wire import (
    decode_fast_publication_frame,
    encode_fast_publication_frame,
)
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command


class GatewaySocketCases(GatewayAdapterContractCase):
    """Exercise the only productive socket endpoint."""

    def test_idle_client_is_retained_without_blocking_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            conn = MagicMock()
            conn.recv.side_effect = BlockingIOError
            server = MagicMock()
            server.accept.return_value = (conn, "peer")
            adapter._server = server

            with patch.object(
                process_socket_module.select,
                "select",
                return_value=([server], [], []),
            ):
                adapter.socket_role.process_socket_once()

            conn.setblocking.assert_called_once_with(False)
            conn.sendall.assert_not_called()
            self.assertIsNotNone(adapter.socket_role._pending)

    def test_only_transient_semantic_publication_is_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            live = publish_evcs_fields_command({"ac_power_w": 1.0}, priority="live")

            self.assertTrue(adapter.socket_role.dispatch_socket_payload(live)["ok"])
            self.assertFalse(
                adapter.socket_role.dispatch_socket_payload({"kind": "health"})["ok"]
            )
            self.assertFalse(
                adapter.socket_role.dispatch_socket_payload(
                    {"kind": "refresh_energy_inputs"}
                )["ok"]
            )

    def test_socket_process_sends_binary_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            command = publish_evcs_fields_command({"ac_power_w": 1.0}, priority="live")
            conn = MagicMock()
            conn.recv.return_value = encode_fast_publication_frame(command)
            server = MagicMock()
            server.accept.return_value = (conn, "peer")
            adapter._server = server

            with patch.object(
                process_socket_module.select,
                "select",
                return_value=([server], [], []),
            ):
                adapter.socket_role.process_socket_once()

            response = decode_fast_publication_frame(conn.sendall.call_args.args[0])
            self.assertTrue(response["accepted"])

    def test_socket_lifecycle_creates_and_removes_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            Path(adapter.paths.socket_path).write_text("stale", encoding="utf-8")

            server = MagicMock()
            with patch.object(
                process_socket_module.socket,
                "socket",
                return_value=server,
            ) as socket_factory:
                adapter.socket_role.start_socket()
            socket_factory.assert_called_once_with(
                process_socket_module.socket.AF_UNIX,
                process_socket_module.socket.SOCK_STREAM,
            )
            self.assertFalse(Path(adapter.paths.socket_path).exists())
            adapter.socket_role.close_socket()
            self.assertIsNone(adapter._server)
