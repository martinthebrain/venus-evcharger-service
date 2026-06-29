#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unix-socket IPC for the dedicated DBus adapter process."""

from __future__ import annotations

import json
import logging
import os
import select
import socket
from collections.abc import Callable
from contextlib import suppress

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_adapter_process_protocol_runtime import DbusAdapterSocketContext
from venus_evcharger.dbus_gateway_command_types import CommandPayload

SocketHandler = Callable[[CommandPayload, str], CommandPayload]


class DbusAdapterSocket:
    _server: socket.socket | None

    def start_socket(self: DbusAdapterSocketContext) -> None:
        with suppress(FileNotFoundError):
            os.unlink(self.paths.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.paths.socket_path)
        server.listen(8)
        server.setblocking(False)
        self._server = server

    def close_socket(self: DbusAdapterSocketContext) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        with suppress(FileNotFoundError):
            os.unlink(self.paths.socket_path)

    def process_socket_once(self: DbusAdapterSocketContext) -> None:
        if self._server is None:
            return
        readable, _writable, _errors = select.select([self._server], [], [], 0.0)
        if not readable:
            return
        try:
            conn, _addr = self._server.accept()
        except BlockingIOError:
            return
        with conn:
            conn.settimeout(0.1)
            try:
                data = conn.recv(65536).decode("utf-8", errors="replace").strip()
            except TimeoutError:
                logging.debug("Gateway socket client connected without sending a request")
                return
            response = self.handle_socket_payload(data)
            conn.sendall((compact_json(response) + "\n").encode("utf-8"))

    def handle_socket_payload(self: DbusAdapterSocketContext, data: str) -> CommandPayload:
        payload, error = parsed_socket_payload(data)
        if error:
            return {"ok": False, "error": error}
        return self.dispatch_socket_payload(payload)

    def dispatch_socket_payload(self: DbusAdapterSocketContext, payload: CommandPayload) -> CommandPayload:
        request_type = str(payload.get("type") or payload.get("kind") or "")
        handler = self.socket_handlers().get(request_type, self.unsupported_socket_request)
        return handler(payload, request_type)

    @staticmethod
    def unsupported_socket_request(_payload: CommandPayload, request_type: str) -> CommandPayload:
        return {"ok": False, "error": f"unsupported request type: {request_type}"}

    def socket_handlers(self: DbusAdapterSocketContext) -> dict[str, SocketHandler]:
        return {
            "snapshot": self.socket_snapshot,
            "health": self.socket_health,
            "refresh_value": self.socket_enqueue,
            "refresh_services": self.socket_enqueue,
            "publish_desired": self.socket_enqueue,
            "publish_value": self.socket_enqueue,
            "set_value": self.socket_enqueue,
        }

    def socket_snapshot(
        self: DbusAdapterSocketContext,
        _payload: CommandPayload,
        _request_type: str,
    ) -> CommandPayload:
        return {"ok": True, "snapshot": self.cache.snapshot()}

    def socket_health(
        self: DbusAdapterSocketContext,
        _payload: CommandPayload,
        _request_type: str,
    ) -> CommandPayload:
        return {"ok": True, "dbus_health": self.health_snapshot()}

    def socket_enqueue(
        self: DbusAdapterSocketContext,
        payload: CommandPayload,
        request_type: str,
    ) -> CommandPayload:
        self.commands.enqueue({**payload, "kind": request_type, "source": payload.get("source", "socket")})
        return {"ok": True}


def parsed_socket_payload(data: str) -> tuple[CommandPayload, str]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as error:
        return {}, str(error)
    if not isinstance(payload, dict):
        return {}, "request must be an object"
    return {str(key): value for key, value in payload.items()}, ""
