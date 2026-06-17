# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
# pyright: reportAttributeAccessIssue=false
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping, cast

from venus_evcharger.control.idempotency import ControlApiIdempotencyStore
from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.control.rate_limit import ControlApiRateLimiter
from venus_evcharger.core.contracts import (
    normalized_control_api_command_response_fields,
    normalized_control_api_error_fields,
    normalized_control_command_fields,
    normalized_control_result_fields,
)


class _LocalControlApiCommandMixin:
    _SAFE_EXTRA_RESPONSE_HEADERS: frozenset[str] = frozenset(("ETag", "Retry-After", "X-State-Token"))

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
                error=cast(dict[str, Any] | None, response_payload.get("error")),
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
                error=cast(dict[str, Any] | None, response_payload.get("error")),
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
            error=cast(dict[str, Any] | None, response_payload.get("error")),
            replayed=False,
            scope="control",
            client_host=client_host,
            status_code=int(status),
        )
        self._write_json(handler, status, response_payload, extra_headers=self._state_token_headers())

    @staticmethod
    def _tracked_command(payload: dict[str, Any], command: ControlCommand) -> ControlCommand:
        if not command.command_id or command.idempotency_key != str(payload.get("idempotency_key", "")).strip():
            return replace(
                command,
                command_id=str(payload.get("command_id", "")).strip(),
                idempotency_key=str(payload.get("idempotency_key", "")).strip(),
            )
        return command

    @staticmethod
    def _payload_error_code(message: str) -> str:
        lowered = message.lower()
        if "unsupported control command" in lowered or "unsupported control path" in lowered:
            return "unsupported_command"
        if "does not support path" in lowered or "requires one of:" in lowered:
            return "unsupported_command"
        return "validation_error"

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

    def _rate_limiter(self) -> ControlApiRateLimiter:
        rate_limiter_factory = getattr(self._service, "_control_api_rate_limiter", None)
        if callable(rate_limiter_factory):
            return cast(ControlApiRateLimiter, rate_limiter_factory())
        return cast(ControlApiRateLimiter, self._fallback_rate_limiter)

    @staticmethod
    def _throttled_response(
        code: str,
        message: str,
        retry_after: float,
    ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]]:
        retry_seconds = max(1, int(retry_after) if retry_after.is_integer() else int(retry_after) + 1)
        payload = normalized_control_api_command_response_fields(
            {
                "ok": False,
                "detail": message,
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": True,
                    "details": {"retry_after_seconds": retry_after},
                },
            }
        )
        return HTTPStatus.TOO_MANY_REQUESTS, payload, {"Retry-After": str(retry_seconds)}

    def _tracked_payload(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> dict[str, Any]:
        tracked = dict(payload)
        command_id = str(tracked.get("command_id", "")).strip() or handler.headers.get("X-Command-Id", "").strip()
        idempotency_key = str(tracked.get("idempotency_key", "")).strip() or handler.headers.get("Idempotency-Key", "").strip()
        tracked["command_id"] = command_id or uuid.uuid4().hex
        tracked["idempotency_key"] = idempotency_key
        return tracked

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

    @staticmethod
    def _idempotency_conflict_response(idempotency_key: str) -> tuple[HTTPStatus, dict[str, Any]]:
        message = "Idempotency-Key was already used for a different payload."
        return (
            HTTPStatus.CONFLICT,
            normalized_control_api_command_response_fields(
                {
                    "ok": False,
                    "detail": message,
                    "error": {
                        "code": "idempotency_conflict",
                        "message": message,
                        "retryable": False,
                        "details": {"idempotency_key": idempotency_key},
                    },
                }
            ),
        )

    @staticmethod
    def _replayed_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
        return normalized_control_api_command_response_fields({**response_payload, "replayed": True})

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

    def _idempotency_store(self) -> ControlApiIdempotencyStore:
        store_factory = getattr(self._service, "_control_api_idempotency_store", None)
        if callable(store_factory):
            return cast(ControlApiIdempotencyStore, store_factory())
        return cast(ControlApiIdempotencyStore, self._fallback_idempotency_store)

    @staticmethod
    def _idempotency_fingerprint(payload: dict[str, Any]) -> str:
        comparable = {
            key: value
            for key, value in payload.items()
            if key not in {"command_id", "idempotency_key"}
        }
        return json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)

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
        message = "If-Match state token does not match the current local service state."
        payload = normalized_control_api_command_response_fields(
            {
                "ok": False,
                "detail": message,
                "error": {
                    "code": "conflict",
                    "message": message,
                    "retryable": True,
                    "details": {
                        "expected": sorted(expected_tokens),
                        "current": current_token,
                    },
                },
            }
        )
        return HTTPStatus.CONFLICT, payload, self._state_token_headers()

    def _command_response_payload(self, command: ControlCommand, result: ControlResult, *, replayed: bool) -> dict[str, Any]:
        error_payload = None
        if not result.accepted:
            error_payload = normalized_control_api_error_fields(
                {
                    "code": self._result_error_code(result),
                    "message": result.detail or "Command rejected.",
                    "retryable": result.reversible_failure,
                    "details": {
                        "status": result.status,
                        "path": result.command.path,
                        "command_id": result.command.command_id,
                        "idempotency_key": result.command.idempotency_key,
                    },
                }
            )
        return normalized_control_api_command_response_fields(
            {
                "ok": bool(result.accepted),
                "detail": result.detail,
                "replayed": replayed,
                "command": self._command_payload(command),
                "result": self._result_payload(result),
                "error": error_payload,
            }
        )

    @staticmethod
    def _result_error_code(result: ControlResult) -> str:
        detail = str(result.detail).strip().lower()
        semantic_checks = (
            (_LocalControlApiCommandMixin._is_topology_error, "unsupported_for_topology"),
            (_LocalControlApiCommandMixin._is_update_progress_error, "update_in_progress"),
            (_LocalControlApiCommandMixin._is_health_error, "blocked_by_health"),
            (_LocalControlApiCommandMixin._is_mode_block_error, "blocked_by_mode"),
        )
        for predicate, error_code in semantic_checks:
            if predicate(result, detail):
                return error_code
        return "command_rejected" if result.status == "rejected" else "conflict"

    @staticmethod
    def _is_topology_error(result: ControlResult, detail: str) -> bool:
        return result.command.name == "set_phase_selection" and "unsupported" in detail

    @staticmethod
    def _is_update_progress_error(_result: ControlResult, detail: str) -> bool:
        if "update" not in detail:
            return False
        for token in ("progress", "running", "busy", "already"):
            if token in detail:
                return True
        return False

    @staticmethod
    def _is_health_error(_result: ControlResult, detail: str) -> bool:
        return any(token in detail for token in ("health", "fault", "lockout", "recovery"))

    @staticmethod
    def _is_mode_block_error(_result: ControlResult, detail: str) -> bool:
        if "mode" not in detail:
            return False
        for token in ("blocked", "cannot", "while", "unsupported"):
            if token in detail:
                return True
        return False

    @staticmethod
    def _error_response_payload(code: str, message: str) -> dict[str, Any]:
        return normalized_control_api_command_response_fields(
            {
                "ok": False,
                "detail": message,
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": False,
                    "details": {},
                },
            }
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

    @staticmethod
    def _http_status_for_result(result: ControlResult) -> HTTPStatus:
        if result.status == "applied":
            return HTTPStatus.OK
        if result.status == "accepted_in_flight":
            return HTTPStatus.ACCEPTED
        return HTTPStatus.CONFLICT

    @staticmethod
    def _command_payload(command: ControlCommand) -> dict[str, Any]:
        return normalized_control_command_fields(asdict(command), default_source="http")

    @staticmethod
    def _result_payload(result: ControlResult) -> dict[str, Any]:
        return normalized_control_result_fields(asdict(result))

    @staticmethod
    def _safe_extra_response_headers(extra_headers: Mapping[str, str] | None) -> dict[str, str]:
        if not extra_headers:
            return {}
        safe_headers: dict[str, str] = {}
        for key, value in extra_headers.items():
            if key not in _LocalControlApiCommandMixin._SAFE_EXTRA_RESPONSE_HEADERS:
                continue
            safe_headers[key] = str(value).replace("\r", "").replace("\n", "")
        return safe_headers

    @staticmethod
    def _write_error(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        payload = _LocalControlApiCommandMixin._error_response_payload(code, message)
        _LocalControlApiCommandMixin._write_json(handler, status, payload)

    @staticmethod
    def _write_json(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: Mapping[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        raw = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        safe_headers = _LocalControlApiCommandMixin._safe_extra_response_headers(extra_headers)
        if "ETag" in safe_headers:
            handler.send_header("ETag", safe_headers["ETag"])
        if "Retry-After" in safe_headers:
            handler.send_header("Retry-After", safe_headers["Retry-After"])
        if "X-State-Token" in safe_headers:
            handler.send_header("X-State-Token", safe_headers["X-State-Token"])
        handler.end_headers()
        handler.wfile.write(raw)
