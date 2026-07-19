# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed ports for the local Control API HTTP adapter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from venus_evcharger.control.models import ControlCommand, ControlCommandSource, ControlResult


JsonObject = dict[str, Any]
AuditCommand = ControlCommand | Mapping[str, Any] | None
AuditResult = ControlResult | Mapping[str, Any] | None


class ControlApiRateLimiterPort(Protocol):
    """Rate-limiter behavior required by the command endpoint."""

    def allow_request(self, client_key: str, *, now: float | None = None) -> tuple[bool, float]: ...

    def allow_command(
        self,
        client_key: str,
        command_name: str,
        *,
        now: float | None = None,
    ) -> tuple[bool, float]: ...


class ControlApiIdempotencyStorePort(Protocol):
    """Idempotency storage required by the command endpoint."""

    def get(self, key: str) -> tuple[str, int, JsonObject] | None: ...

    def put(self, key: str, fingerprint: str, status: int, response: JsonObject) -> None: ...


class ControlApiEventBusPort(Protocol):
    """Event-bus behavior required by the event endpoint."""

    def recent(
        self,
        *,
        limit: int,
        after_seq: int,
    ) -> Iterable[Mapping[str, Any]]: ...

    def wait_for_next(
        self,
        *,
        after_seq: int,
        timeout: float,
    ) -> Mapping[str, Any] | None: ...


class ControlApiCommandPort(Protocol):
    """Command parsing and execution boundary."""

    def control_command_from_payload(
        self,
        payload: JsonObject,
        source: ControlCommandSource = "http",
    ) -> ControlCommand: ...

    def handle_control_command(self, command: ControlCommand) -> ControlResult: ...

    def publish_command_event(
        self,
        command: AuditCommand,
        result: AuditResult,
        *,
        replayed: bool = False,
    ) -> None: ...

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
    ) -> JsonObject: ...


class ControlApiRuntimePort(Protocol):
    """Runtime-owned stores used by the HTTP adapter."""

    def idempotency_store(self) -> ControlApiIdempotencyStorePort: ...

    def rate_limiter(self) -> ControlApiRateLimiterPort: ...


class ControlApiAuthorizationPort(Protocol):
    """Command parsing and state-token behavior needed by authorization."""

    def state_token(self) -> str: ...

    def control_command_from_payload(
        self,
        payload: JsonObject,
        source: ControlCommandSource = "http",
    ) -> ControlCommand: ...


class ControlApiStatePort(Protocol):
    """Named state readers consumed by HTTP routing."""

    def capabilities_payload(self) -> JsonObject: ...

    def state_token(self) -> str: ...

    def automation_payload(self) -> JsonObject: ...

    def build_payload(self) -> JsonObject: ...

    def config_effective_payload(self) -> JsonObject: ...

    def contracts_payload(self) -> JsonObject: ...

    def dbus_diagnostics_payload(self) -> JsonObject: ...

    def health_payload(self) -> JsonObject: ...

    def healthz_payload(self) -> JsonObject: ...

    def operational_payload(self) -> JsonObject: ...

    def runtime_payload(self) -> JsonObject: ...

    def summary_payload(self) -> JsonObject: ...

    def topology_payload(self) -> JsonObject: ...

    def update_payload(self) -> JsonObject: ...

    def version_payload(self) -> JsonObject: ...

    def victron_bias_recommendation_payload(self) -> JsonObject: ...


class ControlApiEventsPort(Protocol):
    """Event stream boundary."""

    def event_bus(self) -> ControlApiEventBusPort: ...

    def event_snapshot_payload(self) -> JsonObject: ...


class ControlApiHttpService(Protocol):
    """Complete, explicit service boundary accepted by the HTTP adapter."""

    def capabilities_payload(self) -> JsonObject: ...

    def event_bus(self) -> ControlApiEventBusPort: ...

    def idempotency_store(self) -> ControlApiIdempotencyStorePort: ...

    def rate_limiter(self) -> ControlApiRateLimiterPort: ...

    def state_token(self) -> str: ...

    def control_command_from_payload(
        self,
        payload: JsonObject,
        source: ControlCommandSource = "http",
    ) -> ControlCommand: ...

    def handle_control_command(self, command: ControlCommand) -> ControlResult: ...

    def publish_command_event(
        self,
        command: AuditCommand,
        result: AuditResult,
        *,
        replayed: bool = False,
    ) -> None: ...

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
    ) -> JsonObject: ...

    def automation_payload(self) -> JsonObject: ...

    def build_payload(self) -> JsonObject: ...

    def config_effective_payload(self) -> JsonObject: ...

    def contracts_payload(self) -> JsonObject: ...

    def dbus_diagnostics_payload(self) -> JsonObject: ...

    def event_snapshot_payload(self) -> JsonObject: ...

    def health_payload(self) -> JsonObject: ...

    def healthz_payload(self) -> JsonObject: ...

    def operational_payload(self) -> JsonObject: ...

    def runtime_payload(self) -> JsonObject: ...

    def summary_payload(self) -> JsonObject: ...

    def topology_payload(self) -> JsonObject: ...

    def update_payload(self) -> JsonObject: ...

    def version_payload(self) -> JsonObject: ...

    def victron_bias_recommendation_payload(self) -> JsonObject: ...


def optional_error_payload(response_payload: Mapping[str, Any]) -> JsonObject | None:
    """Return the nested error object from one API response payload."""
    error = response_payload.get("error")
    if not isinstance(error, Mapping):
        return None
    return {str(key): value for key, value in error.items()}
