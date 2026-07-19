# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP response serialization for Control API v1."""

from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping

from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.core.contracts import (
    normalized_control_command_fields,
    normalized_control_result_fields,
)


SAFE_EXTRA_RESPONSE_HEADERS: frozenset[str] = frozenset(("ETag", "Retry-After", "X-State-Token"))


class ControlApiHttpResponder:
    """Serialize validated Control API payloads to one HTTP handler."""

    @staticmethod
    def error_payload(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "detail": message,
            "replayed": False,
            "command": None,
            "result": None,
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            },
        }

    @staticmethod
    def command_payload(command: ControlCommand) -> dict[str, Any]:
        return normalized_control_command_fields(asdict(command))

    @staticmethod
    def result_payload(result: ControlResult) -> dict[str, Any]:
        return normalized_control_result_fields(asdict(result))

    @staticmethod
    def safe_extra_headers(extra_headers: Mapping[str, str] | None) -> dict[str, str]:
        if not extra_headers:
            return {}
        return {
            key: str(value).replace("\r", "").replace("\n", "")
            for key, value in extra_headers.items()
            if key in SAFE_EXTRA_RESPONSE_HEADERS
        }

    def write_error(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        self.write_json(handler, status, self.error_payload(code, message))

    def write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: Mapping[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        raw = json.dumps(dict(payload), sort_keys=True).encode()
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        for key, value in self.safe_extra_headers(extra_headers).items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(raw)
