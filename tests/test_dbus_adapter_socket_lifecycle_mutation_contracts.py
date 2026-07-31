#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation contracts for the gateway socket lifecycle and event loop."""

from __future__ import annotations

import select
import socket
import tempfile
import time
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

import venus_evcharger.dbus_adapter.process.socket as socket_module
from tests.support.dbus_adapter_socket import (
    ConnectionProbe,
    EventLoopProbe,
    ServerProbe,
    SocketContext,
    pending_connection,
)
from venus_evcharger.dbus_adapter.process.protocols.runtime import DbusAdapterSocketContext
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.ipc.fast_publication_wire import (
    decode_fast_publication_frame,
    encode_fast_publication_frame,
)
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command


class DbusAdapterSocketLifecycleMutationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.context = SocketContext(gateway_paths(str(root)))
        self.events = EventLoopProbe()
        self.role = socket_module.DbusAdapterSocket(
            cast(DbusAdapterSocketContext, self.context),
            events=self.events,
        )

    def test_lifecycle_owns_exactly_one_nonblocking_server_and_all_sources(self) -> None:
        server = ServerProbe()
        socket_path = self.context.paths.socket_path
        Path(socket_path).write_text("stale", encoding="utf-8")
        with (
            patch.object(
                socket,
                "socket",
                return_value=cast(socket.socket, server),
            ) as socket_factory,
            patch.object(socket_module.os, "chmod") as chmod,
        ):
            self.role.start_socket()

        socket_factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        self.assertEqual(server.bind_calls, [socket_path])
        self.assertEqual(server.listen_calls, [socket_module.SOCKET_BACKLOG])
        self.assertEqual(server.blocking, [False])
        chmod.assert_called_once_with(socket_path, 0o600)
        self.assertIs(self.context._server, server)
        self.assertFalse(Path(socket_path).exists())

        self.role.install_glib_watch()
        self.role.install_glib_watch()
        self.assertEqual(
            self.events.watch_calls,
            [(41, 17, 23, self.role._on_server_ready)],
        )
        self.assertEqual(self.role._server_watch_id, 101)

        pending = ConnectionProbe()
        self.role._pending = socket_module._PendingSocketRequest(
            cast(socket.socket, pending),
            9.0,
            bytearray(b"partial"),
        )
        self.role._pending_timer_id = 102
        self.role.close_socket()

        self.assertEqual(self.events.removed, [101, 102])
        self.assertIsNone(self.role._server_watch_id)
        self.assertIsNone(self.role._pending_timer_id)
        self.assertEqual(pending.close_count, 1)
        self.assertEqual(server.close_count, 1)
        self.assertIsNone(self.role._pending)
        self.assertIsNone(self.context._server)

    def test_accept_contract_is_zero_wait_nonblocking_and_race_safe(self) -> None:
        server = ServerProbe()
        with patch.object(
            select,
            "select",
            return_value=([], [], []),
        ) as select_call:
            self.assertIsNone(socket_module.accept_socket_connection(cast(socket.socket, server)))
        select_call.assert_called_once_with(
            [cast(socket.socket, server)],
            [],
            [],
            0.0,
        )
        self.assertEqual(server.accept_count, 0)

        racing = ServerProbe(accept_error=BlockingIOError())
        with patch.object(
            select,
            "select",
            return_value=([racing], [], []),
        ):
            self.assertIsNone(socket_module.accept_socket_connection(cast(socket.socket, racing)))
        self.assertEqual(racing.accept_count, 1)

        connection = ConnectionProbe()
        ready = ServerProbe(connection)
        with patch.object(
            select,
            "select",
            return_value=([ready], [], []),
        ):
            accepted = socket_module.accept_socket_connection(cast(socket.socket, ready))
        self.assertIs(accepted, connection)
        self.assertEqual(ready.accept_count, 1)
        self.assertEqual(connection.blocking, [False])

    def test_process_accepts_once_and_preserves_incomplete_request_deadline(self) -> None:
        connection = ConnectionProbe(BlockingIOError())
        server = ServerProbe(connection)
        self.context._server = cast(socket.socket, server)
        with (
            patch.object(
                select,
                "select",
                return_value=([server], [], []),
            ),
            patch.object(time, "monotonic", return_value=50.0),
        ):
            self.assertIs(self.role.process_socket_once(), True)

        pending = self.role._pending
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertIs(pending.conn, connection)
        self.assertEqual(
            pending.deadline,
            50.0 + socket_module.SOCKET_REQUEST_DEADLINE_SECONDS,
        )
        self.assertEqual(pending.received, bytearray())
        self.assertEqual(server.accept_count, 1)
        self.assertEqual(connection.blocking, [False])

        with patch.object(select, "select", side_effect=AssertionError):
            connection._reads.append(BlockingIOError())
            with patch.object(time, "monotonic", return_value=50.01):
                self.assertIs(self.role.process_socket_once(), True)
        self.assertEqual(server.accept_count, 1)
        self.assertIs(self.role._pending, pending)

    def test_process_idle_and_pending_none_contracts_return_exact_false(self) -> None:
        self.assertIs(self.role.process_socket_once(), False)
        self.assertIs(self.role._service_pending_request(), False)

        server = ServerProbe()
        self.context._server = cast(socket.socket, server)
        with patch.object(
            select,
            "select",
            return_value=([], [], []),
        ):
            self.assertIs(self.role.process_socket_once(), False)
        self.assertEqual(server.accept_count, 0)

    def test_completed_request_clears_pending_before_dispatch_and_always_closes(self) -> None:
        command = publish_evcs_fields_command({"mode": 2}, priority="live")
        connection = ConnectionProbe(encode_fast_publication_frame(command))
        self.role._pending = socket_module._PendingSocketRequest(
            cast(socket.socket, connection),
            10.0,
            bytearray(),
        )

        def response(frame: bytes | None) -> dict[str, object]:
            self.assertIsNone(self.role._pending)
            self.assertEqual(frame, encode_fast_publication_frame(command))
            return {"ok": True, "marker": "handled"}

        with patch.object(self.role, "handle_socket_frame", side_effect=response):
            self.assertIs(self.role._service_pending_request(), True)

        self.assertIsNone(self.role._pending)
        self.assertEqual(connection.close_count, 1)
        self.assertEqual(
            decode_fast_publication_frame(connection.sent[0]),
            {"ok": True, "marker": "handled"},
        )

        failing = ConnectionProbe(encode_fast_publication_frame(command))
        self.role._pending = socket_module._PendingSocketRequest(
            cast(socket.socket, failing),
            10.0,
            bytearray(),
        )
        with (
            patch.object(
                self.role,
                "handle_socket_frame",
                side_effect=RuntimeError("dispatch failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "dispatch failed"),
        ):
            self.role._service_pending_request()
        self.assertIsNone(self.role._pending)
        self.assertEqual(failing.close_count, 1)

    def test_callbacks_are_one_shot_and_choose_exactly_one_next_source(self) -> None:
        self.role._server_watch_id = 77
        with (
            patch.object(self.role, "_process_socket_safely") as process,
            patch.object(self.role, "install_glib_watch") as watch,
            patch.object(self.role, "_install_pending_timer") as timer,
        ):
            self.assertIs(self.role._on_server_ready(1, 2), False)
        self.assertIsNone(self.role._server_watch_id)
        process.assert_called_once_with()
        watch.assert_called_once_with()
        timer.assert_not_called()

        self.role._pending = pending_connection()
        self.role._pending_timer_id = 88
        with (
            patch.object(self.role, "_process_socket_safely") as process,
            patch.object(self.role, "install_glib_watch") as watch,
            patch.object(self.role, "_install_pending_timer") as timer,
        ):
            self.assertIs(self.role._on_pending_timer(), False)
        self.assertIsNone(self.role._pending_timer_id)
        process.assert_called_once_with()
        timer.assert_called_once_with()
        watch.assert_not_called()

    def test_pending_timer_is_single_and_uses_exact_interval_and_callback(self) -> None:
        self.role._install_pending_timer()
        self.role._install_pending_timer()
        self.assertEqual(
            self.events.timer_calls,
            [
                (
                    socket_module.SOCKET_PENDING_POLL_INTERVAL_MS,
                    self.role._on_pending_timer,
                )
            ],
        )
        self.assertEqual(self.role._pending_timer_id, 101)

        self.role._remove_glib_source("_pending_timer_id")
        self.role._remove_glib_source("_pending_timer_id")
        self.assertEqual(self.events.removed, [101])
        self.assertIsNone(self.role._pending_timer_id)

    def test_recoverable_read_error_discards_connection_before_accepting_again(self) -> None:
        failed = ConnectionProbe(ConnectionResetError("reset"))
        self.role._pending = socket_module._PendingSocketRequest(
            cast(socket.socket, failed),
            10.0,
            bytearray(),
        )
        with patch.object(socket_module.logging, "exception") as log_exception:
            self.role._process_socket_safely()

        self.assertIsNone(self.role._pending)
        self.assertEqual(failed.close_count, 1)
        log_exception.assert_called_once()

        replacement = ConnectionProbe(BlockingIOError())
        server = ServerProbe(replacement)
        self.context._server = cast(socket.socket, server)
        with (
            patch.object(select, "select", return_value=([server], [], [])),
            patch.object(time, "monotonic", return_value=20.0),
        ):
            self.assertIs(self.role.process_socket_once(), True)
        pending = self.role._pending
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertIs(pending.conn, replacement)
        self.assertEqual(server.accept_count, 1)

    def test_discard_tolerates_client_close_failure(self) -> None:
        connection = MagicMock()
        connection.close.side_effect = OSError("already gone")
        self.role._pending = socket_module._PendingSocketRequest(
            cast(socket.socket, connection),
            10.0,
            bytearray(),
        )

        self.role._discard_pending_request()

        self.assertIsNone(self.role._pending)
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
