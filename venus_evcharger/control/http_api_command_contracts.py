# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, TypeGuard

from venus_evcharger.control.models import ControlCommand, ControlCommandSource, ControlResult


class ControlApiRateLimiterLike(Protocol):
    """Rate-limiter contract used by the HTTP command endpoint."""

    def allow_request(self, client_key: str, *, now: float | None = None) -> tuple[bool, float]: ...  # pragma: no cover

    def allow_command(
        self,
        client_key: str,
        command_name: str,
        *,
        now: float | None = None,
    ) -> tuple[bool, float]: ...  # pragma: no cover


class ControlApiIdempotencyStoreLike(Protocol):
    """Idempotency-store contract used by the HTTP command endpoint."""

    def get(self, key: str) -> tuple[str, int, dict[str, Any]] | None: ...  # pragma: no cover

    def put(self, key: str, fingerprint: str, status: int, response: dict[str, Any]) -> None: ...  # pragma: no cover


class ControlApiEventBusLike(Protocol):
    """Event-bus contract used by the HTTP event-stream endpoint."""

    def recent(
        self,
        *,
        limit: int,
        after_seq: int,
    ) -> Iterable[Mapping[str, Any]]: ...  # pragma: no cover

    def wait_for_next(
        self,
        *,
        after_seq: int,
        timeout: float,
    ) -> Mapping[str, Any] | None: ...  # pragma: no cover


class ControlApiHttpService(Protocol):
    """Service boundary required by the local Control API HTTP adapter."""

    def _control_api_capabilities_payload(self) -> dict[str, Any]: ...  # pragma: no cover

    def _control_api_event_bus(self) -> ControlApiEventBusLike: ...  # pragma: no cover

    def _control_command_from_payload(
        self,
        payload: Mapping[str, Any],
        *,
        source: ControlCommandSource,
    ) -> ControlCommand: ...  # pragma: no cover

    def _handle_control_command(self, command: ControlCommand) -> ControlResult: ...  # pragma: no cover

    def _state_api_event_snapshot_payload(self) -> dict[str, Any]: ...  # pragma: no cover

    def __getattr__(self, name: str) -> Any: ...  # pragma: no cover


def optional_error_payload(response_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the nested error object from one API response payload."""
    error = response_payload.get("error")
    return {str(key): value for key, value in error.items()} if isinstance(error, dict) else None


def require_rate_limiter(value: object) -> ControlApiRateLimiterLike:
    """Return a validated Control API rate limiter from a service factory."""
    if not _rate_limiter_like(value):
        raise TypeError(
            f"_control_api_rate_limiter must return an object with allow_request/allow_command, "
            f"got {type(value).__name__}"
        )
    return value


def require_idempotency_store(value: object) -> ControlApiIdempotencyStoreLike:
    """Return a validated Control API idempotency store from a service factory."""
    if not _idempotency_store_like(value):
        raise TypeError(
            f"_control_api_idempotency_store must return an object with get/put, got {type(value).__name__}"
        )
    return value


def _rate_limiter_like(value: object) -> TypeGuard[ControlApiRateLimiterLike]:
    return callable(getattr(value, "allow_request", None)) and callable(getattr(value, "allow_command", None))


def _idempotency_store_like(value: object) -> TypeGuard[ControlApiIdempotencyStoreLike]:
    return callable(getattr(value, "get", None)) and callable(getattr(value, "put", None))
