# SPDX-License-Identifier: GPL-3.0-or-later
"""Meta and health state payload helpers for the Control API role."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.core.common import evse_fault_reason
from venus_evcharger.control import CONTROL_API_COMMAND_SCOPE_REQUIREMENTS
from venus_evcharger.core.contracts import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
    normalized_fault_state,
)
from venus_evcharger.service.control_state_config import ControlStateConfig
from venus_evcharger.service.control_state_core import ControlStateCore
from venus_evcharger.service.control_state_operational import ControlStateOperational
from venus_evcharger.service.control_state_victron import ControlStateVictron

_AUTOMATION_DIAGNOSTIC_KEYS = (
    "/Status",
    "/Auto/Health",
    "/Auto/HealthCode",
    "/Auto/DecisionReason",
    "/Auto/DecisionState",
    "/Auto/DecisionRelayIntent",
    "/Auto/DecisionSurplusWatts",
    "/Auto/DecisionGridWatts",
    "/Auto/DecisionSocPercent",
    "/Auto/DecisionStartThresholdWatts",
    "/Auto/DecisionStopThresholdWatts",
    "/Auto/ScheduledState",
    "/Auto/ScheduledReason",
    "/Auto/PhaseLockoutActive",
    "/Auto/PhaseLockoutReason",
    "/Auto/ContactorLockoutActive",
    "/Auto/ContactorLockoutReason",
    "/Auto/LastShellyReadAge",
    "/Auto/LastSuccessfulUpdateAge",
)


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _payload_state(payload: dict[str, Any]) -> dict[str, Any]:
    return _mapping_value(payload.get("state"))


def _automation_diagnostics_subset(diagnostics_state: dict[str, Any]) -> dict[str, Any]:
    return {key: diagnostics_state[key] for key in _AUTOMATION_DIAGNOSTIC_KEYS if key in diagnostics_state}


def _optional_bool_attr(service: object, name: str) -> bool:
    return bool(getattr(service, name)) if hasattr(service, name) else False


def _optional_int_attr(service: object, name: str) -> int:
    return int(getattr(service, name)) if hasattr(service, name) else 0


def _optional_text_attr(service: object, name: str, default: str = "") -> str:
    return str(getattr(service, name)).strip() if hasattr(service, name) else default


def _configured_phase_selections(service: object) -> tuple[str, ...]:
    configured = getattr(service, "supported_phase_selections") if hasattr(service, "supported_phase_selections") else None
    return tuple(configured) if configured else ("P1",)


class ControlStateMeta:
    """Compose meta, health, and aggregate payloads from explicit providers."""

    def __init__(
        self,
        service: Any,
        core: ControlStateCore,
        operational: ControlStateOperational,
        config: ControlStateConfig,
        victron: ControlStateVictron,
        *,
        audit_count: Callable[[], int],
        idempotency_count: Callable[[], int],
        control_running: Callable[[], bool],
    ) -> None:
        self.service = service
        self.core = core
        self.operational = operational
        self.config = config
        self.victron = victron
        self._audit_count = audit_count
        self._idempotency_count = idempotency_count
        self._control_running = control_running

    def healthz_payload(self) -> dict[str, Any]:
        service = self.service
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "healthz",
            "state": {
                "alive": True,
                "control_api_enabled": _optional_bool_attr(service, "control_api_enabled"),
                "control_api_running": self._control_running(),
            },
        }

    def version_payload(self) -> dict[str, Any]:
        service = self.service
        current_version = _optional_text_attr(service, "_software_update_current_version")
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "version",
            "state": {
                "service_version": current_version or _optional_text_attr(service, "firmware_version"),
                "api_version": "v1",
                "product_name": _optional_text_attr(service, "product_name"),
                "instance_id": _optional_int_attr(service, "deviceinstance"),
            },
        }

    def build_payload(self) -> dict[str, Any]:
        service = self.service
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "build",
            "state": {
                "product_name": _optional_text_attr(service, "product_name"),
                "hardware_version": _optional_text_attr(service, "hardware_version"),
                "firmware_version": _optional_text_attr(service, "firmware_version"),
                "connection_name": _optional_text_attr(service, "connection_name"),
                "runtime_state_path": _optional_text_attr(service, "runtime_state_path"),
            },
        }

    @staticmethod
    def contracts_payload() -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "contracts",
            "state": {
                "active_api_version": "v1",
                "openapi_endpoint": "/v1/openapi.json",
                "capabilities_endpoint": "/v1/capabilities",
                "versioning_document": "API_VERSIONING.md",
                "control_document": "CONTROL_API.md",
                "state_document": "STATE_API.md",
                "stable_endpoints": sorted(CONTROL_API_STABLE_ENDPOINTS),
                "experimental_endpoints": sorted(CONTROL_API_EXPERIMENTAL_ENDPOINTS),
            },
        }

    def automation_payload(self) -> dict[str, Any]:
        operational = self.operational.payload()
        health = self.health_payload()
        topology = self.core.topology_payload()
        diagnostics = self.core.dbus_diagnostics_payload()
        operational_state = _payload_state(operational)
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "automation",
            "state": {
                "state_token": self.state_token(),
                "command_endpoint": "/v1/control/command",
                "events_endpoint": "/v1/events",
                "state_endpoints": sorted(CONTROL_API_STATE_ENDPOINTS),
                "safe_write": {
                    "if_match_header": "If-Match",
                    "state_token_header": "X-State-Token",
                    "idempotency_key_header": "Idempotency-Key",
                    "command_id_header": "X-Command-Id",
                    "recommended_flow": "read /v1/state/automation, then POST command with If-Match and Idempotency-Key",
                },
                "writable": {
                    "command_names": sorted(CONTROL_COMMAND_NAMES),
                    "scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
                },
                "operational": operational_state,
                "auto_decision": _mapping_value(operational_state.get("auto_decision")),
                "health": _payload_state(health),
                "topology": _payload_state(topology),
                "diagnostics": _automation_diagnostics_subset(_payload_state(diagnostics)),
            },
        }

    def health_payload(self) -> dict[str, Any]:
        service = self.service
        now = time.time()
        stale = service.runtime.update_is_stale(now)
        health_reason = _optional_text_attr(service, "_last_health_reason", "init")
        fault_reason, fault_active = normalized_fault_state(
            evse_fault_reason(_optional_text_attr(service, "_last_health_reason"))
        )
        return {
            "ok": True,
            "api_version": "v1",
            "kind": "health",
            "state": {
                "health_reason": health_reason,
                "health_code": _optional_int_attr(service, "_last_health_code"),
                "fault_active": bool(fault_active),
                "fault_reason": fault_reason,
                "runtime_overrides_active": _optional_bool_attr(service, "_runtime_overrides_active"),
                "control_api_enabled": _optional_bool_attr(service, "control_api_enabled"),
                "control_api_running": self._control_running(),
                "control_api_transport": "http",
                "listen_host": _optional_text_attr(service, "control_api_listen_host"),
                "listen_port": _optional_int_attr(service, "control_api_listen_port"),
                "unix_socket_path": _optional_text_attr(service, "control_api_bound_unix_socket_path"),
                "control_api_localhost_only": (
                    bool(getattr(service, "control_api_localhost_only"))
                    if hasattr(service, "control_api_localhost_only")
                    else True
                ),
                "command_audit_entries": self._audit_count(),
                "command_audit_path": _optional_text_attr(service, "control_api_audit_path"),
                "idempotency_entries": self._idempotency_count(),
                "idempotency_path": _optional_text_attr(service, "control_api_idempotency_path"),
                "update_stale": stale,
                "last_successful_update_at": getattr(service, "_last_successful_update_at", None),
                "last_recovery_attempt_at": getattr(service, "_last_recovery_attempt_at", None),
            },
        }

    def event_snapshot_payload(self) -> dict[str, Any]:
        return {
            "summary": self.core.summary_payload(),
            "operational": self.operational.payload(),
            "health": self.health_payload(),
            "update": self.core.update_payload(),
            "topology": self.core.topology_payload(),
        }

    def capabilities_payload(self) -> dict[str, Any]:
        service = self.service
        supported_phase_selections = _configured_phase_selections(service)
        topology = {
            "backend_mode": backend_mode_for_service(service, "combined"),
            "meter_backend": backend_type_for_service(service, "meter", "na"),
            "switch_backend": backend_type_for_service(service, "switch", "na"),
            "charger_backend": backend_type_for_service(service, "charger", "na"),
        }
        features = {
            "command_audit_trail": True,
            "dbus_diagnostics_state": True,
            "event_stream": True,
            "event_kind_filters": True,
            "event_retry_hints": True,
            "http_control_command": True,
            "idempotency_tracking": True,
            "optimistic_concurrency": True,
            "per_command_request_schemas": True,
            "rate_limiting": True,
            "runtime_only_idempotency_persistence": True,
            "multi_phase_selection": len(supported_phase_selections) > 1,
            "phase_selection_write": bool(supported_phase_selections),
            "read_api": True,
            "runtime_override_write": True,
            "software_update_trigger": True,
            "state_reads": True,
        }
        read_token = _optional_text_attr(service, "control_api_read_token")
        control_token = _optional_text_attr(service, "control_api_control_token")
        legacy_token = _optional_text_attr(service, "control_api_auth_token")
        effective_control_token = control_token or legacy_token
        effective_read_token = read_token or effective_control_token
        return {
            "ok": True,
            "api_version": "v1",
            "transport": "http",
            "auth_required": bool(effective_read_token or effective_control_token),
            "read_auth_required": bool(effective_read_token),
            "control_auth_required": bool(effective_control_token),
            "localhost_only": (
                bool(getattr(service, "control_api_localhost_only"))
                if hasattr(service, "control_api_localhost_only")
                else True
            ),
            "unix_socket_path": _optional_text_attr(service, "control_api_bound_unix_socket_path"),
            "auth_header": "Authorization: Bearer <token>",
            "auth_scopes": ["control_admin", "control_basic", "read", "update_admin"],
            "command_names": sorted(CONTROL_COMMAND_NAMES),
            "command_scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
            "command_sources": sorted(CONTROL_COMMAND_SOURCES),
            "state_endpoints": sorted(CONTROL_API_STATE_ENDPOINTS),
            "endpoints": sorted(CONTROL_API_ENDPOINTS),
            "available_modes": [0, 1, 2],
            "supported_phase_selections": list(supported_phase_selections),
            "features": features,
            "topology": topology,
            "versioning": {
                "stable_endpoints": sorted(CONTROL_API_STABLE_ENDPOINTS),
                "experimental_endpoints": sorted(CONTROL_API_EXPERIMENTAL_ENDPOINTS),
                "breaking_change_policy": (
                    "Stable v1 endpoints require a version bump for breaking changes; "
                    "experimental endpoints may evolve within v1."
                ),
            },
        }

    def state_token_payload(self) -> dict[str, Any]:
        return self.event_snapshot_payload()

    def state_token(self) -> str:
        encoded = json.dumps(
            self.state_token_payload(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()
