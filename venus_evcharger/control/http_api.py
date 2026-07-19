# SPDX-License-Identifier: GPL-3.0-or-later
"""Local stdlib HTTP transport for Control API v1."""

from __future__ import annotations

import logging
import os
import socketserver
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from venus_evcharger.control.http_api_auth import ControlApiAuthConfig, ControlApiHttpAuthenticator
from venus_evcharger.control.http_api_command_contracts import ControlApiHttpService
from venus_evcharger.control.http_api_commands import ControlApiHttpCommandEndpoint
from venus_evcharger.control.http_api_events import ControlApiHttpEventEndpoint
from venus_evcharger.control.http_api_idempotency import ControlApiHttpIdempotency
from venus_evcharger.control.http_api_rate_limit import ControlApiHttpRateLimit
from venus_evcharger.control.http_api_response import ControlApiHttpResponder
from venus_evcharger.control.http_api_routing import ControlApiHttpRouter, ControlApiHttpStateReader
from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.control.openapi import build_control_api_openapi_spec
from venus_evcharger.core.contracts import (
    normalized_control_api_capabilities_fields,
    normalized_control_api_health_fields,
)


class _ThreadingLocalControlHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ThreadingLocalControlUnixHttpServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalControlApiHttpServer:
    """Own the Control API HTTP transport and its explicitly composed endpoints."""

    def __init__(
        self,
        service: ControlApiHttpService,
        *,
        host: str,
        port: int,
        auth_token: str = "",
        read_token: str = "",
        control_token: str = "",
        admin_token: str = "",
        update_token: str = "",
        localhost_only: bool = True,
        unix_socket_path: str = "",
    ) -> None:
        self._service = service
        self._host = host
        self._port = int(port)
        self._localhost_only = bool(localhost_only)
        self._unix_socket_path = unix_socket_path.strip()
        self._server: _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer | None = None
        self._thread: threading.Thread | None = None
        self.bound_host = ""
        self.bound_port = 0
        self.bound_unix_socket_path = ""

        self.responder = ControlApiHttpResponder()
        self.authenticator = ControlApiHttpAuthenticator(
            service,
            ControlApiAuthConfig(
                auth_token=auth_token.strip(),
                read_token=read_token.strip(),
                control_token=control_token.strip(),
                admin_token=admin_token.strip(),
                update_token=update_token.strip(),
                localhost_only=self._localhost_only,
                unix_socket_path=self._unix_socket_path,
            ),
        )
        self.rate_limit = ControlApiHttpRateLimit(service.rate_limiter())
        self.idempotency = ControlApiHttpIdempotency(service.idempotency_store(), service)
        self.commands = ControlApiHttpCommandEndpoint(
            service,
            self.responder,
            self.authenticator,
            self.rate_limit,
            self.idempotency,
        )
        self.events = ControlApiHttpEventEndpoint(service, self.authenticator)
        self.state_reader = ControlApiHttpStateReader(service)
        self.router = ControlApiHttpRouter(
            state_reader=self.state_reader,
            health_payload=self.health_payload,
            capabilities_payload=self.capabilities_payload,
            openapi_payload=self.openapi_payload,
            responder=self.responder,
            authenticator=self.authenticator,
            commands=self.commands,
            events=self.events,
        )

    @staticmethod
    def bound_host_port(
        server: _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer,
    ) -> tuple[str, int]:
        address = server.server_address
        if isinstance(address, tuple) and len(address) >= 2:
            return str(address[0]), int(address[1])
        return "", 0

    def start(self) -> None:
        if self._server is not None:
            return
        server = self._build_server()
        self._server = server
        if self._unix_socket_path:
            self.bound_unix_socket_path = self._unix_socket_path
            self.bound_host = ""
            self.bound_port = 0
            listen_target = f"unix://{self.bound_unix_socket_path}"
        else:
            self.bound_host, self.bound_port = self.bound_host_port(server)
            self.bound_unix_socket_path = ""
            listen_target = f"http://{self.bound_host}:{self.bound_port}"
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="venus-evcharger-control-api",
            daemon=True,
        )
        self._thread.start()
        logging.info("Started local Control API v1 on %s", listen_target)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        socket_path = self.bound_unix_socket_path
        self._server = None
        self._thread = None
        self.bound_host = ""
        self.bound_port = 0
        self.bound_unix_socket_path = ""
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=1.0)
        if socket_path and os.path.exists(socket_path):
            os.unlink(socket_path)

    def health_payload(self) -> dict[str, Any]:
        read_auth_required = bool(self.authenticator.effective_read_token)
        control_auth_required = bool(self.authenticator.effective_control_token)
        return normalized_control_api_health_fields(
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "listen_host": self.bound_host or self._host,
                "listen_port": int(self.bound_port or self._port),
                "auth_required": bool(read_auth_required or control_auth_required),
                "read_auth_required": read_auth_required,
                "control_auth_required": control_auth_required,
                "localhost_only": self._localhost_only,
                "unix_socket_path": self.bound_unix_socket_path or self._unix_socket_path,
            }
        )

    def capabilities_payload(self) -> dict[str, Any]:
        return normalized_control_api_capabilities_fields(self._service.capabilities_payload())

    @staticmethod
    def openapi_payload() -> dict[str, Any]:
        return build_control_api_openapi_spec()

    def execute_payload(self, payload: dict[str, Any]) -> tuple[ControlCommand, ControlResult]:
        return self.commands.execute_payload(payload)

    def _build_server(self) -> _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer:
        if not self._unix_socket_path:
            return _ThreadingLocalControlHttpServer((self._host, self._port), self._handler_class())
        self.prepare_unix_socket_path(self._unix_socket_path)
        return _ThreadingLocalControlUnixHttpServer(self._unix_socket_path, self._handler_class())

    @staticmethod
    def prepare_unix_socket_path(path: str) -> None:
        if not os.path.exists(path):
            return
        mode = os.stat(path).st_mode
        if not stat.S_ISSOCK(mode):
            raise ValueError(f"Control API unix socket path already exists and is not a socket: {path}")
        os.unlink(path)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        router = self.router

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                router.handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                router.handle_post(self)

            def log_message(self, *message: Any, **named_message: Any) -> None:
                message_format = message[0] if message else named_message.get("format", "")
                logging.debug("Control API HTTP: " + str(message_format), *message[1:])

        return _Handler


__all__ = ["LocalControlApiHttpServer"]
