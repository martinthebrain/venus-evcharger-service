# SPDX-License-Identifier: GPL-3.0-or-later
"""Type aliases for async runtime queues."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, TypeAlias, cast

from venus_evcharger.control import ControlCommand

QueuedPublishValue = tuple[Any, float, float]
QueuedControlCommand = tuple[int, float, ControlCommand]
PublishQueue: TypeAlias = OrderedDict[str, QueuedPublishValue]
ControlCommandQueue: TypeAlias = OrderedDict[str, QueuedControlCommand]


def require_publish_queue(value: object, name: str) -> PublishQueue:
    if not isinstance(value, OrderedDict):
        raise TypeError(f"{name} must be OrderedDict, got {type(value).__name__}")
    for key, item in value.items():
        _require_str_key(key, name)
        _require_publish_value(item, name)
    return cast(PublishQueue, value)


def require_control_command_queue(value: object, name: str) -> ControlCommandQueue:
    if not isinstance(value, OrderedDict):
        raise TypeError(f"{name} must be OrderedDict, got {type(value).__name__}")
    for key, item in value.items():
        _require_str_key(key, name)
        _require_control_command_value(item, name)
    return cast(ControlCommandQueue, value)


def _require_str_key(key: object, name: str) -> None:
    if not isinstance(key, str):
        raise TypeError(f"{name} keys must be str, got {type(key).__name__}")


def _require_publish_value(value: object, name: str) -> None:
    if not isinstance(value, tuple) or len(value) != 3:
        raise TypeError(f"{name} values must be publish tuples")
    _payload, current, queued_at = value
    _require_float(current, f"{name} current")
    _require_float(queued_at, f"{name} queued_at")


def _require_control_command_value(value: object, name: str) -> None:
    if not isinstance(value, tuple) or len(value) != 3:
        raise TypeError(f"{name} values must be control command tuples")
    sequence, queued_at, command = value
    _require_control_sequence(sequence, name)
    _require_float(queued_at, f"{name} queued_at")
    _require_control_command(command, name)


def _require_control_sequence(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} sequence must be int")


def _require_control_command(value: object, name: str) -> None:
    if not isinstance(value, ControlCommand):
        raise TypeError(f"{name} command must be ControlCommand")


def _require_float(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be float")
