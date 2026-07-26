#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation contracts for gateway socket framing and recovery."""

from __future__ import annotations

import logging
import socket
import time
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

import venus_evcharger.dbus_adapter.process.socket as socket_module
from tests.support.dbus_adapter_socket import ConnectionProbe
from venus_evcharger.ipc.fast_publication_wire import (
    FAST_PUBLICATION_WIRE_HEADER_BYTES,
    FastPublicationWireError,
    decode_fast_publication_frame,
    encode_fast_publication_frame,
)
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command


class DbusAdapterSocketProtocolMutationContracts(unittest.TestCase):
    def test_fragment_reader_requests_bounded_chunks_and_stops_at_exact_frame(self) -> None:
        frame = encode_fast_publication_frame(publish_evcs_fields_command({"ac_power_w": 7.0}, priority="live"))
        header = frame[:FAST_PUBLICATION_WIRE_HEADER_BYTES]
        body = frame[FAST_PUBLICATION_WIRE_HEADER_BYTES:]
        connection = ConnectionProbe(header, body)
        pending = socket_module._PendingSocketRequest(
            cast(socket.socket, connection),
            20.0,
            bytearray(),
        )

        complete, received, error = socket_module.read_pending_socket_request(pending)

        self.assertIs(complete, True)
        self.assertEqual(received, frame)
        self.assertIsNone(error)
        self.assertEqual(
            connection.recv_sizes,
            [
                socket_module.SOCKET_READ_CHUNK_BYTES,
                socket_module.SOCKET_READ_CHUNK_BYTES,
            ],
        )

    def test_reader_returns_prebuffered_frame_without_another_socket_read(self) -> None:
        frame = encode_fast_publication_frame({"kind": "publish_evcs_fields"})
        connection = ConnectionProbe()
        pending = socket_module._PendingSocketRequest(
            cast(socket.socket, connection),
            20.0,
            bytearray(frame),
        )

        self.assertEqual(
            socket_module.read_pending_socket_request(pending),
            (True, frame, None),
        )
        self.assertEqual(connection.recv_sizes, [])

    def test_reader_limits_immediately_available_fragments_per_event(self) -> None:
        connection = ConnectionProbe(*(b"E" for _unused in range(socket_module.SOCKET_READ_CHUNKS_PER_EVENT)))
        pending = socket_module._PendingSocketRequest(
            cast(socket.socket, connection),
            20.0,
            bytearray(),
        )

        self.assertEqual(
            socket_module.read_pending_socket_request(pending),
            (False, None, None),
        )
        self.assertEqual(
            connection.recv_sizes,
            [socket_module.SOCKET_READ_CHUNK_BYTES] * socket_module.SOCKET_READ_CHUNKS_PER_EVENT,
        )
        self.assertEqual(
            pending.received,
            bytearray(b"E" * socket_module.SOCKET_READ_CHUNKS_PER_EVENT),
        )

    def test_reader_preserves_partial_data_until_deadline_then_times_out(self) -> None:
        connection = ConnectionProbe(b"EVC", BlockingIOError(), BlockingIOError())
        pending = socket_module._PendingSocketRequest(
            cast(socket.socket, connection),
            4.0,
            bytearray(),
        )
        with patch.object(time, "monotonic", return_value=3.999):
            self.assertEqual(
                socket_module.read_pending_socket_request(pending),
                (False, None, None),
            )
        self.assertEqual(pending.received, bytearray(b"EVC"))

        with patch.object(time, "monotonic", return_value=4.0):
            self.assertEqual(
                socket_module.read_pending_socket_request(pending),
                (True, None, {"ok": False, "error": "request-timeout"}),
            )
        self.assertEqual(pending.received, bytearray(b"EVC"))

    def test_eof_is_incomplete_for_empty_header_and_partial_body(self) -> None:
        for initial, reads in (
            (bytearray(), (b"",)),
            (bytearray(b"EVC"), (b"",)),
        ):
            with self.subTest(initial=bytes(initial)):
                connection = ConnectionProbe(*reads)
                pending = socket_module._PendingSocketRequest(
                    cast(socket.socket, connection),
                    10.0,
                    initial,
                )
                self.assertEqual(
                    socket_module.read_pending_socket_request(pending),
                    (True, None, {"ok": False, "error": "request-incomplete"}),
                )

    def test_frame_size_boundaries_reject_overread_before_decoding(self) -> None:
        exact = bytearray(b"x" * socket_module._MAX_FRAME_BYTES)
        oversized = bytearray(b"x" * (socket_module._MAX_FRAME_BYTES + 1))
        with patch.object(
            socket_module,
            "fast_publication_frame_size",
            return_value=socket_module._MAX_FRAME_BYTES,
        ) as frame_size:
            self.assertEqual(
                socket_module._expected_request_size(exact),
                (socket_module._MAX_FRAME_BYTES, None),
            )
        frame_size.assert_called_once_with(exact)

        with patch.object(
            socket_module,
            "fast_publication_frame_size",
            side_effect=AssertionError("oversize must be rejected first"),
        ):
            self.assertEqual(
                socket_module._expected_request_size(oversized),
                (0, {"ok": False, "error": "request-too-large"}),
            )

        valid = encode_fast_publication_frame({"kind": "publish_evcs_fields"})
        self.assertEqual(
            socket_module._expected_request_size(bytearray(valid + b"x")),
            (0, {"ok": False, "error": "request-too-large"}),
        )

        self.assertEqual(
            socket_module._expected_request_size(bytearray(b"invalid!!")),
            (0, {"ok": False, "error": "invalid-frame-magic"}),
        )

        invalid = ConnectionProbe(b"invalid!!")
        invalid_pending = socket_module._PendingSocketRequest(
            cast(socket.socket, invalid),
            20.0,
            bytearray(),
        )
        complete, frame, error = socket_module.read_pending_socket_request(invalid_pending)
        self.assertIs(complete, True)
        self.assertIsNone(frame)
        self.assertEqual(error, {"ok": False, "error": "invalid-frame-magic"})

    def test_receive_chunk_never_requests_past_hard_limit_plus_sentinel(self) -> None:
        connection = ConnectionProbe(b"a", b"b", BlockingIOError())
        raw = cast(socket.socket, connection)
        self.assertEqual(socket_module._receive_available_chunk(raw, 0), b"a")
        self.assertEqual(
            socket_module._receive_available_chunk(
                raw,
                socket_module._MAX_FRAME_BYTES,
            ),
            b"b",
        )
        self.assertIsNone(
            socket_module._receive_available_chunk(
                raw,
                socket_module._MAX_FRAME_BYTES,
            )
        )
        self.assertEqual(
            connection.recv_sizes,
            [socket_module.SOCKET_READ_CHUNK_BYTES, 1, 1],
        )

    def test_direct_reader_uses_new_deadline_and_returns_frame_error_pair(self) -> None:
        frame = encode_fast_publication_frame({"kind": "publish_evcs_fields"})
        connection = ConnectionProbe(frame)
        with patch.object(time, "monotonic", return_value=8.0):
            self.assertEqual(
                socket_module.receive_socket_request(cast(socket.socket, connection)),
                (frame, None),
            )
        self.assertEqual(connection.recv_sizes, [socket_module.SOCKET_READ_CHUNK_BYTES])

        blocked = ConnectionProbe(BlockingIOError())
        with (
            patch.object(time, "monotonic", return_value=12.0),
            patch.object(
                socket_module,
                "read_pending_socket_request",
                return_value=(False, None, None),
            ) as read,
        ):
            self.assertEqual(
                socket_module.receive_socket_request(cast(socket.socket, blocked)),
                (None, None),
            )
        request = read.call_args.args[0]
        self.assertEqual(
            request.deadline,
            12.0 + socket_module.SOCKET_REQUEST_DEADLINE_SECONDS,
        )
        self.assertEqual(request.received, bytearray())

    def test_send_response_success_and_all_disconnect_classes_are_bounded(self) -> None:
        success = ConnectionProbe()
        socket_module.send_socket_response(
            cast(socket.socket, success),
            {"ok": True, "value": 3},
        )
        self.assertEqual(
            decode_fast_publication_frame(success.sent[0]),
            {"ok": True, "value": 3},
        )

        for error in (
            BlockingIOError(),
            BrokenPipeError(),
            ConnectionResetError(),
            FastPublicationWireError("bad"),
        ):
            with self.subTest(error=type(error).__name__):
                connection = MagicMock()
                if isinstance(error, FastPublicationWireError):
                    encoder = patch.object(
                        socket_module,
                        "encode_fast_publication_frame",
                        side_effect=error,
                    )
                else:
                    connection.sendall.side_effect = error
                    encoder = patch.object(
                        socket_module,
                        "encode_fast_publication_frame",
                        return_value=b"response",
                    )
                with encoder, patch.object(logging, "debug") as debug:
                    socket_module.send_socket_response(connection, {"ok": True})
                debug.assert_called_once_with("Gateway socket client disconnected before reading its response")

    def test_recovery_domain_catches_only_declared_local_failures(self) -> None:
        context = MagicMock()
        events = MagicMock()
        role = socket_module.DbusAdapterSocket(context, events=events)
        for error_type in socket_module.SOCKET_RECOVERY_ERRORS:
            error = error_type("local")
            with (
                self.subTest(error=error_type.__name__),
                patch.object(role, "process_socket_once", side_effect=error),
                patch.object(logging, "exception") as logged,
            ):
                role._process_socket_safely()
                logged.assert_called_once_with("Gateway IPC event failed: %s", error)

        with (
            patch.object(
                role,
                "process_socket_once",
                side_effect=AssertionError("programming defect"),
            ),
            self.assertRaisesRegex(AssertionError, "programming defect"),
        ):
            role._process_socket_safely()


if __name__ == "__main__":
    unittest.main()
