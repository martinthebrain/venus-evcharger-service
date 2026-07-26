#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-blocking binary IPC for transient gateway publications."""

from __future__ import annotations

import logging
import os
import select
import socket
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol, cast

from gi.repository import GLib as _RAW_GLIB

from venus_evcharger.dbus_adapter.process.protocols.runtime import DbusAdapterSocketContext
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.fast_publication_wire import (
    FAST_PUBLICATION_WIRE_HEADER_BYTES,
    FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
    FastPublicationWireError,
    decode_fast_publication_frame,
    encode_fast_publication_frame,
    fast_publication_frame_size,
)
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
)

SOCKET_BACKLOG = 8
SOCKET_READ_CHUNK_BYTES = 4096
SOCKET_READ_CHUNKS_PER_EVENT = 4
SOCKET_REQUEST_DEADLINE_SECONDS = 0.1
SOCKET_PENDING_POLL_INTERVAL_MS = 5
SOCKET_RECOVERY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)
_FAST_PUBLICATION_KINDS = frozenset((PUBLISH_EVCS_FIELDS_KIND, PUBLISH_COMPANION_FIELDS_KIND))
_MAX_FRAME_BYTES = FAST_PUBLICATION_WIRE_HEADER_BYTES + FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES


class _GlibEventApi(Protocol):
    PRIORITY_DEFAULT: int
    IO_IN: int

    def io_add_watch(
        self,
        source: int,
        priority: int,
        condition: int,
        callback: Callable[[int, int], bool],
    ) -> int: ...

    def timeout_add(
        self,
        interval: int,
        callback: Callable[[], bool],
    ) -> int: ...

    def source_remove(self, source_id: int) -> bool: ...


GLIB = cast(_GlibEventApi, _RAW_GLIB)


class DbusAdapterSocket:
    """Serve only the latency-sensitive, transient publication boundary."""

    def __init__(
        self,
        context: DbusAdapterSocketContext,
        *,
        events: _GlibEventApi = GLIB,
    ) -> None:
        self._context = context
        self._events = events
        self._pending: _PendingSocketRequest | None = None
        self._server_watch_id: int | None = None
        self._pending_timer_id: int | None = None

    def start_socket(self) -> None:
        with suppress(FileNotFoundError):
            os.unlink(self._context.paths.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self._context.paths.socket_path)
        server.listen(SOCKET_BACKLOG)
        server.setblocking(False)
        self._context._server = server

    def install_glib_watch(self) -> None:
        """Wake the gateway only when the Unix server socket becomes readable."""
        if self._server_watch_id is not None:
            return
        server = self._context._server
        if server is None:
            return
        self._server_watch_id = int(
            self._events.io_add_watch(
                server.fileno(),
                self._events.PRIORITY_DEFAULT,
                self._events.IO_IN,
                self._on_server_ready,
            )
        )

    def close_socket(self) -> None:
        self._remove_glib_source("_server_watch_id")
        self._remove_glib_source("_pending_timer_id")
        if self._pending is not None:
            self._pending.conn.close()
            self._pending = None
        if self._context._server is not None:
            self._context._server.close()
            self._context._server = None
        with suppress(FileNotFoundError):
            os.unlink(self._context.paths.socket_path)

    def process_socket_once(self) -> bool:
        if self._pending is None:
            server = self._context._server
            if server is None:
                return False
            conn = accept_socket_connection(server)
            if conn is None:
                return False
            self._pending = _PendingSocketRequest(
                conn,
                time.monotonic() + SOCKET_REQUEST_DEADLINE_SECONDS,
                bytearray(),
            )
        return self._service_pending_request()

    def _service_pending_request(self) -> bool:
        pending = self._pending
        if pending is None:
            return False
        complete, frame, error = read_pending_socket_request(pending)
        if not complete:
            return True
        self._pending = None
        try:
            response = error if error is not None else self.handle_socket_frame(frame)
            send_socket_response(pending.conn, response)
        finally:
            pending.conn.close()
        return True

    def _on_server_ready(self, _source: int, _condition: int) -> bool:
        self._server_watch_id = None
        return self._process_event()

    def _on_pending_timer(self) -> bool:
        self._pending_timer_id = None
        return self._process_event()

    def _process_event(self) -> bool:
        self._process_socket_safely()
        if self._pending is not None:
            self._install_pending_timer()
        else:
            self.install_glib_watch()
        return False

    def _process_socket_safely(self) -> None:
        try:
            self.process_socket_once()
        except SOCKET_RECOVERY_ERRORS as error:
            self._discard_pending_request()
            logging.exception("Gateway IPC event failed: %s", error)

    def _discard_pending_request(self) -> None:
        pending = self._pending
        self._pending = None
        if pending is not None:
            with suppress(OSError):
                pending.conn.close()

    def _install_pending_timer(self) -> None:
        if self._pending_timer_id is not None:
            return
        self._pending_timer_id = int(
            self._events.timeout_add(
                SOCKET_PENDING_POLL_INTERVAL_MS,
                self._on_pending_timer,
            )
        )

    def _remove_glib_source(self, attribute: str) -> None:
        source_id = getattr(self, attribute)
        if source_id is None:
            return
        setattr(self, attribute, None)
        self._events.source_remove(source_id)

    def handle_socket_frame(self, frame: bytes | None) -> CommandPayload:
        if frame is None:
            return {"ok": False, "error": "request-incomplete"}
        try:
            payload = decode_fast_publication_frame(frame)
        except FastPublicationWireError as error:
            return {"ok": False, "error": str(error)}
        return self.dispatch_socket_payload(payload)

    def dispatch_socket_payload(self, payload: CommandMapping) -> CommandPayload:
        request_type = str(payload.get("kind") or "")
        if request_type not in _FAST_PUBLICATION_KINDS:
            return {"ok": False, "error": f"unsupported request type: {request_type}"}
        return self._context.fast_publications.enqueue(payload).to_payload()


