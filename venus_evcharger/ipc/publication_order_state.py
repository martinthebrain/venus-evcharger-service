# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded runtime-state persistence for publication high-water marks."""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.ipc.command_types import CommandPayload

PublicationLane = Literal["fast", "durable"]
PublicationFieldKey = tuple[str, str]
_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PublicationOrderMark:
    """One field's latest accepted transport order."""

    order: int
    lane: PublicationLane
    seen_at: float


def load_publication_order_marks(
    path: str,
    *,
    now: float,
    retention_seconds: float,
    capacity: int,
) -> OrderedDict[PublicationFieldKey, PublicationOrderMark]:
    records = _state_records(path)
    if records is None:
        return OrderedDict()
    marks: OrderedDict[PublicationFieldKey, PublicationOrderMark] = OrderedDict()
    cutoff = now - retention_seconds
    for value in records:
        inserted = _insert_state_mark(marks, value, now=now, cutoff=cutoff)
        if inserted and len(marks) >= capacity:
            break
    return marks


def persist_publication_order_marks(
    path: str,
    marks: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    if not path:
        return True
    try:
        write_text_atomically(path, compact_json(_state_payload(marks)) + "\n")
    except (OSError, RuntimeError, TypeError, UnicodeEncodeError, ValueError):
        return False
    return True


def _state_payload(
    marks: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> CommandPayload:
    return {
        "schema_version": _STATE_SCHEMA_VERSION,
        "marks": [
            {
                "key": key,
                "field": field,
                "order": mark.order,
                "lane": mark.lane,
                "seen_at": mark.seen_at,
            }
            for (key, field), mark in marks.items()
        ],
    }


def _load_state_payload(path: str) -> CommandPayload | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            value: object = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    payload = _string_mapping(value)
    if payload is None or payload.get("schema_version") != _STATE_SCHEMA_VERSION:
        return None
    return payload


def _state_records(path: str) -> list[object] | None:
    payload = _load_state_payload(path)
    if payload is None:
        return None
    records = payload.get("marks")
    return records if _is_object_list(records) else None


def _state_mark(
    value: object,
    *,
    now: float,
    cutoff: float,
) -> tuple[PublicationFieldKey, PublicationOrderMark] | None:
    payload = _string_mapping(value)
    if payload is None:
        return None
    identity = _state_identity(payload)
    mark = _state_order_mark(payload, now=now, cutoff=cutoff)
    if identity is None or mark is None:
        return None
    return identity, mark


def _state_identity(payload: Mapping[str, object]) -> PublicationFieldKey | None:
    key = _nonempty_text(payload.get("key"))
    field = _nonempty_text(payload.get("field"))
    if key is None or field is None:
        return None
    return key, field


def _state_order_mark(
    payload: Mapping[str, object],
    *,
    now: float,
    cutoff: float,
) -> PublicationOrderMark | None:
    order = _positive_integer(payload.get("order"))
    lane = _publication_lane(payload.get("lane"))
    seen_at = _finite_float(payload.get("seen_at"))
    if order == 0 or lane is None or seen_at is None:
        return None
    if not cutoff < seen_at <= now:
        return None
    return PublicationOrderMark(order, lane, seen_at)


def _insert_state_mark(
    marks: OrderedDict[PublicationFieldKey, PublicationOrderMark],
    value: object,
    *,
    now: float,
    cutoff: float,
) -> bool:
    parsed = _state_mark(value, now=now, cutoff=cutoff)
    if parsed is None:
        return False
    field_key, mark = parsed
    marks[field_key] = mark
    return True


def _nonempty_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _publication_lane(value: object) -> PublicationLane | None:
    if value == "fast":
        return "fast"
    if value == "durable":
        return "durable"
    return None


def _string_mapping(value: object) -> dict[str, object] | None:
    if not _is_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _positive_integer(value: object) -> int:
    return value if type(value) is int and value > 0 else 0


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (str, bytes, int, float)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


__all__ = [
    "PublicationFieldKey",
    "PublicationLane",
    "PublicationOrderMark",
    "load_publication_order_marks",
    "persist_publication_order_marks",
]
