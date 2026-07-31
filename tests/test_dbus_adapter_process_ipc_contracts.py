#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic contracts for the bounded fast-publication socket."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
import venus_evcharger.dbus_adapter.process.socket as socket_module
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.ipc.fast_publication_wire import (
    FAST_PUBLICATION_WIRE_HEADER_BYTES,
    FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
    FAST_PUBLICATION_WIRE_VERSION,
    decode_fast_publication_frame,
    encode_fast_publication_frame,
)
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command


class DbusAdapterIpcContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        config_path = root / "config.ini"
        config_path.write_text("[DEFAULT]\n", encoding="utf-8")
        self.adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(root / "run")))
        self.events = MagicMock()
        self.events.PRIORITY_DEFAULT = 0
        self.events.IO_IN = 1
        self.socket = socket_module.DbusAdapterSocket(self.adapter, events=self.events)

    def test_start_and_close_obey_transport_contract(self) -> None:
        server = MagicMock()
        with (
            patch.object(socket_module.os, "unlink") as unlink,
            patch.object(socket_module.os, "chmod") as chmod,
            patch.object(socket_module.socket, "socket", return_value=server) as socket_factory,
        ):
            self.socket.start_socket()

        unlink.assert_called_once_with(self.adapter.paths.socket_path)
        socket_factory.assert_called_once_with(
            socket_module.socket.AF_UNIX,
            socket_module.socket.SOCK_STREAM,
        )
        server.bind.assert_called_once_with(self.adapter.paths.socket_path)
        chmod.assert_called_once_with(self.adapter.paths.socket_path, 0o600)
        server.listen.assert_called_once_with(socket_module.SOCKET_BACKLOG)
        server.setblocking.assert_called_once_with(False)

        pending = MagicMock()
        self.socket._pending = socket_module._PendingSocketRequest(pending, 1.0, bytearray())
        with patch.object(socket_module.os, "unlink") as unlink:
            self.socket.close_socket()
        pending.close.assert_called_once_with()
        server.close.assert_called_once_with()
        unlink.assert_called_once_with(self.adapter.paths.socket_path)
        self.assertIsNone(self.adapter._server)
        self.assertIsNone(self.socket._pending)

    def test_close_and_start_tolerate_missing_paths(self) -> None:
        self.adapter._server = None
        with patch.object(socket_module.os, "unlink", side_effect=FileNotFoundError):
            self.socket.close_socket()

        server = MagicMock()
        with (
            patch.object(socket_module.os, "unlink", side_effect=FileNotFoundError),
            patch.object(socket_module.os, "chmod"),
            patch.object(socket_module.socket, "socket", return_value=server),
        ):
            self.socket.start_socket()
        server.bind.assert_called_once_with(self.adapter.paths.socket_path)

    def test_start_socket_cleans_up_when_permission_hardening_fails(self) -> None:
        server = MagicMock()
        with (
            patch.object(socket_module.os, "unlink") as unlink,
            patch.object(socket_module.os, "chmod", side_effect=PermissionError("denied")),
            patch.object(socket_module.socket, "socket", return_value=server),
            self.assertRaisesRegex(PermissionError, "denied"),
        ):
            self.socket.start_socket()
        server.close.assert_called_once_with()
        self.assertEqual(unlink.call_count, 2)
        self.assertIsNone(self.adapter._server)

    def test_glib_watch_is_idle_event_driven_and_pending_reads_use_bounded_timer(self) -> None:
        server = MagicMock()
        server.fileno.return_value = 42
        self.adapter._server = server
        with patch.object(self.events, "io_add_watch", return_value=71) as add_watch:
            self.socket.install_glib_watch()
            self.socket.install_glib_watch()
        add_watch.assert_called_once_with(
            42,
            self.events.PRIORITY_DEFAULT,
            self.events.IO_IN,
            self.socket._on_server_ready,
        )

        connection = MagicMock()
        connection.recv.side_effect = BlockingIOError
        server.accept.return_value = (connection, "peer")
        with (
            patch.object(socket_module.select, "select", return_value=([server], [], [])),
            patch.object(self.events, "timeout_add", return_value=72) as timeout_add,
        ):
            self.assertFalse(self.socket._on_server_ready(42, self.events.IO_IN))
        timeout_add.assert_called_once_with(
            socket_module.SOCKET_PENDING_POLL_INTERVAL_MS,
            self.socket._on_pending_timer,
        )
        self.assertIsNotNone(self.socket._pending)

    def test_glib_sources_cover_idle_pending_and_removal_lifecycle(self) -> None:
        self.socket.install_glib_watch()
        self.events.io_add_watch.assert_not_called()

        pending = MagicMock()
        self.socket._pending = socket_module._PendingSocketRequest(
            pending,
            1.0,
            bytearray(),
        )
        with (
            patch.object(self.socket, "_process_socket_safely") as process,
            patch.object(self.socket, "_install_pending_timer") as install_timer,
        ):
            self.assertFalse(self.socket._on_pending_timer())
        process.assert_called_once_with()
        install_timer.assert_called_once_with()

        self.socket._pending = None
        with (
            patch.object(self.socket, "_process_socket_safely") as process,
            patch.object(self.socket, "install_glib_watch") as install_watch,
        ):
            self.assertFalse(self.socket._on_pending_timer())
        process.assert_called_once_with()
        install_watch.assert_called_once_with()

        self.socket._pending_timer_id = 72
        self.socket._install_pending_timer()
        self.events.timeout_add.assert_not_called()
        self.socket._remove_glib_source("_pending_timer_id")
        self.events.source_remove.assert_called_once_with(72)
        self.assertIsNone(self.socket._pending_timer_id)

    def test_glib_ipc_error_domain_logs_without_touching_dbus_circuit(self) -> None:
        error = OSError("local IPC failed")
        with (
            patch.object(self.socket, "process_socket_once", side_effect=error),
            patch.object(socket_module.logging, "exception") as log_exception,
            patch.object(self.adapter.circuit, "record_error") as record_error,
        ):
            self.socket._process_socket_safely()

        record_error.assert_not_called()
        log_exception.assert_called_once_with("Gateway IPC event failed: %s", error)

    def test_protective_gateway_services_fast_socket_without_dbus_work(self) -> None:
        command = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        connection = MagicMock()
        connection.recv.return_value = encode_fast_publication_frame(command)
        server = MagicMock()
        server.accept.return_value = (connection, "peer")
        self.adapter._server = server
        self.adapter.circuit.protective_until = socket_module.time.time() + 10.0

        with (
            patch.object(socket_module.select, "select", return_value=([server], [], [])),
            patch.object(
                self.adapter.loop_role,
                "process_one_dbus_operation_once",
            ) as dbus_work,
        ):
            self.assertFalse(self.socket._on_server_ready(42, self.events.IO_IN))

        response = decode_fast_publication_frame(connection.sendall.call_args.args[0])
        self.assertTrue(response["accepted"])
        dbus_work.assert_not_called()
        self.assertIsNone(self.socket._pending)

    def test_poll_is_nonblocking_for_idle_and_accept_races(self) -> None:
        self.socket._service_pending_request()

        self.adapter._server = None
        with patch.object(socket_module.select, "select", side_effect=AssertionError):
            self.socket.process_socket_once()

        server = MagicMock()
        self.adapter._server = server
        with patch.object(socket_module.select, "select", return_value=([], [], [])):
            self.socket.process_socket_once()
        server.accept.assert_not_called()

        with patch.object(socket_module.select, "select", return_value=([server], [], [])):
            server.accept.side_effect = BlockingIOError
            self.socket.process_socket_once()

    def test_fragmented_request_is_retained_without_blocking_mainloop(self) -> None:
        command = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        frame = encode_fast_publication_frame(command)
        split = len(frame) // 2
        connection = MagicMock()
        connection.recv.side_effect = [frame[:split], BlockingIOError, frame[split:]]
        server = MagicMock()
        server.accept.return_value = (connection, "peer")
        self.adapter._server = server

        with patch.object(socket_module.select, "select", return_value=([server], [], [])):
            self.socket.process_socket_once()
        connection.setblocking.assert_called_once_with(False)
        connection.sendall.assert_not_called()
        self.assertIsNotNone(self.socket._pending)

        self.socket.process_socket_once()

        response = decode_fast_publication_frame(connection.sendall.call_args.args[0])
        self.assertTrue(response["accepted"])
        connection.close.assert_called_once_with()
        self.assertIsNone(self.socket._pending)

    def test_pending_request_times_out_without_touching_dbus(self) -> None:
        connection = MagicMock()
        connection.recv.side_effect = BlockingIOError
        self.socket._pending = socket_module._PendingSocketRequest(
            connection,
            1.0,
            bytearray(),
        )
        with patch.object(socket_module.time, "monotonic", return_value=1.001):
            self.socket.process_socket_once()

        response = decode_fast_publication_frame(connection.sendall.call_args.args[0])
        self.assertEqual(response, {"ok": False, "error": "request-timeout"})
        self.assertIsNone(self.socket._pending)

    def test_completed_request_is_closed_when_local_dispatch_fails(self) -> None:
        connection = MagicMock()
        connection.recv.return_value = encode_fast_publication_frame(
            publish_evcs_fields_command({"mode": 1}, priority="live")
        )
        self.socket._pending = socket_module._PendingSocketRequest(
            connection,
            1.0,
            bytearray(),
        )

        with (
            patch.object(
                self.socket,
                "handle_socket_frame",
                side_effect=RuntimeError("local queue failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "local queue failed"),
        ):
            self.socket.process_socket_once()

        connection.close.assert_called_once_with()
        self.assertIsNone(self.socket._pending)

    def test_dispatch_rejects_dead_and_malformed_socket_api(self) -> None:
        self.assertEqual(
            self.socket.dispatch_socket_payload({"kind": "health"}),
            {"ok": False, "error": "unsupported request type: health"},
        )
        self.assertEqual(
            self.socket.dispatch_socket_payload({}),
            {"ok": False, "error": "unsupported request type: "},
        )
        self.assertEqual(
            self.socket.handle_socket_frame(None),
            {"ok": False, "error": "request-incomplete"},
        )
        self.assertEqual(
            self.socket.handle_socket_frame(b"not-a-frame"),
            {"ok": False, "error": "invalid-frame-magic"},
        )

    def test_reader_rejects_eof_invalid_header_and_trailing_bytes(self) -> None:
        connection = MagicMock()
        connection.recv.return_value = b""
        self.assertEqual(
            socket_module.receive_socket_request(connection),
            (None, {"ok": False, "error": "request-incomplete"}),
        )

        connection.recv.return_value = b"invalid!!"
        self.assertEqual(
            socket_module.receive_socket_request(connection),
            (None, {"ok": False, "error": "invalid-frame-magic"}),
        )

        frame = encode_fast_publication_frame({"kind": "publish_evcs_fields"})
        connection.recv.return_value = frame + b"x"
        self.assertEqual(
            socket_module.receive_socket_request(connection),
            (None, {"ok": False, "error": "request-too-large"}),
        )

    def test_reader_rejects_declared_oversize_before_allocating_body(self) -> None:
        header = (
            b"EVCF"
            + bytes((FAST_PUBLICATION_WIRE_VERSION,))
            + (FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES + 1).to_bytes(4, "big")
        )
        self.assertEqual(len(header), FAST_PUBLICATION_WIRE_HEADER_BYTES)
        connection = MagicMock()
        connection.recv.return_value = header
        self.assertEqual(
            socket_module.receive_socket_request(connection),
            (None, {"ok": False, "error": "frame-too-large"}),
        )

    def test_response_disconnects_are_bounded(self) -> None:
        for error in (BlockingIOError, BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error):
                connection = MagicMock()
                connection.sendall.side_effect = error
                with patch.object(socket_module.logging, "debug") as debug:
                    socket_module.send_socket_response(connection, {"ok": True})
                debug.assert_called_once_with(
                    "Gateway socket client disconnected before reading its response"
                )

        connection = MagicMock()
        with patch.object(
            socket_module,
            "encode_fast_publication_frame",
            side_effect=socket_module.FastPublicationWireError("bad"),
        ), patch.object(socket_module.logging, "debug") as debug:
            socket_module.send_socket_response(connection, {"ok": True})
        debug.assert_called_once()


if __name__ == "__main__":
    unittest.main()
