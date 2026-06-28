# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit, event, and server helpers for the Control API mixin."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from venus_evcharger.control import ControlApiAuditTrail, ControlApiIdempotencyStore, ControlApiRateLimiter
from venus_evcharger.control.events import ControlApiEventBus


_RuntimeComponent = TypeVar("_RuntimeComponent")


if TYPE_CHECKING:  # pragma: no cover
    from typing import Protocol

    class _ControlApiServerLike(Protocol):
        bound_host: str
        bound_port: int
        bound_unix_socket_path: str

        def start(self) -> None: ...

        def stop(self) -> None: ...


def _runtime_component(
    owner: object,
    attribute_name: str,
    component_type: type[_RuntimeComponent],
    factory: Callable[[], _RuntimeComponent],
) -> _RuntimeComponent:
    component = getattr(owner, attribute_name, None)
    if isinstance(component, component_type):
        return component
    component = factory()
    setattr(owner, attribute_name, component)
    return component


class _ControlApiRuntimeMixin:
    if TYPE_CHECKING:  # pragma: no cover
        control_api_enabled: bool
        control_api_host: str
        control_api_port: int
        control_api_auth_token: str
        control_api_read_token: str
        control_api_control_token: str
        control_api_admin_token: str
        control_api_update_token: str
        control_api_localhost_only: bool
        control_api_unix_socket_path: str
        control_api_listen_host: str
        control_api_listen_port: int
        control_api_bound_unix_socket_path: str
        _control_api_server: _ControlApiServerLike | None

        def _state_api_event_snapshot_payload(self) -> dict[str, Any]: ...

        def _control_api_server_factory(self) -> Callable[..., _ControlApiServerLike]: ...

    def _control_api_audit_trail(self) -> ControlApiAuditTrail:
        return _runtime_component(
            self,
            "_control_api_audit_trail_instance",
            ControlApiAuditTrail,
            lambda: ControlApiAuditTrail(
                history_limit=int(getattr(self, "control_api_audit_max_entries", 200)),
                path=str(getattr(self, "control_api_audit_path", "")).strip(),
            ),
        )

    def _record_control_api_command_audit(
        self,
        *,
        command: Any,
        result: Any,
        error: dict[str, Any] | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
        transport: str = "http",
    ) -> dict[str, Any]:
        return self._control_api_audit_trail().append(
            {
                "timestamp": time.time(),
                "transport": transport,
                "scope": scope,
                "client_host": client_host,
                "status_code": status_code,
                "replayed": replayed,
                "command": self._audit_command_payload(command, transport),
                "result": self._audit_result_payload(result),
                "error": dict(error or {}),
            }
        )

    @staticmethod
    def _audit_command_payload(command: Any, transport: str) -> dict[str, Any]:
        if isinstance(command, dict):
            return dict(command)
        if command is None:
            return {}
        return {
            "name": getattr(command, "name", ""),
            "path": getattr(command, "path", ""),
            "value": getattr(command, "value", None),
            "source": getattr(command, "source", transport),
            "detail": getattr(command, "detail", ""),
            "command_id": getattr(command, "command_id", ""),
            "idempotency_key": getattr(command, "idempotency_key", ""),
        }

    @staticmethod
    def _audit_result_payload(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return dict(result)
        if result is None:
            return {}
        return {
            "status": getattr(result, "status", ""),
            "accepted": getattr(result, "accepted", False),
            "applied": getattr(result, "applied", False),
            "persisted": getattr(result, "persisted", False),
            "reversible_failure": getattr(result, "reversible_failure", False),
            "external_side_effect_started": getattr(result, "external_side_effect_started", False),
            "detail": getattr(result, "detail", ""),
        }

    def _control_api_idempotency_store(self) -> ControlApiIdempotencyStore:
        return _runtime_component(
            self,
            "_control_api_idempotency_store_instance",
            ControlApiIdempotencyStore,
            lambda: ControlApiIdempotencyStore(
                history_limit=int(getattr(self, "control_api_idempotency_max_entries", 200)),
                path=str(getattr(self, "control_api_idempotency_path", "")).strip(),
            ),
        )

    def _control_api_rate_limiter(self) -> ControlApiRateLimiter:
        return _runtime_component(
            self,
            "_control_api_rate_limiter_instance",
            ControlApiRateLimiter,
            lambda: ControlApiRateLimiter(
                max_requests=int(getattr(self, "control_api_rate_limit_max_requests", 30)),
                window_seconds=float(getattr(self, "control_api_rate_limit_window_seconds", 5.0)),
                critical_cooldown_seconds=float(getattr(self, "control_api_critical_cooldown_seconds", 2.0)),
            ),
        )

    def _control_api_event_bus(self) -> ControlApiEventBus:
        return _runtime_component(
            self,
            "_control_api_event_bus_instance",
            ControlApiEventBus,
            ControlApiEventBus,
        )

    def _publish_control_api_command_event(self, command: Any, result: Any, *, replayed: bool = False) -> None:
        self._control_api_event_bus().publish(
            "command",
            {
                "command": getattr(command, "__dict__", {}) or {
                    "name": command.name,
                    "path": command.path,
                    "value": command.value,
                    "source": command.source,
                    "detail": command.detail,
                    "command_id": getattr(command, "command_id", ""),
                    "idempotency_key": getattr(command, "idempotency_key", ""),
                },
                "result": getattr(result, "__dict__", {}) or {
                    "status": result.status,
                    "accepted": result.accepted,
                    "applied": result.applied,
                    "persisted": result.persisted,
                    "reversible_failure": result.reversible_failure,
                    "external_side_effect_started": result.external_side_effect_started,
                    "detail": result.detail,
                },
                "replayed": replayed,
            },
        )

    def _publish_control_api_state_event(self) -> None:
        self._control_api_event_bus().publish("snapshot", self._state_api_event_snapshot_payload())

    def _start_control_api_server(self) -> None:
        if not bool(getattr(self, "control_api_enabled", False)):
            return
        server = getattr(self, "_control_api_server", None)
        if server is None:
            server_factory = self._control_api_server_factory()
            server = server_factory(
                self,
                host=str(getattr(self, "control_api_host", "127.0.0.1")),
                port=int(getattr(self, "control_api_port", 0)),
                auth_token=str(getattr(self, "control_api_auth_token", "")),
                read_token=str(getattr(self, "control_api_read_token", "")),
                control_token=str(getattr(self, "control_api_control_token", "")),
                admin_token=str(getattr(self, "control_api_admin_token", "")),
                update_token=str(getattr(self, "control_api_update_token", "")),
                localhost_only=bool(getattr(self, "control_api_localhost_only", True)),
                unix_socket_path=str(getattr(self, "control_api_unix_socket_path", "")),
            )
            self._control_api_server = server
        server.start()
        self.control_api_listen_host = server.bound_host
        self.control_api_listen_port = server.bound_port
        self.control_api_bound_unix_socket_path = getattr(server, "bound_unix_socket_path", "")
        self._publish_control_api_state_event()

    def _stop_control_api_server(self) -> None:
        server = getattr(self, "_control_api_server", None)
        if server is None:
            return
        server.stop()
