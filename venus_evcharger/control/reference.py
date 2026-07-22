# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared human-facing command reference for Control API v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ControlApiCommandReference:
    """One human-facing command summary kept in sync with the API contract."""

    name: str
    idempotent_shape: str
    accepted_in_flight: str
    typical_restrictions: str


CONTROL_API_COMMAND_SCOPE_REQUIREMENTS: dict[str, str] = {
    "reset_contactor_lockout": "control_admin",
    "reset_phase_lockout": "control_admin",
    "set_auto_runtime_setting": "control_admin",
    "set_auto_start": "control_basic",
    "set_current_setting": "control_basic",
    "set_enable": "control_basic",
    "set_mode": "control_basic",
    "set_phase_selection": "control_basic",
    "set_start_stop": "control_basic",
    "trigger_software_update": "update_admin",
}

CONTROL_API_COMMAND_REFERENCE: tuple[ControlApiCommandReference, ...] = (
    ControlApiCommandReference(
        name="set_mode",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        typical_restrictions="mode-specific runtime rules",
    ),
    ControlApiCommandReference(
        name="set_auto_start",
        idempotent_shape="yes",
        accepted_in_flight="uncommon",
        typical_restrictions="none beyond local policy",
    ),
    ControlApiCommandReference(
        name="set_start_stop",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        typical_restrictions="mode/backend policy",
    ),
    ControlApiCommandReference(
        name="set_enable",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        typical_restrictions="backend/health policy",
    ),
    ControlApiCommandReference(
        name="set_current_setting",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        typical_restrictions="backend/current limits",
    ),
    ControlApiCommandReference(
        name="set_phase_selection",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        typical_restrictions="supported topology and phase hardware",
    ),
    ControlApiCommandReference(
        name="set_auto_runtime_setting",
        idempotent_shape="yes",
        accepted_in_flight="uncommon",
        typical_restrictions="only supported runtime-setting paths",
    ),
    ControlApiCommandReference(
        name="reset_phase_lockout",
        idempotent_shape="no",
        accepted_in_flight="uncommon",
        typical_restrictions="only meaningful when lockout exists",
    ),
    ControlApiCommandReference(
        name="reset_contactor_lockout",
        idempotent_shape="no",
        accepted_in_flight="uncommon",
        typical_restrictions="only meaningful when lockout exists",
    ),
    ControlApiCommandReference(
        name="trigger_software_update",
        idempotent_shape="no",
        accepted_in_flight="possible",
        typical_restrictions="update policy, availability, current update state",
    ),
)

CONTROL_API_COMMAND_REFERENCE_BY_NAME = {
    item.name: item for item in CONTROL_API_COMMAND_REFERENCE
}


_PREFERRED_FIELD_ORDER = ("name", "target", "value", "detail", "command_id", "idempotency_key")
_VALUE_TYPE_ORDER = {
    "boolean or `0/1`": 0,
    "integer": 1,
    "number": 2,
    "string": 3,
    "implementation-defined": 4,
}


def _control_api_component_schemas() -> Mapping[str, Any]:
    from venus_evcharger.control.openapi import build_control_api_openapi_spec

    spec = build_control_api_openapi_spec()
    components = spec.get("components")
    if not isinstance(components, Mapping):
        raise TypeError("Control API OpenAPI spec must contain components mapping")
    schemas = components.get("schemas")
    if not isinstance(schemas, Mapping):
        raise TypeError("Control API OpenAPI components must contain schemas mapping")
    return schemas


def _named_schema_command_name(schema: Any) -> str | None:
    if not isinstance(schema, Mapping):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    name_property = properties.get("name")
    if not isinstance(name_property, Mapping):
        return None
    raw_name = name_property.get("const")
    return raw_name if isinstance(raw_name, str) else None


def _named_request_schemas_by_command() -> dict[str, list[Mapping[str, Any]]]:
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for raw_schema in _control_api_component_schemas().values():
        command_name = _named_schema_command_name(raw_schema)
        if command_name is None or not isinstance(raw_schema, Mapping):
            continue
        by_name.setdefault(command_name, []).append(raw_schema)
    return by_name


def _sorted_required_fields(required_fields: set[str]) -> tuple[str, ...]:
    ordered = [field for field in _PREFERRED_FIELD_ORDER if field in required_fields]
    ordered.extend(sorted(required_fields.difference(_PREFERRED_FIELD_ORDER)))
    return tuple(ordered)


def _is_binary_schema(schema: Mapping[str, Any]) -> bool:
    raw_one_of = schema.get("oneOf")
    if not isinstance(raw_one_of, list) or len(raw_one_of) != 2:
        return False
    normalized = {_binary_variant_shape(item) for item in raw_one_of if isinstance(item, Mapping)}
    return normalized == {("boolean", ()), ("integer", (0, 1))}


