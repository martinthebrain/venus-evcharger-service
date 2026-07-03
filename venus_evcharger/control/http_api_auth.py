# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import ipaddress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from venus_evcharger.control.http_api_command_contracts import ControlApiHttpService
from venus_evcharger.control.http_api_response import _LocalControlApiResponse


class _LocalControlApiAuth(_LocalControlApiResponse):
    if TYPE_CHECKING:
        _auth_token: str
        _admin_token: str
        _control_token: str
        _localhost_only: bool
        _read_token: str
        _service: ControlApiHttpService
        _unix_socket_path: str
        _update_token: str
        _COMMAND_SCOPE_REQUIREMENTS: dict[str, str]
        _INSUFFICIENT_SCOPE_ERROR: tuple[HTTPStatus, str, str]
        _LOCALITY_FORBIDDEN: tuple[HTTPStatus, str, str]
        _SCOPE_ORDER: dict[str, int]
        _STATE_TOKEN_HEADER: str
        _UNAUTHORIZED_ERROR: tuple[HTTPStatus, str, str]

    def _command_post_auth_error(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
    ) -> tuple[HTTPStatus, str, str] | None:
        return self._auth_error(
            handler,
            required_scope=self._required_scope_for_command_payload(payload),
        )

    @staticmethod
    def _parsed_request_target(target: str) -> tuple[str, dict[str, list[str]]]:
        parts = urlsplit(target)
        return parts.path, parse_qs(parts.query, keep_blank_values=True)

    def _locality_error(self, handler: BaseHTTPRequestHandler) -> tuple[HTTPStatus, str, str] | None:
        if not self._localhost_only or self._unix_socket_path:
            return None
        host = self._client_host(handler)
        return None if self._is_loopback_host(host) else self._LOCALITY_FORBIDDEN

    @staticmethod
    def _client_host(handler: BaseHTTPRequestHandler) -> str:
        client_address = getattr(handler, "client_address", ("127.0.0.1", 0))
        if isinstance(client_address, tuple) and client_address:
            return str(client_address[0])
        return "127.0.0.1"

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        if host in {"localhost", "::1"}:
            return True
        try:
            return bool(ipaddress.ip_address(host).is_loopback)
        except ValueError:
            return False

    def _auth_error(self, handler: BaseHTTPRequestHandler, *, required_scope: str) -> tuple[HTTPStatus, str, str] | None:
        scope = self._authorization_scope(handler)
        if self._scope_satisfies_requirement(scope, required_scope):
            return None
        if scope is not None and required_scope != "read":
            return self._INSUFFICIENT_SCOPE_ERROR
        return self._UNAUTHORIZED_ERROR

    def _scope_satisfies_requirement(self, scope: str | None, required_scope: str) -> bool:
        if scope is None:
            return False
        return self._SCOPE_ORDER.get(scope, -1) >= self._SCOPE_ORDER.get(required_scope, 999)

    def _authorization_scope(self, handler: BaseHTTPRequestHandler) -> str | None:
        scope_tokens = self._scope_tokens()
        if not any(token for _scope_name, token in scope_tokens):
            return "update_admin"
        header = handler.headers.get("Authorization", "").strip()
        for scope_name, token in scope_tokens:
            if self._matches_bearer_token(header, token):
                return scope_name
        return None

    def _scope_tokens(self) -> tuple[tuple[str, str], ...]:
        return (
            ("update_admin", self._effective_update_token()),
            ("control_admin", self._effective_admin_token()),
            ("control_basic", self._effective_control_token()),
            ("read", self._effective_read_token()),
        )

    @staticmethod
    def _matches_bearer_token(header: str, token: str) -> bool:
        return bool(token) and header == f"Bearer {token}"

    def _effective_read_token(self) -> str:
        return str(self._read_token or self._control_token or self._admin_token or self._update_token or self._auth_token)

    def _effective_control_token(self) -> str:
        return str(self._control_token or self._auth_token)

    def _effective_admin_token(self) -> str:
        return str(self._admin_token or self._control_token or self._auth_token)

    def _effective_update_token(self) -> str:
        return str(self._update_token or self._admin_token or self._control_token or self._auth_token)

    def _required_scope_for_command_payload(self, payload: dict[str, Any]) -> str:
        command_name = str(payload.get("name", "")).strip()
        if not command_name and "path" in payload:
            try:
                command_name = self._service._control_command_from_payload(
                    {**payload, "command_id": "", "idempotency_key": ""},
                    source="http",
                ).name
            except ValueError:
                return "control_admin"
        return self._COMMAND_SCOPE_REQUIREMENTS.get(command_name, "control_admin")

    def _request_state_tokens(self, handler: BaseHTTPRequestHandler) -> set[str]:
        tokens: set[str] = set()
        header_values = [
            handler.headers.get("If-Match", "").strip(),
            handler.headers.get(self._STATE_TOKEN_HEADER, "").strip(),
        ]
        for raw_value in header_values:
            if not raw_value:
                continue
            for item in raw_value.split(","):
                normalized = self._normalized_token(item)
                if normalized:
                    tokens.add(normalized)
        return tokens

    @staticmethod
    def _normalized_token(raw_value: str) -> str:
        token = raw_value.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if len(token) >= 2 and token[0] == token[-1] == '"':
            token = token[1:-1].strip()
        return token

    def _state_token(self) -> str:
        state_token_factory = getattr(self._service, "_control_api_state_token", None)
        if callable(state_token_factory):
            return str(state_token_factory()).strip()
        return ""

    def _state_token_headers(self) -> dict[str, str]:
        token = self._state_token()
        if not token:
            return {}
        return {"ETag": f'"{token}"', self._STATE_TOKEN_HEADER: token}
