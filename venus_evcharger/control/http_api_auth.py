# SPDX-License-Identifier: GPL-3.0-or-later
"""Authentication, locality, and concurrency policy for Control API HTTP."""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from venus_evcharger.control.http_api_command_contracts import ControlApiAuthorizationPort
from venus_evcharger.control.http_api_command_payloads import optimistic_concurrency_payload
from venus_evcharger.control.reference import CONTROL_API_COMMAND_SCOPE_REQUIREMENTS


AccessError = tuple[HTTPStatus, str, str]
ConcurrencyError = tuple[HTTPStatus, dict[str, Any], dict[str, str]]

LOCALITY_FORBIDDEN: AccessError = (
    HTTPStatus.FORBIDDEN,
    "forbidden_remote_client",
    "Remote clients are not allowed for this API.",
)
UNAUTHORIZED_ERROR: AccessError = (HTTPStatus.UNAUTHORIZED, "unauthorized", "Unauthorized.")
INSUFFICIENT_SCOPE_ERROR: AccessError = (
    HTTPStatus.FORBIDDEN,
    "insufficient_scope",
    "The supplied token does not grant the required scope for this endpoint.",
)
SCOPE_ORDER: dict[str, int] = {
    "read": 0,
    "control_basic": 1,
    "control_admin": 2,
    "update_admin": 3,
}
STATE_TOKEN_HEADER = "X-State-Token"


@dataclass(frozen=True)
class ControlApiAuthConfig:
    """Normalized authentication and locality settings."""

    auth_token: str = ""
    read_token: str = ""
    control_token: str = ""
    admin_token: str = ""
    update_token: str = ""
    localhost_only: bool = True
    unix_socket_path: str = ""


