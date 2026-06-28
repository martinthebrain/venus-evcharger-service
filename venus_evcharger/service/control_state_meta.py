# SPDX-License-Identifier: GPL-3.0-or-later
"""Meta and health state payload helpers for the Control API mixin."""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from typing import TYPE_CHECKING, Any, Callable

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
    normalized_control_api_capabilities_fields,
    normalized_fault_state,
    normalized_state_api_health_fields,
    normalized_state_api_kind,
)

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


class _ControlApiStateMetaMixin:
    if TYPE_CHECKING:  # pragma: no cover
        control_api_enabled: bool
        control_api_listen_host: str
        control_api_listen_port: int
        control_api_bound_unix_socket_path: str
        control_api_localhost_only: bool
        control_api_audit_path: str
        control_api_idempotency_path: str
        control_api_read_token: str
        control_api_control_token: str
        control_api_auth_token: str
        supported_phase_selections: tuple[str, ...]
        product_name: str
        hardware_version: str
        firmware_version: str
        service_name: str
        connection_name: str
        runtime_state_path: str

        def _state_api_summary_payload(self) -> dict[str, Any]: ...

        def _state_api_operational_payload(self) -> dict[str, Any]: ...

        def _state_api_topology_payload(self) -> dict[str, Any]: ...

        def _state_api_update_payload(self) -> dict[str, Any]: ...

        def _state_api_dbus_diagnostics_payload(self) -> dict[str, Any]: ...

        def _control_api_audit_trail(self) -> Any: ...

        def _control_api_idempotency_store(self) -> Any: ...

        def _is_update_stale(self, now: float | None = None) -> bool: ...

    def _state_api_healthz_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("healthz", default="healthz"),
            "state": {
                "alive": True,
                "control_api_enabled": bool(getattr(self, "control_api_enabled", False)),
                "control_api_running": bool(getattr(self, "_control_api_server", None)),
            },
        }

    def _state_api_version_payload(self) -> dict[str, Any]:
        current_version = str(getattr(self, "_software_update_current_version", "")).strip()
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("version", default="version"),
            "state": {
                "service_version": current_version or str(getattr(self, "firmware_version", "")).strip(),
                "api_version": "v1",
                "product_name": str(getattr(self, "product_name", "")).strip(),
                "service_name": str(getattr(self, "service_name", "")).strip(),
            },
        }

    def _state_api_build_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("build", default="build"),
            "state": {
                "product_name": str(getattr(self, "product_name", "")).strip(),
                "hardware_version": str(getattr(self, "hardware_version", "")).strip(),
                "firmware_version": str(getattr(self, "firmware_version", "")).strip(),
                "connection_name": str(getattr(self, "connection_name", "")).strip(),
                "runtime_state_path": str(getattr(self, "runtime_state_path", "")).strip(),
            },
        }

    def _state_api_contracts_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("contracts", default="contracts"),
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

    def _state_api_automation_payload(self) -> dict[str, Any]:
        operational = self._state_api_operational_payload()
        health = self._state_api_health_payload()
        topology = self._state_api_topology_payload()
        diagnostics = self._state_api_dbus_diagnostics_payload()
        operational_state = _payload_state(operational)
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("automation", default="automation"),
            "state": {
                "state_token": self._control_api_state_token(),
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

    def _state_api_health_payload(self) -> dict[str, Any]:
        now = time.time()
        stale = False
        is_update_stale = getattr(self, "_is_update_stale", None)
        if callable(is_update_stale):
            stale = bool(is_update_stale(now))
        fault_reason, fault_active = normalized_fault_state(
            evse_fault_reason(getattr(self, "_last_health_reason", ""))
        )
        return normalized_state_api_health_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "health",
                "state": {
                    "health_reason": getattr(self, "_last_health_reason", "init"),
                    "health_code": getattr(self, "_last_health_code", 0),
                    "fault_active": fault_active,
                    "fault_reason": fault_reason,
                    "runtime_overrides_active": getattr(self, "_runtime_overrides_active", False),
                    "control_api_enabled": getattr(self, "control_api_enabled", False),
                    "control_api_running": bool(getattr(self, "_control_api_server", None)),
                    "control_api_transport": "http",
                    "listen_host": getattr(self, "control_api_listen_host", ""),
                    "listen_port": getattr(self, "control_api_listen_port", 0),
                    "unix_socket_path": getattr(self, "control_api_bound_unix_socket_path", ""),
                    "control_api_localhost_only": getattr(self, "control_api_localhost_only", True),
                    "command_audit_entries": self._control_api_audit_trail().count(),
                    "command_audit_path": getattr(self, "control_api_audit_path", ""),
                    "idempotency_entries": self._control_api_idempotency_store().count(),
                    "idempotency_path": getattr(self, "control_api_idempotency_path", ""),
                    "update_stale": stale,
                    "last_successful_update_at": getattr(self, "_last_successful_update_at", None),
                    "last_recovery_attempt_at": getattr(self, "_last_recovery_attempt_at", None),
                },
            }
        )

    def _state_api_event_snapshot_payload(self) -> dict[str, Any]:
        return {
            "summary": self._state_api_summary_payload(),
            "operational": self._state_api_operational_payload(),
            "health": self._state_api_health_payload(),
            "update": self._state_api_update_payload(),
            "topology": self._state_api_topology_payload(),
        }

    def _control_api_capabilities_payload(self) -> dict[str, Any]:
        supported_phase_selections = tuple(getattr(self, "supported_phase_selections", ("P1",)) or ("P1",))
        topology = {
            "backend_mode": backend_mode_for_service(self, "combined"),
            "meter_backend": backend_type_for_service(self, "meter", "na"),
            "switch_backend": backend_type_for_service(self, "switch", "na"),
            "charger_backend": backend_type_for_service(self, "charger", "na"),
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
        read_token = str(getattr(self, "control_api_read_token", "")).strip()
        control_token = str(getattr(self, "control_api_control_token", "")).strip()
        legacy_token = str(getattr(self, "control_api_auth_token", "")).strip()
        effective_control_token = control_token or legacy_token
        effective_read_token = read_token or effective_control_token
        return normalized_control_api_capabilities_fields(
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "auth_required": bool(effective_read_token or effective_control_token),
                "read_auth_required": bool(effective_read_token),
                "control_auth_required": bool(effective_control_token),
                "localhost_only": bool(getattr(self, "control_api_localhost_only", True)),
                "unix_socket_path": getattr(self, "control_api_bound_unix_socket_path", ""),
                "auth_header": "Authorization: Bearer <token>",
                "auth_scopes": ["read", "control_basic", "control_admin", "update_admin"],
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
        )

    def _control_api_state_token_payload(self) -> dict[str, Any]:
        return self._state_api_event_snapshot_payload()

    def _control_api_state_token(self) -> str:
        encoded = json.dumps(
            self._control_api_state_token_payload(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _control_api_server_factory() -> Any:
        control_module = importlib.import_module("venus_evcharger.service.control")
        return getattr(control_module, "LocalControlApiHttpServer")
