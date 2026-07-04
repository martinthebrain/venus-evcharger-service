# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from venus_evcharger.control.models import ControlCommand, ControlResult


def tracked_command(payload: dict[str, Any], command: ControlCommand) -> ControlCommand:
    command_id = _payload_text(payload, "command_id")
    idempotency_key = _payload_text(payload, "idempotency_key")
    if not command.command_id or command.idempotency_key != idempotency_key:
        return replace(
            command,
            command_id=command_id,
            idempotency_key=idempotency_key,
        )
    return command


def _payload_text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key, "")).strip()


def payload_error_code(message: str) -> str:
    lowered = message.lower()
    unsupported_tokens = (
        "unsupported control command",
        "unsupported control path",
        "does not support path",
        "requires one of:",
    )
    if any(token in lowered for token in unsupported_tokens):
        return "unsupported_command"
    return "validation_error"


def throttled_response(
    code: str,
    message: str,
    retry_after: float,
) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]]:
    retry_seconds = max(1, int(retry_after) if retry_after.is_integer() else int(retry_after) + 1)
    payload = command_api_response(
        ok=False,
        detail=message,
        error=error_payload(
            code=code,
            message=message,
            retryable=True,
            details={"retry_after_seconds": retry_after},
        ),
    )
    return HTTPStatus.TOO_MANY_REQUESTS, payload, {"Retry-After": str(retry_seconds)}


def tracked_payload(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> dict[str, Any]:
    tracked = dict(payload)
    command_id = _payload_text(tracked, "command_id") or handler.headers.get("X-Command-Id", "").strip()
    idempotency_key = _payload_text(tracked, "idempotency_key") or handler.headers.get("Idempotency-Key", "").strip()
    tracked["command_id"] = command_id or uuid.uuid4().hex
    tracked["idempotency_key"] = idempotency_key
    return tracked


def idempotency_conflict_response(idempotency_key: str) -> tuple[HTTPStatus, dict[str, Any]]:
    message = "Idempotency-Key was already used for a different payload."
    return (
        HTTPStatus.CONFLICT,
        command_api_response(
            ok=False,
            detail=message,
            error=error_payload(
                code="idempotency_conflict",
                message=message,
                retryable=False,
                details={"idempotency_key": idempotency_key},
            ),
        ),
    )


def replayed_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
    replayed = dict(response_payload)
    replayed["replayed"] = True
    return replayed


def idempotency_fingerprint(payload: dict[str, Any]) -> str:
    comparable = {
        key: value
        for key, value in payload.items()
        if key not in {"command_id", "idempotency_key"}
    }
    return json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)


def command_response_payload(
    result: ControlResult,
    *,
    replayed: bool,
    command_payload: dict[str, Any],
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    error_payload = None
    if not result.accepted:
        error_payload = error_payload_for_result(result)
    return command_api_response(
        ok=bool(result.accepted),
        detail=result.detail,
        command=command_payload,
        result=result_payload,
        replayed=replayed,
        error=error_payload,
    )


def error_payload_for_result(result: ControlResult) -> dict[str, Any]:
    return error_payload(
        code=result_error_code(result),
        message=result.detail or "Command rejected.",
        retryable=result.reversible_failure,
        details={
            "status": result.status,
            "path": result.command.path,
            "command_id": result.command.command_id,
            "idempotency_key": result.command.idempotency_key,
        },
    )


def optimistic_concurrency_payload(expected_tokens: set[str], current_token: str) -> dict[str, Any]:
    message = "If-Match state token does not match the current local service state."
    return command_api_response(
        ok=False,
        detail=message,
        error=error_payload(
            code="conflict",
            message=message,
            retryable=True,
            details={
                "expected": sorted(expected_tokens),
                "current": current_token,
            },
        ),
    )


def command_api_response(
    *,
    ok: bool,
    detail: str,
    command: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    replayed: bool = False,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "detail": detail,
        "command": command,
        "result": result,
        "replayed": replayed,
        "error": error,
    }


def error_payload(
    *,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "details": details,
    }


def result_error_code(result: ControlResult) -> str:
    detail = str(result.detail).strip().lower()
    semantic_checks = (
        (_is_topology_error, "unsupported_for_topology"),
        (_is_update_progress_error, "update_in_progress"),
        (_is_health_error, "blocked_by_health"),
        (_is_mode_block_error, "blocked_by_mode"),
    )
    for predicate, error_code in semantic_checks:
        if predicate(result, detail):
            return error_code
    return "command_rejected" if result.status == "rejected" else "conflict"


def _is_topology_error(result: ControlResult, detail: str) -> bool:
    return result.command.name == "set_phase_selection" and "unsupported" in detail


def _is_update_progress_error(_result: ControlResult, detail: str) -> bool:
    if "update" not in detail:
        return False
    for token in ("progress", "running", "busy", "already"):
        if token in detail:
            return True
    return False


def _is_health_error(_result: ControlResult, detail: str) -> bool:
    return any(token in detail for token in ("health", "fault", "lockout", "recovery"))


def _is_mode_block_error(_result: ControlResult, detail: str) -> bool:
    if "mode" not in detail:
        return False
    for token in ("blocked", "cannot", "while", "unsupported"):
        if token in detail:
            return True
    return False


def http_status_for_result(result: ControlResult) -> HTTPStatus:
    if result.status == "applied":
        return HTTPStatus.OK
    if result.status == "accepted_in_flight":
        return HTTPStatus.ACCEPTED
    return HTTPStatus.CONFLICT
