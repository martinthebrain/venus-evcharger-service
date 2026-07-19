# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed component contracts for volatile service-state management."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeGuard


ModeNormalizer = Callable[[object], int]
_MISSING = object()


def is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    """Narrow an untyped runtime value to a dictionary with explicit object types."""
    return isinstance(value, dict)


def is_object_list(value: object) -> TypeGuard[list[object]]:
    """Narrow an untyped runtime value to a list with explicit object items."""
    return isinstance(value, list)


def string_object_mapping(value: object) -> dict[str, object] | None:
    """Copy a dictionary whose keys are all strings into a typed mapping."""
    if not is_object_dict(value):
        return None
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        normalized[key] = item
    return normalized


def string_key_items(value: object) -> dict[str, object]:
    """Copy the string-keyed portion of a dynamic dictionary."""
    if not is_object_dict(value):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def string_value_mapping(value: object) -> dict[str, str] | None:
    """Copy a dictionary containing only string keys and string values."""
    mapping = string_object_mapping(value)
    if mapping is None:
        return None
    normalized: dict[str, str] = {}
    for key, item in mapping.items():
        if not isinstance(item, str):
            return None
        normalized[key] = item
    return normalized


@dataclass(frozen=True, slots=True)
class StateAttributes:
    """Typed access boundary around the service's dynamic runtime attributes."""

    target: object

    def has(self, name: str) -> bool:
        return hasattr(self.target, name)

    def get(self, name: str, default: object = _MISSING) -> object:
        if default is _MISSING:
            return getattr(self.target, name)
        return getattr(self.target, name, default)

    def set(self, name: str, value: object) -> None:
        setattr(self.target, name, value)


if TYPE_CHECKING:

    class StateConfigPort(Protocol):
        def load(self) -> configparser.ConfigParser: ...

    class StateValidationPort(Protocol):
        def validate(self) -> None: ...

    class RuntimeSnapshotPort(Protocol):
        def build(self) -> dict[str, object]: ...

    class RuntimeRestorePort(Protocol):
        def restore(self, state: dict[str, object], current_time: float) -> None: ...

    class RuntimeOverridePort(Protocol):
        def apply_to_config(self, config: configparser.ConfigParser) -> configparser.ConfigParser: ...

        def current(self) -> dict[str, str]: ...

        def save(self) -> None: ...

        def flush(self, now: float | None = None) -> None: ...

    class RuntimePersistencePort(Protocol):
        def load(self) -> None: ...

        def save(self) -> None: ...

    class StateSummaryPort(Protocol):
        def build(self) -> str: ...


@dataclass(frozen=True)
class StateControllerComponents:
    """Complete, immutable component graph owned by the state facade."""

    config: StateConfigPort
    validation: StateValidationPort
    snapshot: RuntimeSnapshotPort
    overrides: RuntimeOverridePort
    persistence: RuntimePersistencePort
    summary: StateSummaryPort
