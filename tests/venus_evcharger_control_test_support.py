# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for live Control API tests."""

from __future__ import annotations

import configparser
import time
from collections.abc import Mapping
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.control import (
    ControlApiAuditTrail,
    CONTROL_API_COMMAND_SCOPE_REQUIREMENTS,
    ControlApiEventBus,
    ControlApiIdempotencyStore,
    ControlApiRateLimiter,
    ControlApiV1Service,
    ControlCommand,
    ControlResult,
    LocalControlApiHttpServer,
)
from venus_evcharger.control.models import ControlCommandSource


def _auto_runtime_setting_paths() -> set[str]:
    return set().union(
        ControlApiV1Service._FLOAT_AUTO_RUNTIME_PATHS,
        ControlApiV1Service._STRING_AUTO_RUNTIME_PATHS,
        ControlApiV1Service._BINARY_AUTO_RUNTIME_PATHS,
        ControlApiV1Service._INTEGER_AUTO_RUNTIME_PATHS,
    )


class LiveControlApiTestService:
    """Small in-memory service used by end-to-end API smoke tests."""

    def __init__(self, *, audit_path: str = "", idempotency_path: str = "") -> None:
        self._mapper = ControlApiV1Service(
            current_setting_paths=("/SetCurrent",),
            auto_runtime_setting_paths=_auto_runtime_setting_paths(),
        )
        self._event_bus = ControlApiEventBus()
        self._audit_trail = ControlApiAuditTrail(history_limit=50, path=audit_path)
        self._idempotency_store = ControlApiIdempotencyStore(history_limit=50, path=idempotency_path)
        self._rate_limiter = ControlApiRateLimiter(max_requests=50, window_seconds=5.0, critical_cooldown_seconds=0.0)
        self.product_name = "Venus EV Charger Service"
        self.service_name = "com.victronenergy.evcharger.http.test"
        self.connection_name = "test-connection"
        self.config = configparser.ConfigParser()
        self.config.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=shelly_contactor_switch

