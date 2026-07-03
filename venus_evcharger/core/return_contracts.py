# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime return-value contracts shared across service, ports, and controllers."""

from __future__ import annotations

from typing import Any, TypeVar


T = TypeVar("T")


def require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must return bool, got {type(value).__name__}")
    return value


def require_none(value: object, name: str) -> None:
    if value is not None:
        raise TypeError(f"{name} must return None, got {type(value).__name__}")
    return None


def require_int(value: object, name: str) -> int:
    if not isinstance(value, int):
        raise TypeError(f"{name} must return int, got {type(value).__name__}")
    if isinstance(value, bool):
        raise TypeError(f"{name} must return int, got bool")
    return value


def require_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must return float, got {type(value).__name__}")
    return float(value)


def require_float_or_none(value: object, name: str) -> float | None:
    if value is None:
        return None
    return require_float(value, name)


def require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must return str, got {type(value).__name__}")
    return value


def require_str_or_none(value: object, name: str) -> str | None:
    if value is None:
        return None
    return require_str(value, name)


def require_str_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must return list, got {type(value).__name__}")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} must return list[str]")
    return value


def require_dict(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must return dict, got {type(value).__name__}")
    return value


def require_instance(value: object, name: str, expected_type: type[T]) -> T:
    if not isinstance(value, expected_type):
        raise TypeError(f"{name} must return {expected_type.__name__}, got {type(value).__name__}")
    return value


def _require_tuple_length(value: object, name: str, length: int) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must return tuple, got {type(value).__name__}")
    if len(value) != length:
        raise TypeError(f"{name} must return tuple length {length}, got {len(value)}")
    return value


def require_tuple2(value: object, name: str) -> tuple[Any, Any]:
    checked = _require_tuple_length(value, name, 2)
    return checked[0], checked[1]


def require_tuple3(value: object, name: str) -> tuple[Any, Any, Any]:
    checked = _require_tuple_length(value, name, 3)
    return checked[0], checked[1], checked[2]


def require_tuple4(value: object, name: str) -> tuple[Any, Any, Any, Any]:
    checked = _require_tuple_length(value, name, 4)
    return checked[0], checked[1], checked[2], checked[3]


def require_tuple5(value: object, name: str) -> tuple[Any, Any, Any, Any, Any]:
    checked = _require_tuple_length(value, name, 5)
    return checked[0], checked[1], checked[2], checked[3], checked[4]
