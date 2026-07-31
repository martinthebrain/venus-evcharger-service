# SPDX-License-Identifier: GPL-3.0-or-later
"""Command endpoint for the local Control API HTTP adapter."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from venus_evcharger.control.http_api_auth import ControlApiHttpAuthenticator
from venus_evcharger.control.http_api_command_contracts import (
    ControlApiCommandPort,
    optional_error_payload,
)
from venus_evcharger.control.http_api_command_payloads import (
    command_response_payload,
    http_status_for_result,
    payload_error_code,
    tracked_command,
    tracked_payload,
)
from venus_evcharger.control.http_api_idempotency import ControlApiHttpIdempotency
from venus_evcharger.control.http_api_rate_limit import ControlApiHttpRateLimit, RateLimitError
from venus_evcharger.control.http_api_response import ControlApiHttpResponder
from venus_evcharger.control.models import ControlCommand, ControlResult

CONTROL_API_MAX_REQUEST_BODY_BYTES = 64 * 1024


class ControlApiHttpCommandEndpoint:
    """Validate, execute, audit, and serialize Control API commands."""

    _INVALID_JSON = object()

    def __init__(
        self,
        service: ControlApiCommandPort,
        responder: ControlApiHttpResponder,
        authenticator: ControlApiHttpAuthenticator,
        rate_limit: ControlApiHttpRateLimit,
        idempotency: ControlApiHttpIdempotency,
    ) -> None:
        self._service = service
        self._responder = responder
        self._authenticator = authenticator
        self._rate_limit = rate_limit
        self._idempotency = idempotency

    def read_json_payload(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
        content_length = self._content_length(handler)
        if content_length is None:
            return None
        parsed = self._parsed_json(handler, content_length)
        if parsed is self._INVALID_JSON:
            return None
        if not isinstance(parsed, dict):
            self._responder.write_error(
                handler,
                HTTPStatus.BAD_REQUEST,
                "invalid_payload",
                "JSON body must be an object.",
            )
            return None
        return {str(key): value for key, value in parsed.items()}

    def _content_length(self, handler: BaseHTTPRequestHandler) -> int | None:
        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            self._responder.write_error(
                handler,
                HTTPStatus.BAD_REQUEST,
                "invalid_content_length",
                "Invalid Content-Length.",
            )
            return None
        if content_length < 0:
            self._responder.write_error(
                handler,
                HTTPStatus.BAD_REQUEST,
                "invalid_content_length",
                "Content-Length must not be negative.",
            )
            return None
        if content_length > CONTROL_API_MAX_REQUEST_BODY_BYTES:
            self._responder.write_error(
                handler,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload_too_large",
                f"JSON body exceeds the {CONTROL_API_MAX_REQUEST_BODY_BYTES}-byte limit.",
            )
            return None
        return content_length

    def _parsed_json(self, handler: BaseHTTPRequestHandler, content_length: int) -> object:
        try:
            raw_payload = handler.rfile.read(content_length)
            return json.loads(raw_payload.decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._responder.write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_json", "Invalid JSON body.")
            return self._INVALID_JSON

    def execute_payload(self, payload: dict[str, Any]) -> tuple[ControlCommand, ControlResult]:
        command = self._service.control_command_from_payload(payload, source="http")
        if not command.command_id or command.idempotency_key != str(payload.get("idempotency_key", "")).strip():
            command = tracked_command(payload, command)
        return command, self._service.handle_control_command(command)

    def write_command_result(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        request_payload = tracked_payload(handler, payload)
        client_host = self._authenticator.client_host(handler)
        replay = self._idempotency.replayed_response(request_payload)
        if replay is not None:
            self.write_replayed_response(handler, replay, client_host)
            return
        try:
            command = self._service.control_command_from_payload(request_payload, source="http")
            command = tracked_command(request_payload, command)
        except ValueError as error:
            self.write_validation_error(handler, request_payload, str(error), client_host)
            return
        rate_limit_error = self._rate_limit.error(client_host, command.name)
        if rate_limit_error is not None:
            self.write_rate_limit_error(handler, command, rate_limit_error, client_host)
            return
        result = self._service.handle_control_command(command)
        self.write_new_response(handler, request_payload, command, result, client_host)

    def write_replayed_response(
        self,
        handler: BaseHTTPRequestHandler,
        replay: tuple[HTTPStatus, dict[str, Any]],
        client_host: str,
    ) -> None:
        status, response_payload = replay
        self.record_audit(
            command=response_payload.get("command"),
            result=response_payload.get("result"),
            error=response_payload.get("error") if isinstance(response_payload.get("error"), dict) else None,
            replayed=True,
            client_host=client_host,
            status=status,
        )
        self._responder.write_json(
            handler,
            status,
            response_payload,
            extra_headers=self._authenticator.state_token_headers,
        )

    def write_validation_error(
        self,
        handler: BaseHTTPRequestHandler,
        request_payload: dict[str, Any],
        error_message: str,
        client_host: str,
    ) -> None:
        response_payload = self._responder.error_payload(payload_error_code(error_message), error_message)
        self.record_audit(
            command=request_payload,
            result=None,
            error=optional_error_payload(response_payload),
            replayed=False,
            client_host=client_host,
            status=HTTPStatus.BAD_REQUEST,
        )
        self._responder.write_json(
            handler,
            HTTPStatus.BAD_REQUEST,
            response_payload,
            extra_headers=self._authenticator.state_token_headers,
        )

    def write_rate_limit_error(
        self,
        handler: BaseHTTPRequestHandler,
        command: ControlCommand,
        rate_limit_error: RateLimitError,
        client_host: str,
    ) -> None:
        status, response_payload, headers = rate_limit_error
        self.record_audit(
            command=command,
            result=None,
            error=optional_error_payload(response_payload),
            replayed=False,
            client_host=client_host,
            status=status,
        )
        self._responder.write_json(
            handler,
            status,
            response_payload,
            extra_headers={**self._authenticator.state_token_headers, **headers},
        )

    def write_new_response(
        self,
        handler: BaseHTTPRequestHandler,
        request_payload: dict[str, Any],
        command: ControlCommand,
        result: ControlResult,
        client_host: str,
    ) -> None:
        status = http_status_for_result(result)
        serialized_command = self._responder.command_payload(command)
        serialized_result = self._responder.result_payload(result)
        response_payload = command_response_payload(
            result,
            replayed=False,
            command_payload=serialized_command,
            result_payload=serialized_result,
        )
        self._idempotency.cache_response(
            request_payload,
            status,
            response_payload,
            command_payload=serialized_command,
            result_payload=serialized_result,
        )
        self.record_audit(
            command=command,
            result=result,
            error=optional_error_payload(response_payload),
            replayed=False,
            client_host=client_host,
            status=status,
        )
        self._responder.write_json(
            handler,
            status,
            response_payload,
            extra_headers=self._authenticator.state_token_headers,
        )

    def record_audit(
        self,
        *,
        command: ControlCommand | dict[str, Any] | None,
        result: ControlResult | dict[str, Any] | None,
        error: dict[str, Any] | None,
        replayed: bool,
        client_host: str,
        status: HTTPStatus,
    ) -> None:
        self._service.record_command_audit(
            command=command,
            result=result,
            error=error,
            replayed=replayed,
            scope="control",
            client_host=client_host,
            status_code=int(status),
            transport="http",
        )