[Measurement]
Type=actuator_native
"""
        )
        self.supported_phase_selections = ("P1", "P1_P2", "P1_P2_P3")
        self.control_api_enabled = True
        self.control_api_localhost_only = True
        self.control_api_audit_path = audit_path
        self.control_api_idempotency_path = idempotency_path
        self.control_api_bound_unix_socket_path = ""
        self.control_api_listen_host = ""
        self.control_api_listen_port = 0
        self.virtual_mode = 0
        self.virtual_enable = 1
        self.virtual_startstop = 1
        self.virtual_autostart = 1
        self.requested_phase_selection = "P1"
        self.active_phase_selection = "P1"
        self.current_setting = 6.0
        self.runtime_settings: dict[str, Any] = {"/Auto/StartSurplusWatts": 1700.0}
        self._software_update_state = "idle"
        self._revision = 0

    def control_command_from_payload(
        self,
        payload: dict[str, Any],
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        return self._mapper.command_from_payload(payload, source=source)

    def handle_control_command(self, command: ControlCommand) -> ControlResult:
        result = self._command_result(command)
        self._revision += 1
        self.publish_command_event(command, result)
        self._publish_control_api_state_event()
        return result

    def _command_result(self, command: ControlCommand) -> ControlResult:
        handler = self._command_handlers().get(command.name)
        if handler is None:
            return ControlResult.rejected_result(command, detail="unsupported in live smoke service")
        return handler(command)

    def _command_handlers(self) -> dict[str, Any]:
        return {
            "set_mode": self._apply_mode_command,
            "set_current_setting": self._apply_current_command,
            "set_auto_start": self._apply_autostart_command,
            "set_phase_selection": self._apply_phase_selection_command,
            "set_auto_runtime_setting": self._apply_runtime_setting_command,
            "trigger_software_update": self._apply_software_update_command,
        }

    def _apply_mode_command(self, command: ControlCommand) -> ControlResult:
        self.virtual_mode = int(command.value)
        return ControlResult.applied_result(command, detail="mode updated")

    def _apply_current_command(self, command: ControlCommand) -> ControlResult:
        self.current_setting = float(command.value)
        return ControlResult.applied_result(command, detail="current updated")

    def _apply_autostart_command(self, command: ControlCommand) -> ControlResult:
        self.virtual_autostart = 1 if bool(command.value) else 0
        return ControlResult.applied_result(command, detail="autostart updated")

    def _apply_phase_selection_command(self, command: ControlCommand) -> ControlResult:
        self.requested_phase_selection = str(command.value)
        self.active_phase_selection = str(command.value)
        return ControlResult.applied_result(command, detail="phase selection updated")

    def _apply_runtime_setting_command(self, command: ControlCommand) -> ControlResult:
        self.runtime_settings[command.path] = command.value
        return ControlResult.applied_result(command, detail="runtime setting updated")

    def _apply_software_update_command(self, command: ControlCommand) -> ControlResult:
        self._software_update_state = "running"
        return ControlResult.accepted_in_flight_result(command, detail="update started")

    def summary_payload(self) -> dict[str, Any]:
        return {"ok": True, "api_version": "v1", "kind": "summary", "summary": f"mode={self.virtual_mode}"}

    def runtime_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "runtime",
            "state": {
                "mode": self.virtual_mode,
                "current_setting": self.current_setting,
                "runtime_settings": dict(self.runtime_settings),
            },
        }

    def operational_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "operational",
            "state": {
                "mode": self.virtual_mode,
                "enable": self.virtual_enable,
                "startstop": self.virtual_startstop,
                "autostart": self.virtual_autostart,
                "active_phase_selection": self.active_phase_selection,
                "requested_phase_selection": self.requested_phase_selection,
            },
        }

    def dbus_diagnostics_payload(self) -> dict[str, Any]:
        return {"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {"writes": self._revision}}

    def victron_bias_recommendation_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "victron-bias-recommendation",
            "state": {"available": False},
        }

    def automation_payload(self) -> dict[str, Any]:
        operational = self.operational_payload()
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "automation",
            "state": {
                "state_token": self.state_token(),
                "command_endpoint": "/v1/control/command",
                "events_endpoint": "/v1/events",
                "safe_write": {
                    "if_match_header": "If-Match",
                    "idempotency_key_header": "Idempotency-Key",
                    "command_id_header": "X-Command-Id",
                },
                "writable": {
                    "command_names": sorted(ControlApiV1Service._COMMAND_NAMES),
                    "scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
                },
                "operational": dict(operational["state"]),
                "auto_decision": {},
                "health": dict(self.health_payload()["state"]),
                "topology": dict(self.topology_payload()["state"]),
                "diagnostics": dict(self.dbus_diagnostics_payload()["state"]),
            },
        }

    def topology_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "topology",
            "state": {
                "backend_mode": backend_mode_for_service(self, "combined"),
                "meter_backend": backend_type_for_service(self, "meter", "na"),
                "switch_backend": backend_type_for_service(self, "switch", "na"),
                "charger_backend": backend_type_for_service(self, "charger", "na"),
                "supported_phase_selections": list(self.supported_phase_selections),
            },
        }

    def update_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "update",
            "state": {"state": self._software_update_state, "available": False},
        }

    def config_effective_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "config-effective",
            "state": {
                "control_api_enabled": self.control_api_enabled,
                "control_api_localhost_only": self.control_api_localhost_only,
                "control_api_audit_path": self.control_api_audit_path,
                "control_api_idempotency_path": self.control_api_idempotency_path,
            },
        }

    def healthz_payload(self) -> dict[str, Any]:
        return {"ok": True, "api_version": "v1", "kind": "healthz", "state": {"alive": True}}

    def version_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "version",
            "state": {"service_version": "test", "api_version": "v1"},
        }

    def build_payload(self) -> dict[str, Any]:
        return {"ok": True, "api_version": "v1", "kind": "build", "state": {"product_name": self.product_name}}

    def contracts_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "contracts",
            "state": {"active_api_version": "v1", "openapi_endpoint": "/v1/openapi.json"},
        }

    def health_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "health",
            "state": {
                "control_api_enabled": self.control_api_enabled,
                "control_api_running": True,
                "listen_host": self.control_api_listen_host,
                "listen_port": self.control_api_listen_port,
                "unix_socket_path": self.control_api_bound_unix_socket_path,
                "command_audit_entries": self._audit_trail.count(),
                "idempotency_entries": self._idempotency_store.count(),
            },
        }

    def event_snapshot_payload(self) -> dict[str, Any]:
        return {
            "summary": self.summary_payload(),
            "operational": self.operational_payload(),
            "health": self.health_payload(),
            "update": self.update_payload(),
            "topology": self.topology_payload(),
        }

    def capabilities_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "transport": "http",
            "auth_required": True,
            "read_auth_required": True,
            "control_auth_required": True,
            "localhost_only": True,
            "unix_socket_path": self.control_api_bound_unix_socket_path,
            "auth_header": "Authorization: Bearer <token>",
            "auth_scopes": ["read", "control_basic", "control_admin", "update_admin"],
            "command_names": sorted(ControlApiV1Service._COMMAND_NAMES),
            "command_scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
            "command_sources": ["http"],
            "state_endpoints": ["/v1/state/automation", "/v1/state/summary", "/v1/state/runtime", "/v1/state/health"],
            "endpoints": ["/v1/control/health", "/v1/control/command", "/v1/events", "/v1/state/summary"],
            "available_modes": [0, 1, 2],
            "supported_phase_selections": list(self.supported_phase_selections),
            "features": {"event_stream": True, "optimistic_concurrency": True},
            "topology": {"backend_mode": backend_mode_for_service(self, "combined")},
            "versioning": {"stable_endpoints": ["/v1/control/command"], "experimental_endpoints": ["/v1/events"]},
        }

    def state_token(self) -> str:
        return f"rev-{self._revision}"

    def event_bus(self) -> ControlApiEventBus:
        return self._event_bus

    def _control_api_audit_trail(self) -> ControlApiAuditTrail:
        return self._audit_trail

    def idempotency_store(self) -> ControlApiIdempotencyStore:
        return self._idempotency_store

    def rate_limiter(self) -> ControlApiRateLimiter:
        return self._rate_limiter

    def publish_command_event(self, command: ControlCommand, result: ControlResult, *, replayed: bool = False) -> None:
        self._event_bus.publish(
            "command",
            {
                "command": {
                    "name": command.name,
                    "path": command.path,
                    "value": command.value,
                    "source": command.source,
                    "detail": command.detail,
                    "command_id": command.command_id,
                    "idempotency_key": command.idempotency_key,
                },
                "result": {
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

    def record_command_audit(
        self,
        *,
        command: ControlCommand | Mapping[str, Any] | None,
        result: ControlResult | Mapping[str, Any] | None,
        error: dict[str, Any] | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
        transport: str = "http",
    ) -> dict[str, Any]:
        return self._audit_trail.append(
            {
                "timestamp": time.time(),
                "transport": transport,
                "scope": scope,
                "client_host": client_host,
                "status_code": status_code,
                "replayed": replayed,
                "command": self._command_payload(command),
                "result": self._result_payload(result),
                "error": dict(error or {}),
            }
        )

    @staticmethod
    def _command_payload(command: ControlCommand | Mapping[str, Any] | None) -> dict[str, Any]:
        if isinstance(command, Mapping):
            return dict(command)
        if command is None:
            return {}
        return {
            "name": command.name,
            "path": command.path,
            "value": command.value,
            "source": command.source,
            "detail": command.detail,
            "command_id": command.command_id,
            "idempotency_key": command.idempotency_key,
        }

    @staticmethod
    def _result_payload(result: ControlResult | Mapping[str, Any] | None) -> dict[str, Any]:
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

    def _publish_control_api_state_event(self) -> None:
        self._event_bus.publish("state", self.event_snapshot_payload())


@contextmanager
def started_control_api_server(*, unix_socket: bool = False) -> Iterator[tuple[LiveControlApiTestService, LocalControlApiHttpServer]]:
    with TemporaryDirectory() as tmpdir:
        audit_path = f"{tmpdir}/control-audit.jsonl"
        idempotency_path = f"{tmpdir}/idempotency.json"
        socket_path = f"{tmpdir}/control.sock"
        service = LiveControlApiTestService(audit_path=audit_path, idempotency_path=idempotency_path)
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=0,
            read_token="read-token",
            control_token="control-token",
            admin_token="admin-token",
            update_token="update-token",
            localhost_only=True,
            unix_socket_path=socket_path if unix_socket else "",
        )
        server.start()
        service.control_api_listen_host = server.bound_host
        service.control_api_listen_port = server.bound_port
        service.control_api_bound_unix_socket_path = server.bound_unix_socket_path
        try:
            yield service, server
        finally:
            server.stop()


__all__ = [name for name in globals() if not name.startswith("__")]