class ControlApiHttpAuthenticator:
    """Apply the Control API token hierarchy and optimistic concurrency rules."""

    def __init__(self, service: ControlApiAuthorizationPort, config: ControlApiAuthConfig) -> None:
        self._service = service
        self._config = config

    @staticmethod
    def parse_request_target(target: str) -> tuple[str, dict[str, list[str]]]:
        parts = urlsplit(target)
        return parts.path, parse_qs(parts.query, keep_blank_values=True)

    def locality_error(self, handler: BaseHTTPRequestHandler) -> AccessError | None:
        if not self._config.localhost_only or self._config.unix_socket_path:
            return None
        return None if self.is_loopback_host(self.client_host(handler)) else LOCALITY_FORBIDDEN

    @staticmethod
    def client_host(handler: BaseHTTPRequestHandler) -> str:
        client_address: object = getattr(handler, "client_address", None)
        if isinstance(client_address, tuple) and client_address:
            host = cast(object, client_address[0])
            return str(host)
        return "127.0.0.1"

    @staticmethod
    def is_loopback_host(host: str) -> bool:
        if host == "localhost":
            return True
        try:
            return bool(ipaddress.ip_address(host).is_loopback)
        except ValueError:
            return False

    def auth_error(self, handler: BaseHTTPRequestHandler, *, required_scope: str) -> AccessError | None:
        scope = self.authorization_scope(handler)
        if self.scope_satisfies_requirement(scope, required_scope):
            return None
        if scope is not None and required_scope != "read":
            return INSUFFICIENT_SCOPE_ERROR
        return UNAUTHORIZED_ERROR

    @staticmethod
    def scope_satisfies_requirement(scope: str | None, required_scope: str) -> bool:
        if scope not in SCOPE_ORDER or required_scope not in SCOPE_ORDER:
            return False
        return SCOPE_ORDER[scope] >= SCOPE_ORDER[required_scope]

    def authorization_scope(self, handler: BaseHTTPRequestHandler) -> str | None:
        scope_tokens = self.scope_tokens()
        if not any(token for _scope_name, token in scope_tokens):
            return "update_admin"
        return self._scope_for_header(handler.headers.get("Authorization"), scope_tokens)

    @staticmethod
    def _scope_for_header(
        header_value: str | None,
        scope_tokens: tuple[tuple[str, str], ...],
    ) -> str | None:
        if not header_value:
            return None
        header = header_value.strip()
        return next(
            (
                scope_name
                for scope_name, token in scope_tokens
                if ControlApiHttpAuthenticator.matches_bearer_token(header, token)
            ),
            None,
        )

    def scope_tokens(self) -> tuple[tuple[str, str], ...]:
        return (
            ("update_admin", self.effective_update_token),
            ("control_admin", self.effective_admin_token),
            ("control_basic", self.effective_control_token),
            ("read", self.effective_read_token),
        )

    @staticmethod
    def matches_bearer_token(header: str, token: str) -> bool:
        return bool(token) and secrets.compare_digest(header, f"Bearer {token}")

    @property
    def has_configured_token(self) -> bool:
        """Return whether any explicit authentication boundary is configured."""

        return any(token for _scope_name, token in self.scope_tokens())

    @staticmethod
    def first_configured_token(*tokens: str) -> str:
        return next((str(token) for token in tokens if token), "")

    @property
    def effective_read_token(self) -> str:
        config = self._config
        return self.first_configured_token(
            config.read_token,
            config.control_token,
            config.admin_token,
            config.update_token,
            config.auth_token,
        )

    @property
    def effective_control_token(self) -> str:
        return self.first_configured_token(self._config.control_token, self._config.auth_token)

    @property
    def effective_admin_token(self) -> str:
        config = self._config
        return self.first_configured_token(config.admin_token, config.control_token, config.auth_token)

    @property
    def effective_update_token(self) -> str:
        config = self._config
        return self.first_configured_token(
            config.update_token,
            config.admin_token,
            config.control_token,
            config.auth_token,
        )

    def command_auth_error(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> AccessError | None:
        return self.auth_error(handler, required_scope=self.required_scope_for_command(payload))

    def command_transport_auth_error(self, handler: BaseHTTPRequestHandler) -> AccessError | None:
        """Reject unauthenticated command transports before reading their body."""

        return self.auth_error(handler, required_scope="control_basic")

    def required_scope_for_command(self, payload: dict[str, Any]) -> str:
        command_name = str(payload.get("name", "")).strip()
        return CONTROL_API_COMMAND_SCOPE_REQUIREMENTS.get(command_name, "control_admin")

    @staticmethod
    def request_state_tokens(handler: BaseHTTPRequestHandler) -> set[str]:
        tokens: set[str] = set()
        header_values = (
            handler.headers.get("If-Match", "").strip(),
            handler.headers.get(STATE_TOKEN_HEADER, "").strip(),
        )
        for raw_value in header_values:
            for item in raw_value.split(",") if raw_value else ():
                normalized = ControlApiHttpAuthenticator.normalized_token(item)
                if normalized:
                    tokens.add(normalized)
        return tokens

    @staticmethod
    def normalized_token(raw_value: str) -> str:
        token = raw_value.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if len(token) >= 2 and token[0] == token[-1] == '"':
            token = token[1:-1].strip()
        return token

    @property
    def state_token(self) -> str:
        return str(self._service.state_token()).strip()

    @property
    def state_token_headers(self) -> dict[str, str]:
        token = self.state_token
        if not token:
            return {}
        return {"ETag": f'"{token}"', STATE_TOKEN_HEADER: token}

    def concurrency_error(self, handler: BaseHTTPRequestHandler) -> ConcurrencyError | None:
        expected_tokens = self.request_state_tokens(handler)
        if not expected_tokens or "*" in expected_tokens:
            return None
        current_token = self.state_token
        if current_token in expected_tokens:
            return None
        return (
            HTTPStatus.CONFLICT,
            optimistic_concurrency_payload(expected_tokens, current_token),
            self.state_token_headers,
        )
