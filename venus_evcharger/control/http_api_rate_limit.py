# SPDX-License-Identifier: GPL-3.0-or-later
"""Rate-limit policy for Control API HTTP commands."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from venus_evcharger.control.http_api_command_contracts import ControlApiRateLimiterPort
from venus_evcharger.control.http_api_command_payloads import throttled_response


RateLimitError = tuple[HTTPStatus, dict[str, Any], dict[str, str]]


class ControlApiHttpRateLimit:
    """Translate runtime rate-limit decisions into HTTP outcomes."""

    def __init__(self, limiter: ControlApiRateLimiterPort) -> None:
        self._limiter = limiter

    def error(self, client_host: str, command_name: str) -> RateLimitError | None:
        client_key = client_host or "local"
        request_allowed, retry_after = self._limiter.allow_request(client_key)
        if not request_allowed:
            return throttled_response(
                "rate_limited",
                "Too many control requests in a short time window.",
                retry_after,
            )
        command_allowed, retry_after = self._limiter.allow_command(client_key, command_name)
        if command_allowed:
            return None
        return throttled_response(
            "cooldown_active",
            f"Command '{command_name}' is temporarily cooling down.",
            retry_after,
        )
