# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping

from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.core.contracts import (
    normalized_control_api_command_response_fields,
    normalized_control_command_fields,
    normalized_control_result_fields,
)


SAFE_EXTRA_RESPONSE_HEADERS: frozenset[str] = frozenset(("ETag", "Retry-After", "X-State-Token"))


def error_response_payload(code: str, message: str) -> dict[str, Any]:
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


def command_payload(command: ControlCommand) -> dict[str, Any]:
    return normalized_control_command_fields(asdict(command), default_source="http")


def result_payload(result: ControlResult) -> dict[str, Any]:
    return normalized_control_result_fields(asdict(result))


def safe_extra_response_headers(extra_headers: Mapping[str, str] | None) -> dict[str, str]:
    if not extra_headers:
        return {}
    safe_headers: dict[str, str] = {}
    for key, value in extra_headers.items():
        if key not in SAFE_EXTRA_RESPONSE_HEADERS:
            continue
        safe_headers[key] = str(value).replace("\r", "").replace("\n", "")
    return safe_headers


def write_error(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus,
    code: str,
    message: str,
) -> None:
    write_json(handler, status, error_response_payload(code, message))


def write_json(
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
    safe_headers = safe_extra_response_headers(extra_headers)
    if "ETag" in safe_headers:
        handler.send_header("ETag", safe_headers["ETag"])
    if "Retry-After" in safe_headers:
        handler.send_header("Retry-After", safe_headers["Retry-After"])
    if "X-State-Token" in safe_headers:
        handler.send_header("X-State-Token", safe_headers["X-State-Token"])
    handler.end_headers()
    handler.wfile.write(raw)


class _LocalControlApiResponse:
    _SAFE_EXTRA_RESPONSE_HEADERS = SAFE_EXTRA_RESPONSE_HEADERS
    _command_payload = staticmethod(command_payload)
    _error_response_payload = staticmethod(error_response_payload)
    _result_payload = staticmethod(result_payload)
    _safe_extra_response_headers = staticmethod(safe_extra_response_headers)
    _write_error = staticmethod(write_error)
    _write_json = staticmethod(write_json)
