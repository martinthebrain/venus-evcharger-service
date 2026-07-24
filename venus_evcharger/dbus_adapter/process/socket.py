#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unix-socket IPC for the DBus adapter process."""

from __future__ import annotations

import json
import logging
import os
import select
import socket
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_adapter.process.protocols.runtime import DbusAdapterSocketContext
from venus_evcharger.ipc.command_mailbox import normalized_mapping
from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
)

SocketHandler = Callable[[CommandPayload, str], CommandPayload]
SocketReadResult = tuple[str, CommandPayload | None]
SOCKET_BACKLOG = 8
SOCKET_REQUEST_BYTES = 65536
SOCKET_READ_CHUNK_BYTES = 4096
SOCKET_CLIENT_TIMEOUT_SECONDS = 0.1


class DbusAdapterSocket:
    def __init__(self, context: DbusAdapterSocketContext) -> None:
        self._context = context

    def start_socket(self) -> None:
        with suppress(FileNotFoundError):
            os.unlink(self._context.paths.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self._context.paths.socket_path)
        server.listen(SOCKET_BACKLOG)
        server.setblocking(False)
        self._context._server = server

    def close_socket(self) -> None:
        if self._context._server is not None:
            self._context._server.close()
            self._context._server = None
        with suppress(FileNotFoundError):
            os.unlink(self._context.paths.socket_path)

    def process_socket_once(self) -> None:
        if self._context._server is None:
            return
        conn = accept_socket_connection(self._context._server)
        if conn is None:
            return
        with conn:
            self.serve_socket_connection(conn)

    def serve_socket_connection(self, conn: socket.socket) -> None:
        data, error = receive_socket_request(conn)
        if error is None:
            send_socket_response(conn, self.handle_socket_payload(data))
        elif error:
            send_socket_response(conn, error)

    def handle_socket_payload(self, data: str) -> CommandPayload:
        payload, error = parsed_socket_payload(data)
        if error:
            return {"ok": False, "error": error}
        return self.dispatch_socket_payload(payload)

    def dispatch_socket_payload(self, payload: CommandPayload) -> CommandPayload:
        request_type = str(payload.get("type") or payload.get("kind") or "")
        handler = self.socket_handlers().get(request_type, self.unsupported_socket_request)
        return handler(payload, request_type)

    @staticmethod
    def unsupported_socket_request(_payload: CommandPayload, request_type: str) -> CommandPayload:
        return {"ok": False, "error": f"unsupported request type: {request_type}"}

    def socket_handlers(self) -> dict[str, SocketHandler]:
        return {
            "snapshot": self.socket_snapshot,
            "health": self.socket_health,
            "refresh_energy_inputs": self.socket_enqueue,
            PUBLISH_EVCS_FIELDS_KIND: self.socket_fast_publish,
            PUBLISH_COMPANION_FIELDS_KIND: self.socket_fast_publish,
        }

    def socket_snapshot(
        self,
        _payload: CommandPayload,
        _request_type: str,
    ) -> CommandPayload:
        return {"ok": True, "snapshot": self._context.cache.snapshot()}

    def socket_health(
        self,
        _payload: CommandPayload,
        _request_type: str,
    ) -> CommandPayload:
        return {"ok": True, "dbus_health": self._context.health_role.health_snapshot()}

    def socket_enqueue(
        self,
        payload: CommandPayload,
        request_type: str,
    ) -> CommandPayload:
        self._context.commands.enqueue(
            {**payload, "kind": request_type, "source": payload.get("source", "socket")}
        )
        return {"ok": True}

    def socket_fast_publish(
        self,
        payload: CommandPayload,
        _request_type: str,
    ) -> CommandPayload:
        return self._context.fast_publications.enqueue(payload).to_payload()


def parsed_socket_payload(data: str) -> tuple[CommandPayload, str]:
    try:
        decoded: object = json.loads(data)
    except json.JSONDecodeError as error:
        return {}, str(error)
    payload = normalized_mapping(decoded)
    if payload is None:
        return {}, "request must be an object"
    return payload, ""


def accept_socket_connection(server: socket.socket) -> socket.socket | None:
    readable, _writable, _errors = select.select([server], [], [], 0.0)
    if not readable:
        return None
    try:
        conn, _addr = server.accept()
    except BlockingIOError:
        return None
    return conn


def receive_socket_request(conn: socket.socket) -> tuple[str, CommandPayload | None]:
    return _SocketRequestReader(
        conn=conn,
        deadline=time.monotonic() + SOCKET_CLIENT_TIMEOUT_SECONDS,
        received=bytearray(),
    ).read()


@dataclass(slots=True)
class _SocketRequestReader:
    conn: socket.socket
    deadline: float
    received: bytearray

    def read(self) -> SocketReadResult:
        while True:
            result = self._read_next_chunk()
            if result is not None:
                return result

    def _read_next_chunk(self) -> SocketReadResult | None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0.0:
            return _socket_read_timeout(self.received)
        self.conn.settimeout(remaining)
        try:
            chunk = self.conn.recv(
                min(
                    SOCKET_READ_CHUNK_BYTES,
                    SOCKET_REQUEST_BYTES + 1 - len(self.received),
                )
            )
        except TimeoutError:
            return _socket_read_timeout(self.received)
        return self._consume(chunk)

    def _consume(self, chunk: bytes) -> SocketReadResult | None:
        if not chunk:
            return _decoded_socket_request(self.received)
        self.received.extend(chunk)
        if len(self.received) > SOCKET_REQUEST_BYTES:
            return "", {"ok": False, "error": "request-too-large"}
        newline = self.received.find(b"\n")
        if newline >= 0:
            return _decoded_socket_request(self.received[:newline])
        return None


def _socket_read_timeout(received: bytearray) -> tuple[str, CommandPayload | None]:
    if received:
        return "", {"ok": False, "error": "request-timeout"}
    logging.debug("Gateway socket client connected without sending a request")
    return "", {}


def _decoded_socket_request(raw: bytes | bytearray) -> tuple[str, None]:
    return bytes(raw).decode(errors="replace").strip(), None


def send_socket_response(conn: socket.socket, response: CommandPayload) -> None:
    try:
        conn.sendall((compact_json(response) + "\n").encode())
    except (BrokenPipeError, ConnectionResetError):
        logging.debug("Gateway socket client disconnected before reading its response")