def _binary_variant_shape(schema: Mapping[str, Any]) -> tuple[Any, tuple[Any, ...]]:
    raw_enum = schema.get("enum")
    enum_values = tuple(raw_enum) if isinstance(raw_enum, list) else ()
    return schema.get("type"), enum_values


def _schema_value_type(schema: Mapping[str, Any]) -> str:
    if _is_binary_schema(schema):
        return "boolean or `0/1`"
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    return "implementation-defined"


def _format_scalar(value: Any) -> str:
    if isinstance(value, str):
        scalar = value
    elif isinstance(value, bool):
        scalar = "1" if value else "0"
    else:
        scalar = _format_numeric_scalar(value)
    return f"`{scalar}`"


def _format_numeric_scalar(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _schema_allowed_values(schema: Mapping[str, Any]) -> str:
    if _is_binary_schema(schema):
        return "binary"
    enum_values = _enum_label(schema)
    if enum_values is not None:
        return enum_values
    const_value = _const_label(schema)
    if const_value is not None:
        return const_value
    minimum_label = _minimum_label(schema)
    return minimum_label if minimum_label is not None else "implementation-defined"


def _enum_label(schema: Mapping[str, Any]) -> str | None:
    raw_enum = schema.get("enum")
    if not isinstance(raw_enum, list) or not raw_enum:
        return None
    return ", ".join(_format_scalar(item) for item in raw_enum)


def _const_label(schema: Mapping[str, Any]) -> str | None:
    raw_const = schema.get("const")
    if raw_const in (None, ""):
        return None
    return _format_scalar(raw_const)


def _minimum_label(schema: Mapping[str, Any]) -> str | None:
    minimum = schema.get("minimum")
    if not isinstance(minimum, (int, float)):
        return None
    minimum_scalar = _format_numeric_scalar(minimum)
    return f"`>= {minimum_scalar}`"


def _joined_labels(labels: set[str], *, target_specific: bool) -> str:
    ordered = sorted(labels, key=lambda item: (_VALUE_TYPE_ORDER.get(item, 99), item))
    if len(ordered) == 1:
        return ordered[0]
    if len(ordered) == 2:
        joined = f"{ordered[0]} or {ordered[1]}"
    else:
        joined = f"{', '.join(ordered[:-1])}, or {ordered[-1]}"
    if target_specific:
        return f"{joined} depending on `target`"
    return joined


def _command_contract_summary(name: str) -> tuple[tuple[str, ...], str, str]:
    schemas = _named_request_schemas_by_command()[name]
    required_fields = _collected_required_fields(schemas)
    value_types, allowed_values = _collected_value_contract_labels(schemas)
    target_specific = len(schemas) > 1
    value_type = _joined_labels(value_types, target_specific=target_specific)
    allowed = allowed_values.pop() if len(allowed_values) == 1 else "target-specific schema"
    return _sorted_required_fields(required_fields), value_type, allowed


def _collected_required_fields(schemas: list[Mapping[str, Any]]) -> set[str]:
    required_fields: set[str] = set()
    for schema in schemas:
        raw_required = schema.get("required")
        if isinstance(raw_required, list):
            required_fields.update(str(item) for item in raw_required)
    return required_fields


def _collected_value_contract_labels(schemas: list[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    value_types: set[str] = set()
    allowed_values: set[str] = set()
    for schema in schemas:
        value_schema = _schema_value_property(schema)
        if value_schema is None:
            continue
        value_types.add(_schema_value_type(value_schema))
        allowed_values.add(_schema_allowed_values(value_schema))
    return value_types, allowed_values


def _schema_value_property(schema: Mapping[str, Any]) -> Mapping[str, Any] | None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return None
    value_schema = properties.get("value", {})
    return value_schema if isinstance(value_schema, Mapping) else None


def render_control_api_command_matrix_markdown() -> str:
    """Render the shared command matrix from the OpenAPI contract."""
    header = [
        "| Command name | Required fields | Value type | Allowed values / ranges | Idempotent shape | `accepted_in_flight` | Required scope | Typical restrictions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = []
    for item in CONTROL_API_COMMAND_REFERENCE:
        required_fields, value_type, allowed_values = _command_contract_summary(item.name)
        rows.append(
            f"| `{item.name}` | "
            f"{', '.join(f'`{field}`' for field in required_fields)} | "
            f"{value_type} | "
            f"{allowed_values} | "
            f"{item.idempotent_shape} | "
            f"{item.accepted_in_flight} | "
            f"`{CONTROL_API_COMMAND_SCOPE_REQUIREMENTS[item.name]}` | "
            f"{item.typical_restrictions} |"
        )
    return "\n".join([*header, *rows])
