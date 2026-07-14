# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the local Control API v1 payloads."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from venus_evcharger.core.contracts_basic import non_negative_int, normalize_binary_flag
from venus_evcharger.core.contracts_basic import non_negative_float_or_none

CONTROL_API_VERSIONS = frozenset({"v1"})
CONTROL_API_TRANSPORTS = frozenset({"http"})
CONTROL_API_AUTH_SCOPES = frozenset({"read", "control_basic", "control_admin", "update_admin"})
CONTROL_API_ERROR_CODES = frozenset(
    {
        "bad_request",
        "blocked_by_health",
        "blocked_by_mode",
        "command_rejected",
        "conflict",
        "cooldown_active",
        "forbidden_remote_client",
        "idempotency_conflict",
        "invalid_content_length",
        "invalid_json",
        "invalid_payload",
        "insufficient_scope",
        "not_found",
        "rate_limited",
        "unauthorized",
        "unsupported_command",
        "unsupported_for_topology",
        "update_in_progress",
        "validation_error",
    }
)
CONTROL_API_STATE_ENDPOINTS = frozenset(
    {
        "/v1/state/automation",
        "/v1/state/build",
        "/v1/state/config-effective",
        "/v1/state/contracts",
        "/v1/state/dbus-diagnostics",
        "/v1/state/health",
        "/v1/state/healthz",
        "/v1/state/operational",
        "/v1/state/runtime",
        "/v1/state/summary",
        "/v1/state/topology",
        "/v1/state/update",
        "/v1/state/version",
        "/v1/state/victron-bias-recommendation",
    }
)
CONTROL_API_ENDPOINTS = frozenset(
    {
        "/v1/capabilities",
        "/v1/control/command",
        "/v1/control/health",
        "/v1/events",
        "/v1/openapi.json",
        *CONTROL_API_STATE_ENDPOINTS,
    }
)
CONTROL_API_EXPERIMENTAL_ENDPOINTS = frozenset({"/v1/events"})
CONTROL_API_STABLE_ENDPOINTS = frozenset(endpoint for endpoint in CONTROL_API_ENDPOINTS if endpoint not in CONTROL_API_EXPERIMENTAL_ENDPOINTS)
CONTROL_COMMAND_NAMES = frozenset(
    {
        "legacy_unknown_write",
        "reset_contactor_lockout",
        "reset_phase_lockout",
        "set_auto_runtime_setting",
        "set_auto_start",
        "set_current_setting",
        "set_enable",
        "set_mode",
        "set_phase_selection",
        "set_start_stop",
        "trigger_software_update",
    }
)
CONTROL_COMMAND_SOURCES = frozenset({"dbus", "http", "internal", "mqtt"})
CONTROL_COMMAND_STATUSES = frozenset({"accepted_in_flight", "applied", "rejected"})
CONTROL_API_EVENT_KINDS = frozenset({"snapshot", "command", "state", "heartbeat"})


def _normalized_text(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return text or default


def _normalized_items(value: Any, fallback: Iterable[Any]) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return tuple(fallback)


def normalized_control_api_version(value: Any) -> str:
    version = _normalized_text(value)
    return version if version in CONTROL_API_VERSIONS else "v1"


def normalized_control_api_transport(value: Any) -> str:
    transport = _normalized_text(value).lower()
    return transport if transport in CONTROL_API_TRANSPORTS else "http"


def normalized_control_api_error_code(value: Any) -> str:
    code = _normalized_text(value).lower().replace(" ", "_").replace("-", "_")
    return code if code in CONTROL_API_ERROR_CODES else "bad_request"


def normalized_control_api_auth_scope(value: Any, *, default: str | None = None) -> str:
    scope = _normalized_text(value).lower()
    normalized_default = default if default in CONTROL_API_AUTH_SCOPES else "read"
    return scope if scope in CONTROL_API_AUTH_SCOPES else normalized_default


def normalized_control_command_name(value: Any) -> str:
    name = _normalized_text(value)
    return name if name in CONTROL_COMMAND_NAMES else "legacy_unknown_write"


def normalized_control_command_source(value: Any, *, default: str | None = None) -> str:
    source = _normalized_text(value).lower()
    normalized_default = "http" if default not in CONTROL_COMMAND_SOURCES else default
    return source if source in CONTROL_COMMAND_SOURCES else normalized_default


def normalized_control_command_status(value: Any) -> str:
    status = _normalized_text(value)
    return status if status in CONTROL_COMMAND_STATUSES else "rejected"


def normalized_control_command_fields(
    payload: Mapping[str, Any] | None,
    *,
    default_source: str | None = None,
) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "name": normalized_control_command_name(raw.get("name")),
        "path": _normalized_text(raw.get("path")),
        "value": raw.get("value"),
        "source": normalized_control_command_source(raw.get("source"), default=default_source),
        "detail": _normalized_text(raw.get("detail")),
        "command_id": _normalized_text(raw.get("command_id")),
        "idempotency_key": _normalized_text(raw.get("idempotency_key")),
    }


