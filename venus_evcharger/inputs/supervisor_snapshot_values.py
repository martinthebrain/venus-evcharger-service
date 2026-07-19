# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize untrusted auto-input snapshot values at the JSON boundary."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TypeAlias, TypeGuard

from venus_evcharger.inputs.supervisor_contracts import SnapshotPayload

NumericInput: TypeAlias = str | bytes | bytearray | int | float
NUMERIC_INPUT_TYPES = (str, bytes, bytearray, int, float)


def _is_numeric_input(value: object) -> TypeGuard[NumericInput]:
    return isinstance(value, NUMERIC_INPUT_TYPES)


def _finite_float(value: NumericInput) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def snapshot_timestamp(value: object) -> float | None:
    """Return one finite numeric timestamp from an untrusted JSON value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if not _is_numeric_input(value):
        return None
    return _finite_float(value)


def snapshot_number(value: object) -> float | None:
    """Normalize one optional finite numeric snapshot field."""
    return snapshot_timestamp(value)


def snapshot_int(value: object) -> int | None:
    """Return one integer from an untrusted scalar snapshot value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if not _is_numeric_input(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Narrow one untrusted JSON value to an explicitly typed mapping."""
    return isinstance(value, Mapping)


def is_object_list(value: object) -> TypeGuard[list[object]]:
    """Narrow one untrusted JSON value to an explicitly typed list."""
    return isinstance(value, list)


def snapshot_payload(value: object) -> SnapshotPayload | None:
    """Normalize a JSON object to the snapshot string-key contract."""
    if not is_object_mapping(value):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def copied_object_mapping(value: object) -> SnapshotPayload | None:
    """Copy one nested JSON object while normalizing its keys."""
    if not is_object_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}
