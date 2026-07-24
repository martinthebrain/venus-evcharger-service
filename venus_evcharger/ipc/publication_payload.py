# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, lossless field payloads for semantic gateway publication."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TypeGuard

from venus_evcharger.core.shared import compact_json
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    publication_field_orders,
)

MAX_PUBLICATION_COALESCE_KEY_BYTES = 512
MAX_PUBLICATION_FIELD_NAME_BYTES = 256
MAX_PUBLICATION_FIELDS_PER_KEY = 256
MAX_PUBLICATION_PAYLOAD_BYTES = 48 * 1024


def publication_fields(command: CommandMapping | None) -> dict[str, object]:
    if command is None:
        return {}
    return _string_mapping(command.get("fields")) or {}


def publication_payload_limit_reason(command: CommandMapping) -> str:
    """Return the first violated bounded-publication contract."""
    fields = publication_fields(command)
    reasons = (
        _coalesce_key_limit_reason(command),
        _field_shape_limit_reason(fields),
        _serialized_payload_limit_reason(command),
    )
    for reason in reasons:
        if reason:
            return reason
    return ""


def _coalesce_key_limit_reason(command: CommandMapping) -> str:
    key = str(command.get("coalesce_key") or "")
    too_large = len(key.encode("utf-8")) > MAX_PUBLICATION_COALESCE_KEY_BYTES
    return "coalesce-key-too-large" if too_large else ""


def _field_shape_limit_reason(fields: Mapping[str, object]) -> str:
    if len(fields) > MAX_PUBLICATION_FIELDS_PER_KEY:
        return "field-limit"
    names_too_large = any(
        len(field.encode("utf-8")) > MAX_PUBLICATION_FIELD_NAME_BYTES
        for field in fields
    )
    return "field-name-too-large" if names_too_large else ""


def _serialized_payload_limit_reason(command: CommandMapping) -> str:
    try:
        payload_size = len(compact_json(dict(command)).encode("utf-8"))
    except (TypeError, ValueError):
        return "payload-not-json"
    return "payload-limit" if payload_size > MAX_PUBLICATION_PAYLOAD_BYTES else ""


def merge_publication_payload(
    existing: CommandMapping | None,
    candidate: CommandMapping,
    *,
    accepted_fields: Collection[str] | None = None,
) -> CommandPayload:
    """Merge fields by their individual order while keeping candidate metadata."""
    payload = dict(existing or {})
    payload.update(candidate)
    existing_fields = publication_fields(existing)
    candidate_fields = _accepted_candidate_fields(candidate, accepted_fields)
    existing_orders = publication_field_orders(existing or {})
    candidate_orders = publication_field_orders(candidate)
    merged_fields, merged_orders = _merge_ordered_fields(
        existing_fields,
        candidate_fields,
        existing_orders,
        candidate_orders,
    )
    payload["fields"] = merged_fields
    _set_field_orders(payload, merged_orders)
    return payload


def filter_publication_payload(
    command: CommandMapping,
    fields: Collection[str],
) -> CommandPayload | None:
    """Return a publication containing exactly the requested existing fields."""
    values = _selected_field_values(command, fields)
    if not values:
        return None
    payload = dict(command)
    payload["fields"] = values
    orders = publication_field_orders(command)
    selected_orders = {field: orders.get(field, 0) for field in values}
    _set_field_orders(payload, selected_orders)
    return payload


def _accepted_candidate_fields(
    candidate: CommandMapping,
    accepted_fields: Collection[str] | None,
) -> dict[str, object]:
    fields = publication_fields(candidate)
    if accepted_fields is None:
        return fields
    allowed = frozenset(accepted_fields)
    return {field: value for field, value in fields.items() if field in allowed}


def _merge_ordered_fields(
    existing_fields: Mapping[str, object],
    candidate_fields: Mapping[str, object],
    existing_orders: Mapping[str, int],
    candidate_orders: Mapping[str, int],
) -> tuple[dict[str, object], dict[str, int]]:
    merged_fields = dict(existing_fields)
    merged_orders = dict(existing_orders)
    for field, value in candidate_fields.items():
        _merge_candidate_field(
            field,
            value,
            candidate_orders.get(field, 0),
            merged_fields,
            merged_orders,
        )
    return merged_fields, merged_orders


def _merge_candidate_field(
    field: str,
    value: object,
    order: int,
    merged_fields: dict[str, object],
    merged_orders: dict[str, int],
) -> None:
    missing = field not in merged_fields
    newer = order >= merged_orders.get(field, 0)
    if missing or newer:
        merged_fields[field] = value
        merged_orders[field] = order


def _selected_field_values(
    command: CommandMapping,
    fields: Collection[str],
) -> dict[str, object]:
    selected = frozenset(fields)
    return {
        field: value
        for field, value in publication_fields(command).items()
        if field in selected
    }


def _set_field_orders(payload: CommandPayload, orders: dict[str, int]) -> None:
    if any(orders.values()):
        payload[PUBLICATION_FIELD_ORDERS_FIELD] = orders
        return
    payload.pop(PUBLICATION_FIELD_ORDERS_FIELD, None)


def _string_mapping(value: object) -> dict[str, object] | None:
    if not _is_mapping(value):
        return None
    if not all(isinstance(key, str) for key in value):
        return None
    return {str(key): item for key, item in value.items()}


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


__all__ = [
    "MAX_PUBLICATION_COALESCE_KEY_BYTES",
    "MAX_PUBLICATION_FIELD_NAME_BYTES",
    "MAX_PUBLICATION_FIELDS_PER_KEY",
    "MAX_PUBLICATION_PAYLOAD_BYTES",
    "filter_publication_payload",
    "merge_publication_payload",
    "publication_fields",
    "publication_payload_limit_reason",
]