def _normalized_control_flag(raw: Mapping[str, Any], key: str, default: bool = False) -> bool:
    return bool(normalize_binary_flag(raw.get(key, default)))


def normalized_control_result_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    status = normalized_control_command_status(raw.get("status"))

    if status == "applied":
        accepted = True
        applied = True
        persisted = True
        reversible_failure = False
        external_side_effect_started = _normalized_control_flag(raw, "external_side_effect_started")
    elif status == "accepted_in_flight":
        accepted = True
        applied = False
        persisted = False
        reversible_failure = False
        external_side_effect_started = True
    else:
        accepted = False
        applied = False
        persisted = False
        reversible_failure = _normalized_control_flag(raw, "reversible_failure", True)
        external_side_effect_started = False

    return {
        "command": normalized_control_command_fields(raw.get("command")),
        "status": status,
        "accepted": accepted,
        "applied": applied,
        "persisted": persisted,
        "reversible_failure": reversible_failure,
        "external_side_effect_started": external_side_effect_started,
        "detail": _normalized_text(raw.get("detail")),
    }


def normalized_control_api_health_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    read_auth_required, control_auth_required = _normalized_control_auth_flags(raw)
    return {
        "ok": _normalized_control_flag(raw, "ok", True),
        "api_version": normalized_control_api_version(raw.get("api_version")),
        "transport": normalized_control_api_transport(raw.get("transport")),
        "listen_host": _normalized_text(raw.get("listen_host")),
        "listen_port": non_negative_int(raw.get("listen_port")),
        "auth_required": bool(read_auth_required or control_auth_required),
        "read_auth_required": read_auth_required,
        "control_auth_required": control_auth_required,
        "localhost_only": _normalized_control_flag(raw, "localhost_only", True),
        "unix_socket_path": _normalized_text(raw.get("unix_socket_path")),
    }


def normalized_control_api_error_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    details = raw.get("details")
    normalized_details: dict[str, Any] = {}
    if isinstance(details, Mapping):
        for key, value in details.items():
            normalized_details[str(key)] = value
    return {
        "code": normalized_control_api_error_code(raw.get("code")),
        "message": _normalized_text(raw.get("message") or raw.get("detail")),
        "retryable": _normalized_control_flag(raw, "retryable"),
        "details": normalized_details,
    }


def _normalized_bool_mapping(value: Any) -> dict[str, bool]:
    normalized: dict[str, bool] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized[str(key)] = bool(normalize_binary_flag(item))
    return normalized


