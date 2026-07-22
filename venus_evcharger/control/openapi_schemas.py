# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable schema components for the local OpenAPI document."""

from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.core.contracts import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_ERROR_CODES,
    CONTROL_API_EVENT_KINDS,
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_API_TRANSPORTS,
    CONTROL_API_VERSIONS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
    CONTROL_COMMAND_STATUSES,
    STATE_API_KINDS,
)
from venus_evcharger.core.contracts_control_surface import (
    CONTROL_BINARY_AUTO_RUNTIME_TARGETS,
    CONTROL_COMMAND_DEFAULT_TARGETS,
    CONTROL_CURRENT_SETTING_TARGETS,
    CONTROL_DIRECT_TARGET_COMMANDS,
    CONTROL_FLOAT_AUTO_RUNTIME_TARGETS,
    CONTROL_INTEGER_AUTO_RUNTIME_TARGETS,
    CONTROL_PHASE_SELECTIONS,
    CONTROL_STRING_AUTO_RUNTIME_TARGETS,
)

from .openapi_helpers import (
    _array_schema,
    _boolean_or_binary_integer_schema,
    _boolean_schema,
    _const_schema,
    _integer_schema,
    _number_schema,
    _object_schema,
    _ref,
    _string_schema,
)


def _tracking_properties() -> dict[str, Any]:
    return {
        "detail": _string_schema(),
        "command_id": _string_schema(),
        "idempotency_key": _string_schema(),
    }


def _tracking_headers() -> list[dict[str, Any]]:
    return [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": False,
            "schema": _string_schema(),
            "description": "Optional replay-safe key for command deduplication.",
        },
        {
            "name": "X-Command-Id",
            "in": "header",
            "required": False,
            "schema": _string_schema(),
            "description": "Optional client-supplied command identifier.",
        },
    ]


def _named_command_request_schema(
    name: str,
    value_schema: Mapping[str, Any],
    *,
    target_schema: Mapping[str, Any] | None = None,
    target_required: bool = False,
) -> dict[str, Any]:
    properties = {"name": _const_schema(name), "value": dict(value_schema), **_tracking_properties()}
    required = ["name", "value"]
    if target_schema is not None:
        properties["target"] = dict(target_schema)
    if target_required:
        required.append("target")
    return _object_schema(properties, required=required)


def _control_request_schemas() -> dict[str, Any]:
    direct_binary_target_names = {
        "auto_contactor_lockout_reset": "ResetContactorLockout",
        "auto_phase_lockout_reset": "ResetPhaseLockout",
        "auto_software_update_run": "TriggerSoftwareUpdate",
        "auto_start": "SetAutoStart",
        "enable": "SetEnable",
        "start_stop": "SetStartStop",
    }
    binary_schema = _boolean_or_binary_integer_schema()
    request_schemas: dict[str, Any] = {
        "SetModeCommandRequest": _named_command_request_schema(
            "set_mode",
            _integer_schema(enum=(0, 1, 2)),
            target_schema=_const_schema(CONTROL_COMMAND_DEFAULT_TARGETS["set_mode"]),
        ),
        "SetPhaseSelectionCommandRequest": _named_command_request_schema(
            "set_phase_selection",
            _string_schema(enum=sorted(CONTROL_PHASE_SELECTIONS)),
            target_schema=_const_schema(CONTROL_COMMAND_DEFAULT_TARGETS["set_phase_selection"]),
        ),
        "SetCurrentSettingCommandRequest": _named_command_request_schema(
        "set_current_setting",
        _number_schema(minimum=0.0),
        target_schema={"type": "string", "enum": sorted(CONTROL_CURRENT_SETTING_TARGETS)},
        target_required=True,
        ),
    }
    for target, schema_name in direct_binary_target_names.items():
        command_name = CONTROL_DIRECT_TARGET_COMMANDS[target]
        request_schemas[f"{schema_name}CommandRequest"] = _named_command_request_schema(
            command_name,
            binary_schema,
            target_schema=_const_schema(target),
        )
    request_schemas["SetAutoRuntimeFloatCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        _number_schema(minimum=0.0),
        target_schema={"type": "string", "enum": sorted(CONTROL_FLOAT_AUTO_RUNTIME_TARGETS)},
        target_required=True,
    )
    request_schemas["SetAutoRuntimeStringCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        _string_schema(),
        target_schema={"type": "string", "enum": sorted(CONTROL_STRING_AUTO_RUNTIME_TARGETS)},
        target_required=True,
    )
    request_schemas["SetAutoRuntimeBinaryCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        binary_schema,
        target_schema={"type": "string", "enum": sorted(CONTROL_BINARY_AUTO_RUNTIME_TARGETS)},
        target_required=True,
    )
    request_schemas["SetAutoRuntimeIntegerCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        _integer_schema(minimum=0),
        target_schema={"type": "string", "enum": sorted(CONTROL_INTEGER_AUTO_RUNTIME_TARGETS)},
        target_required=True,
    )
    return request_schemas


