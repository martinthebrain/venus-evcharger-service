# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned Control API runtime resources and HTTP server lifecycle."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Protocol

from venus_evcharger.control import (
    ControlApiAuditTrail,
    ControlApiIdempotencyStore,
    ControlApiRateLimiter,
    LocalControlApiHttpServer,
)
from venus_evcharger.control.events import ControlApiEventBus
from venus_evcharger.control.http_api_command_contracts import (
    AuditCommand,
    AuditResult,
    ControlApiHttpService,
    JsonObject,
)


class ControlApiServer(Protocol):
    bound_host: str
    bound_port: int
    bound_unix_socket_path: str

    def start(self) -> None: ...
    def stop(self) -> None: ...


class ControlRuntime:
    """Own mutable Control API infrastructure outside the wallbox state object."""

    def __init__(self, service: Any, http_service: ControlApiHttpService) -> None:
        self.service = service
        self.http_service = http_service
        self._audit: ControlApiAuditTrail | None = None
        self._idempotency: ControlApiIdempotencyStore | None = None
        self._rate_limiter: ControlApiRateLimiter | None = None
        self._events = ControlApiEventBus()
        self._server: ControlApiServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def audit_trail(self) -> ControlApiAuditTrail:
        if self._audit is None:
            self._audit = ControlApiAuditTrail(
                history_limit=int(getattr(self.service, "control_api_audit_max_entries", 200)),
                path=str(getattr(self.service, "control_api_audit_path", "")).strip(),
            )
        return self._audit

    def idempotency_store(self) -> ControlApiIdempotencyStore:
        if self._idempotency is None:
            self._idempotency = ControlApiIdempotencyStore(
                history_limit=int(getattr(self.service, "control_api_idempotency_max_entries", 200)),
                path=str(getattr(self.service, "control_api_idempotency_path", "")).strip(),
            )
        return self._idempotency

    def rate_limiter(self) -> ControlApiRateLimiter:
        if self._rate_limiter is None:
            self._rate_limiter = ControlApiRateLimiter(
                max_requests=int(getattr(self.service, "control_api_rate_limit_max_requests", 30)),
                window_seconds=float(getattr(self.service, "control_api_rate_limit_window_seconds", 5.0)),
                critical_cooldown_seconds=float(
                    getattr(self.service, "control_api_critical_cooldown_seconds", 2.0)
                ),
            )
        return self._rate_limiter

    def event_bus(self) -> ControlApiEventBus:
        return self._events

    def record_command_audit(
        self,
        *,
        command: AuditCommand,
        result: AuditResult,
        error: JsonObject | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
        transport: str = "http",
    ) -> JsonObject:
        return self.audit_trail().append(
            {
                "timestamp": time.time(),
                "transport": transport,
                "scope": scope,
                "client_host": client_host,
                "status_code": status_code,
                "replayed": replayed,
                "command": self.command_payload(command, transport),
                "result": self.result_payload(result),
                "error": dict(error or {}),
            }
        )

    @staticmethod
    def command_payload(command: AuditCommand, transport: str) -> JsonObject:
        if isinstance(command, Mapping):
            return dict(command)
        if command is None:
            return {}
        return {
            "name": command.name,
            "path": command.path,
            "value": command.value,
            "source": command.source or transport,
            "detail": command.detail,
            "command_id": command.command_id,
            "idempotency_key": command.idempotency_key,
        }

    @staticmethod
    def result_payload(result: AuditResult) -> JsonObject:
        if isinstance(result, Mapping):
            return dict(result)
        if result is None:
            return {}
        return {
            "status": result.status,
            "accepted": result.accepted,
            "applied": result.applied,
            "persisted": result.persisted,
            "reversible_failure": result.reversible_failure,
            "external_side_effect_started": result.external_side_effect_started,
            "detail": result.detail,
        }

    def publish_command_event(
        self,
        command: AuditCommand,
        result: AuditResult,
        *,
        replayed: bool = False,
    ) -> None:
        self._events.publish(
            "command",
            {
                "command": self.command_payload(command, "internal"),
                "result": self.result_payload(result),
                "replayed": replayed,
            },
        )

    def publish_state_event(self, payload: JsonObject) -> None:
        self._events.publish("snapshot", payload)

    def start_server(self, state_payload: JsonObject) -> None:
        if not hasattr(self.service, "control_api_enabled") or not bool(self.service.control_api_enabled):
            return
        if self._server is None:
            self._server = LocalControlApiHttpServer(
                self.http_service,
                host=str(getattr(self.service, "control_api_host", "127.0.0.1")),
                port=int(getattr(self.service, "control_api_port", 0)),
                auth_token=str(getattr(self.service, "control_api_auth_token", "")),
                read_token=str(getattr(self.service, "control_api_read_token", "")),
                control_token=str(getattr(self.service, "control_api_control_token", "")),
                admin_token=str(getattr(self.service, "control_api_admin_token", "")),
                update_token=str(getattr(self.service, "control_api_update_token", "")),
                localhost_only=bool(getattr(self.service, "control_api_localhost_only", True)),
                unix_socket_path=str(getattr(self.service, "control_api_unix_socket_path", "")),
            )
        self._server.start()
        self.service.control_api_listen_host = self._server.bound_host
        self.service.control_api_listen_port = self._server.bound_port
        self.service.control_api_bound_unix_socket_path = self._server.bound_unix_socket_path
        self.publish_state_event(state_payload)

    def stop_server(self) -> None:
        if self._server is None:
            return
        self._server.stop()
