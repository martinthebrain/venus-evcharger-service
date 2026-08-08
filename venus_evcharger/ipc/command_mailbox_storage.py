# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON and filesystem primitives for durable command mailboxes."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import TypeGuard, TypeVar

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload


def normalized_mapping(value: object) -> CommandPayload | None:
    """Return a string-keyed command payload when value is a mapping."""
    if not _is_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def read_command_json(path: str) -> object:
    """Read a command payload, returning ``None`` for unavailable JSON."""
    try:
        return read_command_json_strict(path)
    except (OSError, json.JSONDecodeError):
        return None


def read_command_json_strict(path: str) -> object:
    """Read one command payload while preserving filesystem and JSON errors."""
    with open(path, encoding="utf-8") as handle:
        payload: object = json.load(handle)
    return payload


def command_file_signature(path: str) -> tuple[int, int, int] | None:
    """Return the inode, size, and generation timestamp of a command file."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_ino, status.st_size, status.st_mtime_ns


def write_command_json(path: str, payload: CommandMapping) -> None:
    """Atomically write one compact, JSON-safe command payload."""
    write_text_atomically(path, compact_json(_json_ready_mapping(payload)) + "\n")


def now_epoch() -> float:
    """Return the epoch timestamp used for durable command metadata."""
    return time.time()


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _json_ready(value: object) -> object:
    if _is_json_scalar(value):
        return value
    return _json_ready_container(value)


def _json_ready_container(value: object) -> object:
    if _is_mapping(value):
        return _json_ready_mapping(value)
    if _is_object_list(value) or _is_object_tuple(value):
        return [_json_ready(item) for item in value]
    return str(value)


def _is_json_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


_MappingKey = TypeVar("_MappingKey")


def _json_ready_mapping(value: Mapping[_MappingKey, object]) -> CommandPayload:
    return {str(key): _json_ready(item) for key, item in value.items()}


__all__ = [
    "command_file_signature",
    "normalized_mapping",
    "now_epoch",
    "read_command_json",
    "read_command_json_strict",
    "write_command_json",
]
