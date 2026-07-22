# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated types for the asynchronous control-command queue."""

from __future__ import annotations

from collections import OrderedDict
from typing import TypeAlias, TypeGuard

from venus_evcharger.control import ControlCommand

QueuedControlCommand = tuple[int, float, ControlCommand]
ControlCommandQueue: TypeAlias = OrderedDict[str, QueuedControlCommand]
_QUEUED_CONTROL_COMMAND_SIZE = 3


def require_control_command_queue(value: object, name: str) -> ControlCommandQueue:
    if is_control_command_queue(value, name):
        return value
    raise TypeError(f"{name} must be OrderedDict, got {type(value).__name__}")


def is_control_command_queue(value: object, name: str = "control command queue") -> TypeGuard[ControlCommandQueue]:
    if not isinstance(value, OrderedDict):
        return False
    _validate_control_command_queue(value, name)
    return True


def _validate_control_command_queue(value: OrderedDict[object, object], name: str) -> None:
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{name} keys must be str, got {type(key).__name__}")
        _require_control_command_value(item, name)


def _require_control_command_value(value: object, name: str) -> None:
    sequence, queued_at, command = _control_command_tuple(value, name)
    _require_sequence(sequence, name)
    _require_queued_at(queued_at, name)
    _require_control_command(command, name)


def _control_command_tuple(value: object, name: str) -> tuple[object, object, object]:
    if not isinstance(value, tuple) or len(value) != _QUEUED_CONTROL_COMMAND_SIZE:
        raise TypeError(f"{name} values must be control command tuples")
    return value


def _require_sequence(sequence: object, name: str) -> None:
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError(f"{name} sequence must be int")


def _require_queued_at(queued_at: object, name: str) -> None:
    if isinstance(queued_at, bool) or not isinstance(queued_at, int | float):
        raise TypeError(f"{name} queued_at must be float")


def _require_control_command(command: object, name: str) -> None:
    if not isinstance(command, ControlCommand):
        raise TypeError(f"{name} command must be ControlCommand")


__all__ = ["ControlCommandQueue", "QueuedControlCommand", "require_control_command_queue"]