def accept_socket_connection(server: socket.socket) -> socket.socket | None:
    readable, _writable, _errors = select.select([server], [], [], 0.0)
    if not readable:
        return None
    try:
        conn, _addr = server.accept()
    except BlockingIOError:
        return None
    conn.setblocking(False)
    return conn


def receive_socket_request(
    conn: socket.socket,
) -> tuple[bytes | None, CommandPayload | None]:
    """Read one immediately available frame for direct boundary tests."""
    pending = _PendingSocketRequest(
        conn,
        time.monotonic() + SOCKET_REQUEST_DEADLINE_SECONDS,
        bytearray(),
    )
    _complete, frame, error = read_pending_socket_request(pending)
    return frame, error


@dataclass(slots=True)
class _PendingSocketRequest:
    conn: socket.socket
    deadline: float
    received: bytearray


def read_pending_socket_request(
    pending: _PendingSocketRequest,
) -> tuple[bool, bytes | None, CommandPayload | None]:
    result = _buffered_request_result(pending.received)
    for _unused in range(SOCKET_READ_CHUNKS_PER_EVENT):
        if result is not None:
            return result
        result = _receive_request_chunk(pending)
        if result is None:
            result = _buffered_request_result(pending.received)
    return result if result is not None else (False, None, None)


def _buffered_request_result(
    received: bytearray,
) -> tuple[bool, bytes | None, CommandPayload | None] | None:
    expected_size, error = _expected_request_size(received)
    if error is not None:
        return True, None, error
    if expected_size and len(received) == expected_size:
        return True, bytes(received), None
    return None


def _receive_request_chunk(
    pending: _PendingSocketRequest,
) -> tuple[bool, bytes | None, CommandPayload | None] | None:
    chunk = _receive_available_chunk(pending.conn, len(pending.received))
    if chunk is None:
        return _pending_read_result(pending.deadline)
    if not chunk:
        return True, None, {"ok": False, "error": "request-incomplete"}
    pending.received.extend(chunk)
    return None


def _pending_read_result(
    deadline: float,
) -> tuple[bool, bytes | None, CommandPayload | None]:
    if time.monotonic() < deadline:
        return False, None, None
    return True, None, {"ok": False, "error": "request-timeout"}


def _expected_request_size(
    received: bytearray,
) -> tuple[int, CommandPayload | None]:
    if len(received) > _MAX_FRAME_BYTES:
        return 0, {"ok": False, "error": "request-too-large"}
    try:
        expected_size = fast_publication_frame_size(received)
    except FastPublicationWireError as error:
        return 0, {"ok": False, "error": str(error)}
    if expected_size and len(received) > expected_size:
        return 0, {"ok": False, "error": "request-too-large"}
    return expected_size, None


def _receive_available_chunk(
    conn: socket.socket,
    received_bytes: int,
) -> bytes | None:
    try:
        return conn.recv(min(SOCKET_READ_CHUNK_BYTES, _MAX_FRAME_BYTES + 1 - received_bytes))
    except BlockingIOError:
        return None


def send_socket_response(conn: socket.socket, response: CommandMapping) -> None:
    try:
        conn.sendall(encode_fast_publication_frame(response))
    except (
        BlockingIOError,
        BrokenPipeError,
        ConnectionResetError,
        FastPublicationWireError,
    ):
        logging.debug("Gateway socket client disconnected before reading its response")
