# SPDX-License-Identifier: GPL-3.0-or-later
"""Idempotency policy for Control API HTTP commands."""

from __future__ import annotations

from http import HTTPStatus
from collections.abc import Mapping
from typing import Any

from venus_evcharger.control.http_api_command_contracts import (
    ControlApiCommandPort,
    ControlApiIdempotencyStorePort,
)
from venus_evcharger.control.http_api_command_payloads import (
    idempotency_conflict_response,
    idempotency_fingerprint,
    replayed_payload,
)


class ControlApiHttpIdempotency:
    """Resolve, persist, and announce idempotent command responses."""

    def __init__(self, store: ControlApiIdempotencyStorePort, events: ControlApiCommandPort) -> None:
        self._store = store
        self._events = events

    def replayed_response(self, payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]] | None:
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return None
        cached = self._cached_response(idempotency_key)
        if cached is None:
            return None
        return self._replayed_cached_response(payload, idempotency_key, cached)

    def _replayed_cached_response(
        self,
        payload: dict[str, Any],
        idempotency_key: str,
        cached: tuple[str, int, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None],
    ) -> tuple[HTTPStatus, dict[str, Any]]:
        fingerprint, status, response_payload, command, result = cached
        if fingerprint != idempotency_fingerprint(payload):
            return idempotency_conflict_response(idempotency_key)
        response = replayed_payload(response_payload)
        if command is not None and result is not None:
            self._events.publish_command_event(command, result, replayed=True)
        return HTTPStatus(status), response

    def cache_response(
        self,
        payload: dict[str, Any],
        status: HTTPStatus,
        response_payload: dict[str, Any],
        *,
        command_payload: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> None:
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return
        persisted_response = dict(response_payload)
        persisted_response["command"] = command_payload
        persisted_response["result"] = result_payload
        self._store.put(
            idempotency_key,
            idempotency_fingerprint(payload),
            int(status),
            persisted_response,
        )

    def _cached_response(
        self,
        idempotency_key: str,
    ) -> tuple[str, int, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None] | None:
        cached = self._store.get(idempotency_key)
        if cached is None:
            return None
        fingerprint, status, response_payload = cached
        command = self._mapping_payload(response_payload.get("command"))
        result = self._mapping_payload(response_payload.get("result"))
        return (
            fingerprint,
            status,
            response_payload,
            command,
            result,
        )

    @staticmethod
    def _mapping_payload(value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return {str(key): item for key, item in value.items()}