def _control_command_request_schema_names() -> list[str]:
    return sorted(_control_request_schemas())


def _component_schemas() -> dict[str, Any]:
    generic_state = {"type": "object", "additionalProperties": True}
    schemas: dict[str, Any] = {
        "ControlHealth": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "transport": _string_schema(enum=CONTROL_API_TRANSPORTS, default="http"),
                "listen_host": _string_schema(default="127.0.0.1"),
                "listen_port": _integer_schema(minimum=0),
                "auth_required": _boolean_schema(default=False),
                "read_auth_required": _boolean_schema(default=False),
                "control_auth_required": _boolean_schema(default=False),
                "localhost_only": _boolean_schema(default=True),
                "unix_socket_path": _string_schema(default=""),
            },
            required=(
                "ok","api_version","transport","listen_host","listen_port","auth_required",
                "read_auth_required","control_auth_required","localhost_only","unix_socket_path",
            ),
        ),
        "ControlError": _object_schema(
            {
                "code": _string_schema(enum=CONTROL_API_ERROR_CODES, default="bad_request"),
                "message": _string_schema(),
                "retryable": _boolean_schema(default=False),
                "details": {"type": "object", "additionalProperties": True},
            },
            required=("code", "message", "retryable", "details"),
        ),
        "ControlCommand": _object_schema(
            {
                "name": _string_schema(enum=CONTROL_COMMAND_NAMES),
                "target": _string_schema(),
                "value": {},
                "source": _string_schema(enum=CONTROL_COMMAND_SOURCES, default="http"),
                "detail": _string_schema(),
                "command_id": _string_schema(),
                "idempotency_key": _string_schema(),
            },
            required=("name", "target", "value", "source", "detail", "command_id", "idempotency_key"),
        ),
        "ControlResult": _object_schema(
            {
                "command": _ref("ControlCommand"),
                "status": _string_schema(enum=CONTROL_COMMAND_STATUSES, default="rejected"),
                "accepted": _boolean_schema(default=False),
                "applied": _boolean_schema(default=False),
                "persisted": _boolean_schema(default=False),
                "reversible_failure": _boolean_schema(default=True),
                "external_side_effect_started": _boolean_schema(default=False),
                "detail": _string_schema(),
            },
            required=(
                "command","status","accepted","applied","persisted","reversible_failure",
                "external_side_effect_started","detail",
            ),
        ),
        "ControlCommandResponse": _object_schema(
            {
                "ok": _boolean_schema(default=False),
                "detail": _string_schema(),
                "replayed": _boolean_schema(default=False),
                "command": {"oneOf": [_ref("ControlCommand"), {"type": "null"}]},
                "result": {"oneOf": [_ref("ControlResult"), {"type": "null"}]},
                "error": {"oneOf": [_ref("ControlError"), {"type": "null"}]},
            },
            required=("ok", "detail", "replayed", "command", "result", "error"),
        ),
        "ControlCommandRequest": {"oneOf": [_ref(name) for name in _control_command_request_schema_names()]},
        "ControlVersioning": _object_schema(
            {
                "stable_endpoints": _array_schema(_string_schema(enum=CONTROL_API_STABLE_ENDPOINTS)),
                "experimental_endpoints": _array_schema(_string_schema(enum=CONTROL_API_EXPERIMENTAL_ENDPOINTS)),
                "breaking_change_policy": _string_schema(),
            },
            required=("stable_endpoints", "experimental_endpoints", "breaking_change_policy"),
        ),
        "ControlCapabilities": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "transport": _string_schema(enum=CONTROL_API_TRANSPORTS, default="http"),
                "auth_required": _boolean_schema(default=False),
                "read_auth_required": _boolean_schema(default=False),
                "control_auth_required": _boolean_schema(default=False),
                "localhost_only": _boolean_schema(default=True),
                "unix_socket_path": _string_schema(default=""),
                "auth_header": _string_schema(default="Authorization: Bearer <token>"),
                "command_names": _array_schema(_string_schema(enum=CONTROL_COMMAND_NAMES)),
                "command_sources": _array_schema(_string_schema(enum=CONTROL_COMMAND_SOURCES)),
                "state_endpoints": _array_schema(_string_schema(enum=CONTROL_API_STATE_ENDPOINTS)),
                "endpoints": _array_schema(_string_schema(enum=CONTROL_API_ENDPOINTS)),
                "available_modes": _array_schema(_integer_schema(minimum=0)),
                "supported_phase_selections": _array_schema(_string_schema()),
                "features": {"type": "object", "additionalProperties": {"type": "boolean"}},
                "topology": {"type": "object", "additionalProperties": {"type": "string"}},
                "versioning": _ref("ControlVersioning"),
            },
            required=(
                "ok","api_version","transport","auth_required","read_auth_required","control_auth_required",
                "localhost_only","unix_socket_path","auth_header","command_names","command_sources",
                "state_endpoints","endpoints","available_modes","supported_phase_selections","features",
                "topology","versioning",
            ),
        ),
        "ControlEvent": _object_schema(
            {
                "seq": _integer_schema(minimum=0),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=CONTROL_API_EVENT_KINDS, default="state"),
                "timestamp": _number_schema(minimum=0.0),
                "resume_token": _string_schema(default="0"),
                "payload": generic_state,
            },
            required=("seq", "api_version", "kind", "timestamp", "resume_token", "payload"),
        ),
    }
    for schema_name, default_kind in (
        ("StateAutomation", "automation"),
        ("StateBuild", "build"),
        ("StateConfigEffective", "config-effective"),
        ("StateContracts", "contracts"),
        ("StateDbusDiagnostics", "dbus-diagnostics"),
        ("StateHealth", "health"),
        ("StateHealthz", "healthz"),
        ("StateOperational", "operational"),
        ("StateRuntime", "runtime"),
        ("StateSummary", "summary"),
        ("StateTopology", "topology"),
        ("StateUpdate", "update"),
        ("StateVersion", "version"),
        ("StateVictronBiasRecommendation", "victron-bias-recommendation"),
    ):
        schemas[schema_name] = (
            _object_schema(
                {
                    "ok": _boolean_schema(default=True),
                    "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                    "kind": _string_schema(enum=STATE_API_KINDS, default=default_kind),
                    "summary": _string_schema(),
                },
                required=("ok", "api_version", "kind", "summary"),
            )
            if default_kind == "summary"
            else _object_schema(
                {
                    "ok": _boolean_schema(default=True),
                    "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                    "kind": _string_schema(enum=STATE_API_KINDS, default=default_kind),
                    "state": generic_state,
                },
                required=("ok", "api_version", "kind", "state"),
            )
        )
    schemas.update(_control_request_schemas())
    return schemas
