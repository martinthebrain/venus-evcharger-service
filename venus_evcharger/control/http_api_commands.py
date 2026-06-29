# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any

from venus_evcharger.control.http_api_command_payloads import (
    command_response_payload,
    http_status_for_result,
    idempotency_conflict_response,
    idempotency_fingerprint,
    optimistic_concurrency_payload,
    payload_error_code,
    replayed_payload,
    result_error_code,
    throttled_response,
    tracked_command,
    tracked_payload,
)
from venus_evcharger.control.idempotency import ControlApiIdempotencyStore
from venus_evcharger.control.http_api_command_contracts import (
    ControlApiIdempotencyStoreLike,
    ControlApiRateLimiterLike,
    optional_error_payload,
    require_idempotency_store,
    require_rate_limiter,
)
from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.control.rate_limit import ControlApiRateLimiter
from venus_evcharger.control.http_api_response import (
    error_response_payload,
)


class _LocalControlApiCommand:
    _http_status_for_result = staticmethod(http_status_for_result)
    _idempotency_conflict_response = staticmethod(idempotency_conflict_response)
    _idempotency_fingerprint = staticmethod(idempotency_fingerprint)
    _payload_error_code = staticmethod(payload_error_code)
    _replayed_payload = staticmethod(replayed_payload)
    _result_error_code = staticmethod(result_error_code)
    _throttled_response = staticmethod(throttled_response)
    _tracked_command = staticmethod(tracked_command)
    _tracked_payload = staticmethod(tracked_payload)

    if TYPE_CHECKING:
        _fallback_idempotency_store: ControlApiIdempotencyStore
        _fallback_rate_limiter: ControlApiRateLimiter
        _service: Any

        def _client_host(self, handler: BaseHTTPRequestHandler) -> str: ...

        def _request_state_tokens(self, handler: BaseHTTPRequestHandler) -> set[str]: ...

        def _state_token(self) -> str: ...

        def _state_token_headers(self) -> dict[str, str]: ...

        def _command_payload(self, command: ControlCommand) -> dict[str, Any]: ...

        def _error_response_payload(self, code: str, message: str) -> dict[str, Any]: ...

        def _result_payload(self, result: ControlResult) -> dict[str, Any]: ...

        def _write_error(
            self,
            handler: BaseHTTPRequestHandler,
            status: HTTPStatus,
            code: str,
            message: str,
        ) -> None: ...

        def _write_json(
            self,
            handler: BaseHTTPRequestHandler,
            status: HTTPStatus,
            payload: dict[str, Any],
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None: ...

    def _read_json_payload(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_content_length", "Invalid Content-Length.")
            return None
        try:
            raw_payload = handler.rfile.read(max(0, content_length))
            parsed = json.loads(raw_payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_json", "Invalid JSON body.")
            return None
        if not isinstance(parsed, dict):
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_payload", "JSON body must be an object.")
            return None
        return parsed

    def _write_command_result(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        tracked_payload = self._tracked_payload(handler, payload)
        client_host = self._client_host(handler)
        replay = self._replayed_response(tracked_payload)
        if replay is not None:
            self._record_command_audit(
                command=replay[1].get("command"),
                result=replay[1].get("result"),
                error=replay[1].get("error"),
                replayed=True,
                scope="control",
                client_host=client_host,
                status_code=int(replay[0]),
            )
            self._write_json(handler, replay[0], replay[1], extra_headers=self._state_token_headers())
            return
        try:
            command = self._service._control_command_from_payload(tracked_payload, source="http")
            command = self._tracked_command(tracked_payload, command)
        except ValueError as error:
            error_message = str(error)
            response_payload = self._error_response_payload(self._payload_error_code(error_message), error_message)
            self._record_command_audit(
                command=tracked_payload,
                result=None,
                error=optional_error_payload(response_payload),
                replayed=False,
                scope="control",
                client_host=client_host,
                status_code=int(HTTPStatus.BAD_REQUEST),
            )
            self._write_json(handler, HTTPStatus.BAD_REQUEST, response_payload, extra_headers=self._state_token_headers())
            return
        rate_limit_error = self._rate_limit_error(client_host, command.name)
        if rate_limit_error is not None:
            status, response_payload, headers = rate_limit_error
            self._record_command_audit(
                command=command,
                result=None,
                error=optional_error_payload(response_payload),
                replayed=False,
                scope="control",
                client_host=client_host,
                status_code=int(status),
            )
            self._write_json(handler, status, response_payload, extra_headers={**self._state_token_headers(), **headers})
            return
        result = self._service._handle_control_command(command)
        status = self._http_status_for_result(result)
        response_payload = self._command_response_payload(command, result, replayed=False)
        self._cache_idempotent_response(tracked_payload, status, response_payload, command, result)
        self._record_command_audit(
            command=command,
            result=result,
            error=optional_error_payload(response_payload),
            replayed=False,
            scope="control",
            client_host=client_host,
            status_code=int(status),
        )
        self._write_json(handler, status, response_payload, extra_headers=self._state_token_headers())

    def _rate_limit_error(
        self,
        client_host: str,
        command_name: str,
    ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]] | None:
        client_key = client_host if client_host else "local"
        request_allowed, retry_after = self._rate_limiter().allow_request(client_key)
        if not request_allowed:
            return self._throttled_response(
                "rate_limited",
                "Too many control requests in a short time window.",
                retry_after,
            )
        command_allowed, retry_after = self._rate_limiter().allow_command(client_key, command_name)
        if command_allowed:
            return None
        return self._throttled_response(
            "cooldown_active",
            f"Command '{command_name}' is temporarily cooling down.",
            retry_after,
        )

    def _rate_limiter(self) -> ControlApiRateLimiterLike:
        rate_limiter_factory = getattr(self._service, "_control_api_rate_limiter", None)
        if callable(rate_limiter_factory):
            return require_rate_limiter(rate_limiter_factory())
        return self._fallback_rate_limiter

    def _replayed_response(self, payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]] | None:
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return None
        cached = self._cached_idempotent_response(idempotency_key)
        if cached is None:
            return None
        fingerprint, status, response_payload, command, result = cached
        if fingerprint != self._idempotency_fingerprint(payload):
            return self._idempotency_conflict_response(idempotency_key)
        replayed_payload = self._replayed_payload(response_payload)
        self._publish_replayed_command_event(command, result)
        return (HTTPStatus(status), replayed_payload)

    def _cached_idempotent_response(
        self,
        idempotency_key: str,
    ) -> tuple[str, int, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None] | None:
        cached = self._idempotency_store().get(idempotency_key)
        if cached is None:
            return None
        fingerprint, status, response_payload = cached
        command = response_payload.get("command")
        result = response_payload.get("result")
        return fingerprint, status, response_payload, command if isinstance(command, dict) else None, result if isinstance(result, dict) else None

    def _publish_replayed_command_event(
        self,
        command: Any,
        result: Any,
    ) -> None:
        publish_event = getattr(self._service, "_publish_control_api_command_event", None)
        if not callable(publish_event) or command is None or result is None:
            return
        publish_event(command, result, replayed=True)

    def _cache_idempotent_response(
        self,
        payload: dict[str, Any],
        status: HTTPStatus,
        response_payload: dict[str, Any],
        command: ControlCommand,
        result: ControlResult,
    ) -> None:
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return
        persisted_response = dict(response_payload)
        persisted_response["command"] = self._command_payload(command)
        persisted_response["result"] = self._result_payload(result)
        self._idempotency_store().put(
            idempotency_key,
            self._idempotency_fingerprint(payload),
            int(status),
            persisted_response,
        )

    def _idempotency_store(self) -> ControlApiIdempotencyStoreLike:
        store_factory = getattr(self._service, "_control_api_idempotency_store", None)
        if callable(store_factory):
            return require_idempotency_store(store_factory())
        return self._fallback_idempotency_store

    def _optimistic_concurrency_error(
        self,
        handler: BaseHTTPRequestHandler,
    ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]] | None:
        expected_tokens = self._request_state_tokens(handler)
        if not expected_tokens or "*" in expected_tokens:
            return None
        current_token = self._state_token()
        if current_token in expected_tokens:
            return None
        return HTTPStatus.CONFLICT, optimistic_concurrency_payload(expected_tokens, current_token), self._state_token_headers()

    def _command_response_payload(self, command: ControlCommand, result: ControlResult, *, replayed: bool) -> dict[str, Any]:
        return command_response_payload(
            command,
            result,
            replayed=replayed,
            command_payload=self._command_payload(command),
            result_payload=self._result_payload(result),
        )

    def _record_command_audit(
        self,
        *,
        command: Any,
        result: Any,
        error: dict[str, Any] | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
    ) -> None:
        record_audit = getattr(self._service, "_record_control_api_command_audit", None)
        if not callable(record_audit):
            return
        record_audit(
            command=command,
            result=result,
            error=error,
            replayed=replayed,
            scope=scope,
            client_host=client_host,
            status_code=status_code,
            transport="http",
        )
