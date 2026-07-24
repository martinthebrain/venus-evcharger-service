#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic contracts for the gateway Unix-socket boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
import venus_evcharger.dbus_adapter.process.socket as socket_module
from venus_evcharger.dbus_gateway import gateway_paths


class DbusAdapterIpcContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        config_path = root / "config.ini"
        config_path.write_text("[DEFAULT]\n", encoding="utf-8")
        self.adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(root / "run")))
        self.socket = self.adapter.socket_role

    def test_start_and_close_obey_transport_contract(self) -> None:
        server = MagicMock()
        with (
            patch.object(socket_module.os, "unlink") as unlink,
            patch.object(socket_module.socket, "socket", return_value=server) as socket_factory,
        ):
            self.socket.start_socket()

        unlink.assert_called_once_with(self.adapter.paths.socket_path)
        socket_factory.assert_called_once_with(socket_module.socket.AF_UNIX, socket_module.socket.SOCK_STREAM)
        server.bind.assert_called_once_with(self.adapter.paths.socket_path)
        server.listen.assert_called_once_with(8)
        server.setblocking.assert_called_once_with(False)
        self.assertIs(self.adapter._server, server)

        with patch.object(socket_module.os, "unlink") as unlink:
            self.socket.close_socket()
        server.close.assert_called_once_with()
        unlink.assert_called_once_with(self.adapter.paths.socket_path)
        self.assertIsNone(self.adapter._server)

    def test_close_tolerates_missing_transport_and_path(self) -> None:
        self.adapter._server = None
        with patch.object(socket_module.os, "unlink", side_effect=FileNotFoundError):
            self.socket.close_socket()
        self.assertIsNone(self.adapter._server)

    def test_start_tolerates_missing_stale_path(self) -> None:
        server = MagicMock()
        with (
            patch.object(socket_module.os, "unlink", side_effect=FileNotFoundError),
            patch.object(socket_module.socket, "socket", return_value=server),
        ):
            self.socket.start_socket()
        server.bind.assert_called_once_with(self.adapter.paths.socket_path)
        self.assertIs(self.adapter._server, server)

    def test_ipc_poll_handles_idle_race_and_timeout(self) -> None:
        self.adapter._server = None
        with patch.object(socket_module.select, "select", side_effect=AssertionError("must not poll")):
            self.socket.process_socket_once()

        server = MagicMock()
        self.adapter._server = server
        with patch.object(socket_module.select, "select", return_value=([], [], [])) as select_call:
            self.socket.process_socket_once()
        select_call.assert_called_once_with([server], [], [], 0.0)
        server.accept.assert_not_called()

        with patch.object(socket_module.select, "select", return_value=([server], [], [])):
            server.accept.side_effect = BlockingIOError
            self.socket.process_socket_once()

        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = TimeoutError
        server.accept.side_effect = None
        server.accept.return_value = (connection, "peer")
        with (
            patch.object(socket_module.select, "select", return_value=([server], [], [])),
            patch.object(socket_module.logging, "debug") as debug,
        ):
            self.socket.process_socket_once()
        connection.settimeout.assert_called_once()
        self.assertGreater(connection.settimeout.call_args.args[0], 0.0)
        self.assertLessEqual(connection.settimeout.call_args.args[0], 0.1)
        connection.recv.assert_called_once_with(socket_module.SOCKET_READ_CHUNK_BYTES)
        connection.sendall.assert_not_called()
        debug.assert_called_once_with("Gateway socket client connected without sending a request")

    def test_ipc_poll_serializes_exact_response(self) -> None:
        server = MagicMock()
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b"  {\"type\":\"snapshot\"}  \n"
        server.accept.return_value = (connection, "peer")
        self.adapter._server = server
        with (
            patch.object(socket_module.select, "select", return_value=([server], [], [])),
            patch.object(self.socket, "handle_socket_payload", return_value={"ok": True}) as handle,
        ):
            self.socket.process_socket_once()
        handle.assert_called_once_with('{"type":"snapshot"}')
        connection.settimeout.assert_called_once()
        self.assertGreater(connection.settimeout.call_args.args[0], 0.0)
        self.assertLessEqual(connection.settimeout.call_args.args[0], 0.1)
        connection.sendall.assert_called_once_with(b'{"ok":true}\n')

        connection.recv.return_value = b"\xff\n"
        with (
            patch.object(socket_module.select, "select", return_value=([server], [], [])),
            patch.object(self.socket, "handle_socket_payload", return_value={"ok": True}),
        ):
            self.socket.process_socket_once()

        connection.recv.return_value = b"x" * (socket_module.SOCKET_REQUEST_BYTES + 1)
        connection.sendall.reset_mock()
        with patch.object(socket_module.select, "select", return_value=([server], [], [])):
            self.socket.process_socket_once()
        connection.sendall.assert_called_once_with(b'{"error":"request-too-large","ok":false}\n')

    def test_fragmented_request_is_read_until_newline(self) -> None:
        connection = MagicMock()
        connection.recv.side_effect = [b'{"type":', b'"snapshot"', b"}\n"]

        data, error = socket_module.receive_socket_request(connection)

        self.assertEqual(data, '{"type":"snapshot"}')
        self.assertIsNone(error)
        self.assertEqual(connection.recv.call_count, 3)
        self.assertEqual(connection.settimeout.call_count, 3)

    def test_partial_request_deadline_returns_explicit_error(self) -> None:
        connection = MagicMock()
        connection.recv.side_effect = [b'{"type":', TimeoutError]

        data, error = socket_module.receive_socket_request(connection)

        self.assertEqual(data, "")
        self.assertEqual(error, {"ok": False, "error": "request-timeout"})

    def test_elapsed_deadline_and_clean_eof_do_not_block(self) -> None:
        connection = MagicMock()
        with (
            patch.object(socket_module.time, "monotonic", side_effect=[1.0, 2.0]),
            patch.object(socket_module.logging, "debug") as debug,
        ):
            self.assertEqual(socket_module.receive_socket_request(connection), ("", {}))
        connection.recv.assert_not_called()
        debug.assert_called_once_with("Gateway socket client connected without sending a request")

        connection.reset_mock()
        connection.recv.return_value = b""
        self.assertEqual(socket_module.receive_socket_request(connection), ("", None))
        connection.recv.assert_called_once()

    def test_payload_parser_and_dispatch_contracts(self) -> None:
        payload, error = socket_module.parsed_socket_payload('{"type":"health","count":2}')
        self.assertEqual(payload, {"type": "health", "count": 2})
        self.assertEqual(error, "")
        self.assertEqual(socket_module.parsed_socket_payload("[]"), ({}, "request must be an object"))
        invalid_payload, invalid_error = socket_module.parsed_socket_payload("{")
        try:
            json.loads("{")
        except json.JSONDecodeError as error:
            expected_error = str(error)
        self.assertEqual(invalid_payload, {})
        self.assertEqual(invalid_error, expected_error)

        self.assertEqual(self.socket.handle_socket_payload("[]"), {"ok": False, "error": "request must be an object"})
        with patch.object(self.socket, "dispatch_socket_payload", return_value={"ok": True}) as dispatch:
            self.assertEqual(self.socket.handle_socket_payload('{"type":"health"}'), {"ok": True})
        dispatch.assert_called_once_with({"type": "health"})
        with patch.object(self.socket, "socket_health", return_value={"ok": True, "dbus_health": {}}) as health:
            self.assertEqual(self.socket.dispatch_socket_payload({"kind": "health"}), {"ok": True, "dbus_health": {}})
        health.assert_called_once_with({"kind": "health"}, "health")
        self.assertEqual(
            self.socket.dispatch_socket_payload({"type": "unknown"}),
            {"ok": False, "error": "unsupported request type: unknown"},
        )
        self.assertEqual(
            self.socket.dispatch_socket_payload({}),
            {"ok": False, "error": "unsupported request type: "},
        )

        self.assertEqual(
            set(self.socket.socket_handlers()),
            {
                "snapshot",
                "health",
                "refresh_energy_inputs",
                "publish_evcs_fields",
                "publish_companion_fields",
            },
        )

    def test_snapshot_health_and_enqueue_preserve_payload_contracts(self) -> None:
        with patch.object(self.adapter.cache, "snapshot", return_value={"sequence": 7}) as snapshot:
            self.assertEqual(self.socket.socket_snapshot({}, "snapshot"), {"ok": True, "snapshot": {"sequence": 7}})
        snapshot.assert_called_once_with()
        with patch.object(self.adapter.health_role, "health_snapshot", return_value={"state": "ok"}) as health:
            self.assertEqual(self.socket.socket_health({}, "health"), {"ok": True, "dbus_health": {"state": "ok"}})
        health.assert_called_once_with()

        with patch.object(self.adapter.commands, "enqueue") as enqueue:
            self.assertEqual(
                self.socket.socket_enqueue({"type": "set_value", "source": "ui", "value": 2}, "set_value"),
                {"ok": True},
            )
        enqueue.assert_called_once_with(
            {"type": "set_value", "source": "ui", "value": 2, "kind": "set_value"}
        )
        with patch.object(self.adapter.commands, "enqueue") as enqueue:
            self.assertEqual(self.socket.socket_enqueue({"value": 3}, "set_value"), {"ok": True})
        enqueue.assert_called_once_with({"value": 3, "kind": "set_value", "source": "socket"})


if __name__ == "__main__":
    unittest.main()
