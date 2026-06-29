# SPDX-License-Identifier: GPL-3.0-or-later
"""Local stdlib HTTP adapter for Control API v1."""

from __future__ import annotations

import logging
import os
import socketserver
import stat
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from venus_evcharger.control.http_api_command_contracts import ControlApiHttpService
from venus_evcharger.control.idempotency import ControlApiIdempotencyStore
from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.control.openapi import build_control_api_openapi_spec
from venus_evcharger.control.rate_limit import ControlApiRateLimiter
from venus_evcharger.control.reference import CONTROL_API_COMMAND_SCOPE_REQUIREMENTS
from venus_evcharger.core.contracts import normalized_control_api_capabilities_fields, normalized_control_api_health_fields

from .http_api_routing import _LocalControlApiRouting


class _ThreadingLocalControlHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ThreadingLocalControlUnixHttpServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalControlApiHttpServer(_LocalControlApiRouting):
    """Expose one tiny local HTTP surface for Control API v1."""

    _STATE_GET_ENDPOINTS = frozenset(
        {
            "/v1/state/automation",
            "/v1/state/build",
            "/v1/state/config-effective",
            "/v1/state/contracts",
            "/v1/state/dbus-diagnostics",
            "/v1/state/health",
            "/v1/state/healthz",
            "/v1/state/operational",
            "/v1/state/runtime",
            "/v1/state/summary",
            "/v1/state/topology",
            "/v1/state/update",
            "/v1/state/version",
            "/v1/state/victron-bias-recommendation",
        }
    )
    _LOCALITY_FORBIDDEN = (
        HTTPStatus.FORBIDDEN,
        "forbidden_remote_client",
        "Remote clients are not allowed for this API.",
    )
    _UNAUTHORIZED_ERROR = (HTTPStatus.UNAUTHORIZED, "unauthorized", "Unauthorized.")
    _INSUFFICIENT_SCOPE_ERROR = (
        HTTPStatus.FORBIDDEN,
        "insufficient_scope",
        "The supplied token does not grant the required scope for this endpoint.",
    )
    _RETRY_HEADER = "X-Control-Api-Retry-Ms"
    _STATE_TOKEN_HEADER = "X-State-Token"
    _COMMAND_SCOPE_REQUIREMENTS: dict[str, str] = dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS)
    _SCOPE_ORDER: dict[str, int] = {
        "read": 0,
        "control_basic": 1,
        "control_admin": 2,
        "update_admin": 3,
    }

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
        self._auth_token = auth_token.strip()
        self._read_token = read_token.strip()
        self._control_token = control_token.strip()
        self._admin_token = admin_token.strip()
        self._update_token = update_token.strip()
        self._localhost_only = bool(localhost_only)
        self._unix_socket_path = unix_socket_path.strip()
        self._server: _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._fallback_idempotency_store = ControlApiIdempotencyStore()
        self._fallback_rate_limiter = ControlApiRateLimiter()
        self.bound_host = ""
        self.bound_port = 0
        self.bound_unix_socket_path = ""

    @staticmethod
    def _bound_host_port(
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
            self.bound_host, self.bound_port = self._bound_host_port(server)
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
        read_auth_required = bool(self._effective_read_token())
        control_auth_required = bool(self._effective_control_token())
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
        payload = self._service._control_api_capabilities_payload()
        return normalized_control_api_capabilities_fields(payload)

    @staticmethod
    def openapi_payload() -> dict[str, Any]:
        return build_control_api_openapi_spec()

    def execute_payload(self, payload: dict[str, Any]) -> tuple[ControlCommand, ControlResult]:
        command = self._service._control_command_from_payload(payload, source="http")
        if not command.command_id or command.idempotency_key != str(payload.get("idempotency_key", "")).strip():
            command = self._tracked_command(payload, command)
        result = self._service._handle_control_command(command)
        return command, result

    def _build_server(self) -> _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer:
        if not self._unix_socket_path:
            return _ThreadingLocalControlHttpServer((self._host, self._port), self._handler_class())
        self._prepare_unix_socket_path(self._unix_socket_path)
        return _ThreadingLocalControlUnixHttpServer(self._unix_socket_path, self._handler_class())

    @staticmethod
    def _prepare_unix_socket_path(path: str) -> None:
        if not os.path.exists(path):
            return
        mode = os.stat(path).st_mode
        if not stat.S_ISSOCK(mode):
            raise ValueError(f"Control API unix socket path already exists and is not a socket: {path}")
        os.unlink(path)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle_post(self)

            def log_message(self, message_format: str, *args: Any) -> None:
                logging.debug("Control API HTTP: " + message_format, *args)

        return _Handler


__all__ = [
    "LocalControlApiHttpServer",
    "_ThreadingLocalControlHttpServer",
    "_ThreadingLocalControlUnixHttpServer",
    "threading",
    "logging",
    "os",
    "stat",
]
