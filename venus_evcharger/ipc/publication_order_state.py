# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded runtime-state persistence for publication high-water marks."""

from __future__ import annotations

import json
import math
import os
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeGuard

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.ipc.command_types import CommandPayload

PublicationLane = Literal["fast", "durable"]
PublicationFieldKey = tuple[str, str]
_STATE_SCHEMA_VERSION = 1
_STATE_JOURNAL_SUFFIX = ".journal"
_STATE_JOURNAL_COMPACT_BYTES = 64 * 1024
_STATE_JOURNAL_LINE_BYTES = 64 * 1024


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
) -> OrderedDict[PublicationFieldKey, PublicationOrderMark]:
    records = _state_records(path) or []
    marks: OrderedDict[PublicationFieldKey, PublicationOrderMark] = OrderedDict()
    cutoff = now - retention_seconds
    for value in records:
        _insert_state_mark(marks, value, now=now, cutoff=cutoff)
    for value in _journal_records(path):
        _insert_state_mark(marks, value, now=now, cutoff=cutoff)
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


def checkpoint_publication_order_marks(
    path: str,
    changed: Mapping[PublicationFieldKey, PublicationOrderMark],
    current: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    """Append a bounded delta and occasionally compact it into the base state."""
    if not path or not changed:
        return True
    return _write_checkpoint(path, changed, current)


def _write_checkpoint(
    path: str,
    changed: Mapping[PublicationFieldKey, PublicationOrderMark],
    current: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    if Path(path).is_dir():
        return False
    if not _prepare_journal_for_append(path, current):
        return False
    if not _append_state_delta(path, changed):
        return False
    return _finish_checkpoint(path, current)


def _prepare_journal_for_append(
    path: str,
    current: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    journal_path = _journal_path(path)
    if not Path(journal_path).exists():
        return True
    journal_size = _journal_size(journal_path)
    if journal_size is None:
        return False
    if journal_size <= _STATE_JOURNAL_COMPACT_BYTES:
        return True
    return _compact_state(path, journal_path, current)


def _finish_checkpoint(
    path: str,
    current: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    journal_path = _journal_path(path)
    journal_size = _journal_size(journal_path)
    if journal_size is None:
        return False
    if journal_size <= _STATE_JOURNAL_COMPACT_BYTES:
        return True
    return _compact_state(path, journal_path, current)


def _journal_size(journal_path: str) -> int | None:
    try:
        return os.path.getsize(journal_path)
    except OSError:
        return None


def _compact_state(
    path: str,
    journal_path: str,
    current: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    if not persist_publication_order_marks(path, current):
        return False
    return _remove_compacted_journal(journal_path)


def _remove_compacted_journal(journal_path: str) -> bool:
    try:
        os.unlink(journal_path)
    except FileNotFoundError:
        return True
    except OSError:
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
            if mark.lane == "durable"
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


def _journal_records(path: str) -> list[object]:
    records: list[object] = []
    try:
        with open(_journal_path(path), encoding="utf-8") as handle:
            while line := handle.readline(_STATE_JOURNAL_LINE_BYTES + 1):
                if len(line.encode("utf-8")) > _STATE_JOURNAL_LINE_BYTES:
                    continue
                payload = _journal_payload(line)
                if payload is not None:
                    records.extend(payload)
    except (OSError, UnicodeDecodeError):
        return []
    return records


def _journal_payload(line: str) -> list[object] | None:
    try:
        loaded: object = json.loads(line)
    except json.JSONDecodeError:
        return None
    payload = _string_mapping(loaded)
    if payload is None or payload.get("schema_version") != _STATE_SCHEMA_VERSION:
        return None
    marks = payload.get("marks")
    return marks if _is_object_list(marks) else None


def _append_state_delta(
    path: str,
    marks: Mapping[PublicationFieldKey, PublicationOrderMark],
) -> bool:
    line = compact_json(_state_payload(marks)) + "\n"
    if len(line.encode("utf-8")) > _STATE_JOURNAL_LINE_BYTES:
        return False
    try:
        with open(_journal_path(path), "a", encoding="utf-8") as handle:
            handle.write(line)
    except (OSError, RuntimeError, TypeError, UnicodeEncodeError, ValueError):
        return False
    return True


def _journal_path(path: str) -> str:
    return f"{path}{_STATE_JOURNAL_SUFFIX}"


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
    if order is None or lane != "durable" or seen_at is None:
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
) -> None:
    parsed = _state_mark(value, now=now, cutoff=cutoff)
    if parsed is None:
        return
    field_key, mark = parsed
    marks[field_key] = mark


def _nonempty_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _publication_lane(value: object) -> PublicationLane | None:
    return "durable" if value == "durable" else None


def _string_mapping(value: object) -> dict[str, object] | None:
    if not _is_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _positive_integer(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


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
    "checkpoint_publication_order_marks",
    "load_publication_order_marks",
    "persist_publication_order_marks",
]
