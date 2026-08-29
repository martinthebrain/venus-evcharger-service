#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded local protocol servers for auto-input differential scenarios."""

from __future__ import annotations

import json
import socketserver
import threading
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class QuietJsonHandler(BaseHTTPRequestHandler):
    """Serve one configurable JSON response without request logging."""

    payload: bytes = b"{}"
    delay_seconds: float = 0.0

    def do_GET(self) -> None:
        """Return the configured response, optionally after a test delay."""
        if self.delay_seconds > 0.0:
            time.sleep(self.delay_seconds)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        try:
            self.wfile.write(self.payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(  # pylint: disable=redefined-builtin
        self,
        format: str,
        *_args: object,
    ) -> None:
        """Suppress the standard HTTP request log in differential runs."""
        del format
        return


class _ModbusHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = _receive_exact(self.request, 12)
        if len(request) != 12:
            return
        transaction = request[:2]
        unit_id = request[6]
        function = request[7]
        address = int.from_bytes(request[8:10], "big")
        values = {10: 805}
        value = values.get(address, 0).to_bytes(2, "big")
        pdu = bytes((function, len(value))) + value
        response = transaction + b"\x00\x00" + len(pdu + bytes((unit_id,))).to_bytes(2, "big")
        self.request.sendall(response + bytes((unit_id,)) + pdu)


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _receive_exact(connection: object, length: int) -> bytes:
    receiver = getattr(connection, "recv")
    result = bytearray()
    while len(result) < length:
        block = receiver(length - len(result))
        if not block:
            break
        result.extend(block)
    return bytes(result)


@contextmanager
def json_server(
    payload: Mapping[str, object],
    *,
    delay_seconds: float = 0.0,
) -> Generator[str, None, None]:
    """Serve one immutable JSON payload on an ephemeral loopback port."""
    handler = type(
        "ScenarioJsonHandler",
        (QuietJsonHandler,),
        {
            "payload": json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            "delay_seconds": delay_seconds,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@contextmanager
def controlled_json_server(
    payload: Mapping[str, object],
) -> Generator[tuple[str, type[QuietJsonHandler]], None, None]:
    """Serve JSON while allowing a scenario to change response delay."""
    handler = type(
        "ControlledScenarioJsonHandler",
        (QuietJsonHandler,),
        {"payload": json.dumps(payload, separators=(",", ":")).encode("utf-8")},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@contextmanager
def modbus_server() -> Generator[tuple[str, int], None, None]:
    """Serve the minimal deterministic Modbus response used by scenarios."""
    server = _ThreadingTcpServer(("127.0.0.1", 0), _ModbusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
