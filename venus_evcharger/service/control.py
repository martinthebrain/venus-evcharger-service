# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed Control API boundary for the wallbox service."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.control import (
    ControlApiIdempotencyStore,
    ControlApiRateLimiter,
    ControlCommand,
    ControlResult,
    LocalControlApiHttpServer,
)
from venus_evcharger.control.events import ControlApiEventBus
from venus_evcharger.control.http_api_command_contracts import AuditCommand, AuditResult, JsonObject
from venus_evcharger.control.models import ControlCommandSource

from .control_runtime import ControlRuntime
from .control_state_config import ControlStateConfig
from .control_state_core import ControlStateCore
from .control_state_meta import ControlStateMeta
from .control_state_operational import ControlStateOperational
from .control_state_victron import ControlStateVictron

__all__ = ["LocalControlApiHttpServer", "ServiceControlFacade"]


class _ControlAutoPort(Protocol):
    def handle_command(self, command: ControlCommand) -> ControlResult: ...


class _ControlServicePort(Protocol):
    @property
    def auto(self) -> _ControlAutoPort: ...


class ServiceControlFacade:
    """Own Control API state builders, stores, events, and transport lifecycle."""

    def __init__(self, service: _ControlServicePort) -> None:
        self.service = service
        self.core = ControlStateCore(service)
        self.operational = ControlStateOperational(service)
        self.config = ControlStateConfig(service)
        self.victron = ControlStateVictron(service)
        self.runtime = ControlRuntime(service, self)
        self.meta = ControlStateMeta(
            service,
            self.core,
            self.operational,
            self.config,
            self.victron,
            audit_count=lambda: self.runtime.audit_trail().count(),
            idempotency_count=lambda: self.runtime.idempotency_store().count(),
            control_running=lambda: self.runtime.running,
        )

    def start_server(self) -> None:
        self.runtime.start_server(self.meta.event_snapshot_payload())

    def stop_server(self) -> None:
        self.runtime.stop_server()

    def publish_command_event(
        self,
        command: AuditCommand,
        result: AuditResult,
        *,
        replayed: bool = False,
    ) -> None:
        self.runtime.publish_command_event(command, result, replayed=replayed)

    def control_command_from_payload(
        self,
        payload: JsonObject,
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        return self.core.command_from_payload(payload, source)

    def handle_control_command(self, command: ControlCommand) -> ControlResult:
        return self.service.auto.handle_command(command)

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
        return self.runtime.record_command_audit(
            command=command,
            result=result,
            error=error,
            replayed=replayed,
            scope=scope,
            client_host=client_host,
            status_code=status_code,
            transport=transport,
        )

    def idempotency_store(self) -> ControlApiIdempotencyStore:
        return self.runtime.idempotency_store()

    def rate_limiter(self) -> ControlApiRateLimiter:
        return self.runtime.rate_limiter()

    def event_bus(self) -> ControlApiEventBus:
        return self.runtime.event_bus()

    def state_token(self) -> str:
        return self.meta.state_token()

    def capabilities_payload(self) -> JsonObject:
        return self.meta.capabilities_payload()

    def automation_payload(self) -> JsonObject:
        return self.meta.automation_payload()

    def build_payload(self) -> JsonObject:
        return self.meta.build_payload()

    def config_effective_payload(self) -> JsonObject:
        return self.config.effective_payload()

    def contracts_payload(self) -> JsonObject:
        return self.meta.contracts_payload()

    def dbus_diagnostics_payload(self) -> JsonObject:
        return self.core.dbus_diagnostics_payload()

    def event_snapshot_payload(self) -> JsonObject:
        return self.meta.event_snapshot_payload()

    def health_payload(self) -> JsonObject:
        return self.meta.health_payload()

    def healthz_payload(self) -> JsonObject:
        return self.meta.healthz_payload()

    def operational_payload(self) -> JsonObject:
        return self.operational.payload()

    def runtime_payload(self) -> JsonObject:
        return self.core.runtime_payload()

    def summary_payload(self) -> JsonObject:
        return self.core.summary_payload()

    def topology_payload(self) -> JsonObject:
        return self.core.topology_payload()

    def update_payload(self) -> JsonObject:
        return self.core.update_payload()

    def version_payload(self) -> JsonObject:
        return self.meta.version_payload()

    def victron_bias_recommendation_payload(self) -> JsonObject:
        return self.victron.recommendation_payload()