def _normalized_text_mapping(value: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized[str(key)] = _normalized_text(item, "na")
    return normalized


def _normalized_phase_selections(value: Any) -> list[str]:
    return sorted({_normalized_text(item, "P1") for item in _normalized_items(value, ("P1",))})


def _normalized_command_names(value: Any) -> list[str]:
    return sorted({normalized_control_command_name(item) for item in _normalized_items(value, CONTROL_COMMAND_NAMES)})


def _normalized_command_sources(value: Any) -> list[str]:
    return sorted({normalized_control_command_source(item) for item in _normalized_items(value, CONTROL_COMMAND_SOURCES)})


def _normalized_allowed_endpoints(value: Any, allowed: frozenset[str]) -> list[str]:
    return sorted(
        {
            endpoint
            for endpoint in (_normalized_text(item) for item in _normalized_items(value, allowed))
            if endpoint in allowed
        }
        or allowed
    )


def _normalized_control_auth_flags(raw: Mapping[str, Any]) -> tuple[bool, bool]:
    read_auth_required = _normalized_control_flag(raw, "read_auth_required", _normalized_control_flag(raw, "auth_required"))
    control_auth_required = _normalized_control_flag(
        raw,
        "control_auth_required",
        read_auth_required,
    )
    return read_auth_required, control_auth_required


def _normalized_control_versioning(raw: Mapping[str, Any]) -> dict[str, Any]:
    versioning = raw.get("versioning")
    return {
        "stable_endpoints": _normalized_allowed_endpoints(
            versioning.get("stable_endpoints") if isinstance(versioning, Mapping) else (),
            CONTROL_API_STABLE_ENDPOINTS,
        ),
        "experimental_endpoints": _normalized_allowed_endpoints(
            versioning.get("experimental_endpoints") if isinstance(versioning, Mapping) else (),
            CONTROL_API_EXPERIMENTAL_ENDPOINTS,
        ),
        "breaking_change_policy": _normalized_text(
            versioning.get("breaking_change_policy") if isinstance(versioning, Mapping) else "",
            "Stable v1 endpoints require a version bump for breaking changes; experimental endpoints may evolve within v1.",
        ),
    }


def _normalized_auth_scopes(value: Any) -> list[str]:
    return sorted({normalized_control_api_auth_scope(item) for item in _normalized_items(value, CONTROL_API_AUTH_SCOPES)})


def _normalized_command_scope_requirements(value: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if isinstance(value, Mapping):
        for command_name, scope in value.items():
            normalized[normalized_control_command_name(command_name)] = normalized_control_api_auth_scope(
                scope,
                default="control_basic",
            )
    return normalized


def _normalized_available_modes(value: Any) -> list[int]:
    return sorted(non_negative_int(item) for item in _normalized_items(value, (0, 1, 2)))


def normalized_control_api_capabilities_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    read_auth_required, control_auth_required = _normalized_control_auth_flags(raw)
    return {
        "ok": _normalized_control_flag(raw, "ok", True),
        "api_version": normalized_control_api_version(raw.get("api_version")),
        "transport": normalized_control_api_transport(raw.get("transport")),
        "auth_required": bool(read_auth_required or control_auth_required),
        "read_auth_required": read_auth_required,
        "control_auth_required": control_auth_required,
        "localhost_only": _normalized_control_flag(raw, "localhost_only", True),
        "unix_socket_path": _normalized_text(raw.get("unix_socket_path")),
        "auth_header": _normalized_text(raw.get("auth_header"), "Authorization: Bearer <token>"),
        "auth_scopes": _normalized_auth_scopes(raw.get("auth_scopes")),
        "command_names": _normalized_command_names(raw.get("command_names")),
        "command_scope_requirements": _normalized_command_scope_requirements(raw.get("command_scope_requirements")),
        "command_sources": _normalized_command_sources(raw.get("command_sources")),
        "state_endpoints": _normalized_allowed_endpoints(raw.get("state_endpoints"), CONTROL_API_STATE_ENDPOINTS),
        "endpoints": _normalized_allowed_endpoints(raw.get("endpoints"), CONTROL_API_ENDPOINTS),
        "available_modes": _normalized_available_modes(raw.get("available_modes")),
        "supported_phase_selections": _normalized_phase_selections(raw.get("supported_phase_selections")),
        "features": _normalized_bool_mapping(raw.get("features")),
        "topology": _normalized_text_mapping(raw.get("topology")),
        "versioning": _normalized_control_versioning(raw),
    }


def normalized_control_api_command_response_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    command = raw.get("command")
    result = raw.get("result")
    error = raw.get("error")
    return {
        "ok": _normalized_control_flag(raw, "ok"),
        "detail": _normalized_text(raw.get("detail")),
        "replayed": _normalized_control_flag(raw, "replayed"),
        "command": normalized_control_command_fields(command) if isinstance(command, Mapping) else None,
        "result": normalized_control_result_fields(result) if isinstance(result, Mapping) else None,
        "error": normalized_control_api_error_fields(error) if isinstance(error, Mapping) else None,
    }


def normalized_control_api_event_kind(value: Any) -> str:
    kind = _normalized_text(value).lower()
    return kind if kind in CONTROL_API_EVENT_KINDS else "state"


def normalized_control_api_event_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    event_payload = raw.get("payload")
    normalized_payload: dict[str, Any] = {}
    if isinstance(event_payload, Mapping):
        for key, value in event_payload.items():
            normalized_payload[str(key)] = value
    seq = non_negative_int(raw.get("seq"))
    return {
        "seq": seq,
        "api_version": normalized_control_api_version(raw.get("api_version")),
        "kind": normalized_control_api_event_kind(raw.get("kind")),
        "timestamp": non_negative_float_or_none(raw.get("timestamp")) or 0.0,
        "resume_token": _normalized_text(raw.get("resume_token"), str(seq)),
        "payload": normalized_payload,
    }
